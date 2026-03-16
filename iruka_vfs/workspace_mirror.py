from __future__ import annotations

from datetime import datetime
import hashlib
import json
import threading
import time
import traceback

import redis
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from iruka_vfs.constants import (
    MEMORY_CACHE_FLUSH_BATCH,
    MEMORY_CACHE_FLUSH_INTERVAL_SECONDS,
    VFS_CHECKPOINT_DEBOUNCE_SECONDS,
    VFS_CHECKPOINT_MAX_FAILURES,
    VFS_CHECKPOINT_RETRY_BASE_SECONDS,
    VFS_CHECKPOINT_RETRY_MAX_SECONDS,
)
from iruka_vfs.dependencies import get_vfs_dependencies
from iruka_vfs.file_sources import WritableFileSource
from iruka_vfs.models import WorkspaceMirror
from iruka_vfs.sqlalchemy_repositories import build_sqlalchemy_repositories
from iruka_vfs import runtime_state

_dependencies = get_vfs_dependencies()
_repositories = _dependencies.repositories or build_sqlalchemy_repositories(_dependencies)
settings = _dependencies.settings
AgentWorkspace = _dependencies.AgentWorkspace
Chapter = _dependencies.Chapter
VirtualFileNode = _dependencies.VirtualFileNode
VirtualShellSession = _dependencies.VirtualShellSession


def workspace_tenant_key(workspace: AgentWorkspace) -> str:
    metadata = dict(workspace.metadata_json or {})
    tenant_key = str(
        getattr(workspace, "tenant_id", "")
        or metadata.get("tenant_id")
        or metadata.get("tenant")
        or settings.default_tenant_id
    ).strip()
    return tenant_key or settings.default_tenant_id


def normalize_tenant_id(tenant_id: str | None) -> str:
    normalized = str(tenant_id or "").strip()
    return normalized or settings.default_tenant_id


def assert_workspace_tenant(workspace: AgentWorkspace, tenant_id: str | None) -> str:
    expected = workspace_tenant_key(workspace)
    requested = normalize_tenant_id(tenant_id)
    if expected != requested:
        raise PermissionError(f"tenant mismatch: workspace tenant is '{expected}', requested '{requested}'")
    return expected


def set_active_workspace_mirror(mirror: WorkspaceMirror | None) -> None:
    runtime_state.active_workspace_context.mirror = mirror


def set_active_workspace_tenant(tenant_key: str | None) -> None:
    runtime_state.active_workspace_context.tenant_key = tenant_key


def set_active_workspace_scope(scope_key: str | None) -> None:
    runtime_state.active_workspace_context.scope_key = scope_key


def active_workspace_mirror(workspace_id: int | None = None) -> WorkspaceMirror | None:
    mirror = getattr(runtime_state.active_workspace_context, "mirror", None)
    if not mirror:
        return None
    if workspace_id is not None and int(mirror.workspace_id) != int(workspace_id):
        return None
    return mirror


def active_workspace_tenant() -> str | None:
    tenant_key = getattr(runtime_state.active_workspace_context, "tenant_key", None)
    if tenant_key is None:
        mirror = active_workspace_mirror()
        if mirror:
            return str(mirror.tenant_key)
    return str(tenant_key) if tenant_key else None


def active_workspace_scope() -> str | None:
    scope_key = getattr(runtime_state.active_workspace_context, "scope_key", None)
    if scope_key is None:
        mirror = active_workspace_mirror()
        if mirror:
            return str(mirror.scope_key)
    return str(scope_key) if scope_key else None


def effective_tenant_key(explicit_tenant_key: str | None = None) -> str:
    tenant_key = str(explicit_tenant_key or active_workspace_tenant() or "").strip()
    return tenant_key or settings.default_tenant_id


def workspace_scope_for_db(db: Session) -> str:
    bind = db.get_bind()
    if bind is None:
        base = str(getattr(settings, "database_url", "") or "default-db")
    else:
        url = str(bind.url.render_as_string(hide_password=False))
        if ":memory:" in url:
            return f"sqlite-memory-{id(bind)}"
        base = url
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]


def effective_workspace_scope(explicit_scope_key: str | None = None) -> str:
    scope_key = str(explicit_scope_key or active_workspace_scope() or "").strip()
    if scope_key:
        return scope_key
    base = str(getattr(settings, "database_url", "") or "default-db")
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]


def workspace_base_key(tenant_key: str, workspace_id: int, scope_key: str | None = None) -> str:
    resolved_scope_key = effective_workspace_scope(scope_key)
    return f"{settings.redis_key_namespace}:vfs:scope:{resolved_scope_key}:tenant:{tenant_key}:workspace:{workspace_id}"


def workspace_index_key(tenant_key: str, workspace_id: int, scope_key: str | None = None) -> str:
    resolved_scope_key = effective_workspace_scope(scope_key)
    return f"{settings.redis_key_namespace}:vfs:scope:{resolved_scope_key}:tenant:{tenant_key}:workspace-index:{workspace_id}"


def workspace_dirty_set_key() -> str:
    return f"{settings.redis_key_namespace}:vfs:dirty-workspaces"


def workspace_lock_key(base_key: str) -> str:
    return f"{base_key}:lock"


def workspace_mirror_key(base_key: str) -> str:
    return f"{base_key}:mirror"


def workspace_mirror_nodes_key(base_key: str) -> str:
    return f"{base_key}:mirror-nodes"


def workspace_mirror_dirty_nodes_key(base_key: str) -> str:
    return f"{base_key}:mirror-dirty-nodes"


def workspace_error_key(base_key: str) -> str:
    return f"{base_key}:checkpoint-error"


def workspace_queue_key() -> str:
    return f"{settings.redis_key_namespace}:vfs:checkpoint-queue"


def workspace_enqueued_key() -> str:
    return f"{settings.redis_key_namespace}:vfs:checkpoint-enqueued"


def workspace_due_key(base_key: str) -> str:
    return f"{base_key}:checkpoint-due-at"


def workspace_retry_count_key(base_key: str) -> str:
    return f"{base_key}:checkpoint-retry-count"


def workspace_dead_letter_set_key() -> str:
    return f"{settings.redis_key_namespace}:vfs:checkpoint-dead-letter"


def workspace_dead_letter_payload_key(base_key: str) -> str:
    return f"{base_key}:checkpoint-dead-letter"


def mirror_has_dirty_state(mirror: WorkspaceMirror) -> bool:
    return bool(
        mirror.dirty_content_node_ids
        or mirror.dirty_structure_node_ids
        or mirror.dirty_session
        or mirror.dirty_workspace_metadata
    )


def _checkpoint_retry_delay_seconds(failure_count: int) -> float:
    exponent = max(int(failure_count) - 1, 0)
    return min(max(VFS_CHECKPOINT_RETRY_BASE_SECONDS, 0.01) * (2 ** exponent), max(VFS_CHECKPOINT_RETRY_MAX_SECONDS, 0.01))


def _clear_checkpoint_failure_state(client, base_key: str) -> None:
    client.delete(workspace_retry_count_key(base_key))
    client.delete(workspace_dead_letter_payload_key(base_key))
    client.srem(workspace_dead_letter_set_key(), base_key)


def _record_checkpoint_failure(
    client,
    base_key: str,
    *,
    reason: str,
    error_payload: dict[str, object],
) -> tuple[bool, int]:
    failure_count = int(client.get(workspace_retry_count_key(base_key)) or 0) + 1
    client.set(workspace_retry_count_key(base_key), str(failure_count))
    if failure_count >= max(int(VFS_CHECKPOINT_MAX_FAILURES), 1):
        client.sadd(workspace_dead_letter_set_key(), base_key)
        client.set(
            workspace_dead_letter_payload_key(base_key),
            json.dumps(
                {
                    "base_key": base_key,
                    "failure_count": failure_count,
                    "reason": reason,
                    "last_failure": error_payload,
                    "ts": datetime.utcnow().isoformat(),
                },
                ensure_ascii=False,
            ),
        )
        return True, failure_count
    return False, failure_count


def snapshot_workspace_checkpoint_metrics() -> dict[str, int]:
    from iruka_vfs import service

    client = service._get_redis_client()
    queue_depth = 0
    enqueued = 0
    dirty = 0
    dead_letter = 0
    try:
        if hasattr(client, "llen"):
            queue_depth = int(client.llen(workspace_queue_key()) or 0)
        elif hasattr(client, "queues"):
            queue_depth = len(client.queues.get(workspace_queue_key(), []))
        if hasattr(client, "scard"):
            enqueued = int(client.scard(workspace_enqueued_key()) or 0)
            dirty = int(client.scard(workspace_dirty_set_key()) or 0)
            dead_letter = int(client.scard(workspace_dead_letter_set_key()) or 0)
        elif hasattr(client, "sets"):
            enqueued = len(client.sets.get(workspace_enqueued_key(), set()))
            dirty = len(client.sets.get(workspace_dirty_set_key(), set()))
            dead_letter = len(client.sets.get(workspace_dead_letter_set_key(), set()))
    except Exception:
        pass
    return {
        "checkpoint_queue_depth": int(queue_depth),
        "checkpoint_enqueued": int(enqueued),
        "checkpoint_dirty_workspaces": int(dirty),
        "checkpoint_dead_letter": int(dead_letter),
    }


def _serialize_node_payload(node: VirtualFileNode) -> dict[str, object]:
    return {
        "id": int(node.id),
        "tenant_id": str(getattr(node, "tenant_id", "") or effective_tenant_key()),
        "workspace_id": int(node.workspace_id),
        "parent_id": int(node.parent_id) if node.parent_id is not None else None,
        "name": str(node.name or ""),
        "node_type": str(node.node_type or "file"),
        "content_text": str(node.content_text or ""),
        "version_no": int(node.version_no or 1),
    }


def serialize_workspace_mirror(mirror: WorkspaceMirror) -> str:
    payload = {
        "tenant_key": mirror.tenant_key,
        "scope_key": mirror.scope_key,
        "workspace_id": mirror.workspace_id,
        "chapter_id": mirror.chapter_id,
        "root_id": mirror.root_id,
        "session_id": mirror.session_id,
        "cwd_node_id": mirror.cwd_node_id,
        "path_to_id": {path: int(node_id) for path, node_id in mirror.path_to_id.items()},
        "children_by_parent": {
            "null" if parent_id is None else str(parent_id): [int(child_id) for child_id in child_ids]
            for parent_id, child_ids in mirror.children_by_parent.items()
        },
        "workspace_metadata": dict(mirror.workspace_metadata),
        "revision": int(mirror.revision),
        "checkpoint_revision": int(mirror.checkpoint_revision),
        "dirty_content_node_ids": sorted(int(node_id) for node_id in mirror.dirty_content_node_ids),
        "dirty_structure_node_ids": sorted(int(node_id) for node_id in mirror.dirty_structure_node_ids),
        "dirty_session": bool(mirror.dirty_session),
        "dirty_workspace_metadata": bool(mirror.dirty_workspace_metadata),
        "next_temp_id": int(mirror.next_temp_id),
        "nodes": {
            str(node_id): _serialize_node_payload(node)
            for node_id, node in mirror.nodes.items()
        },
    }
    return json.dumps(payload, ensure_ascii=False)


def serialize_workspace_mirror_meta(mirror: WorkspaceMirror) -> str:
    payload = {
        "tenant_key": mirror.tenant_key,
        "scope_key": mirror.scope_key,
        "workspace_id": mirror.workspace_id,
        "chapter_id": mirror.chapter_id,
        "root_id": mirror.root_id,
        "session_id": mirror.session_id,
        "cwd_node_id": mirror.cwd_node_id,
        "path_to_id": {path: int(node_id) for path, node_id in mirror.path_to_id.items()},
        "children_by_parent": {
            "null" if parent_id is None else str(parent_id): [int(child_id) for child_id in child_ids]
            for parent_id, child_ids in mirror.children_by_parent.items()
        },
        "workspace_metadata": dict(mirror.workspace_metadata),
        "revision": int(mirror.revision),
        "checkpoint_revision": int(mirror.checkpoint_revision),
        "dirty_content_node_ids": sorted(int(node_id) for node_id in mirror.dirty_content_node_ids),
        "dirty_structure_node_ids": sorted(int(node_id) for node_id in mirror.dirty_structure_node_ids),
        "dirty_session": bool(mirror.dirty_session),
        "dirty_workspace_metadata": bool(mirror.dirty_workspace_metadata),
        "next_temp_id": int(mirror.next_temp_id),
    }
    return json.dumps(payload, ensure_ascii=False)


def serialize_workspace_nodes(nodes: dict[int, VirtualFileNode]) -> str:
    return json.dumps({str(node_id): _serialize_node_payload(node) for node_id, node in nodes.items()}, ensure_ascii=False)


def deserialize_workspace_nodes(raw_value: str | None) -> dict[int, VirtualFileNode]:
    nodes: dict[int, VirtualFileNode] = {}
    if not raw_value:
        return nodes
    payload = json.loads(raw_value)
    for raw_node_id, node_payload in dict(payload or {}).items():
        node_id = int(raw_node_id)
        nodes[node_id] = VirtualFileNode(
            id=int(node_payload["id"]),
            tenant_id=str(node_payload.get("tenant_id") or effective_tenant_key()),
            workspace_id=int(node_payload["workspace_id"]),
            parent_id=int(node_payload["parent_id"]) if node_payload.get("parent_id") is not None else None,
            name=str(node_payload.get("name") or ""),
            node_type=str(node_payload.get("node_type") or "file"),
            content_text=str(node_payload.get("content_text") or ""),
            version_no=int(node_payload.get("version_no") or 1),
        )
    return nodes


def deserialize_workspace_mirror(
    raw_value: str,
    *,
    raw_nodes_value: str | None = None,
    raw_dirty_nodes_value: str | None = None,
) -> WorkspaceMirror:
    payload = json.loads(raw_value)
    if "nodes" in payload:
        return _deserialize_legacy_workspace_mirror(payload)

    tenant_key = str(payload.get("tenant_key") or "default")
    nodes = deserialize_workspace_nodes(raw_nodes_value)
    dirty_nodes = deserialize_workspace_nodes(raw_dirty_nodes_value)
    for node in list(nodes.values()) + list(dirty_nodes.values()):
        if not str(getattr(node, "tenant_id", "") or "").strip():
            node.tenant_id = tenant_key
    nodes.update(dirty_nodes)
    children_by_parent: dict[int | None, list[int]] = {}
    for raw_parent, raw_child_ids in dict(payload.get("children_by_parent") or {}).items():
        parent_id = None if raw_parent == "null" else int(raw_parent)
        children_by_parent[parent_id] = [int(item) for item in list(raw_child_ids or [])]
    return WorkspaceMirror(
        tenant_key=tenant_key,
        scope_key=str(payload.get("scope_key") or effective_workspace_scope()),
        workspace_id=int(payload["workspace_id"]),
        chapter_id=int(payload["chapter_id"]),
        root_id=int(payload["root_id"]),
        session_id=int(payload["session_id"]),
        cwd_node_id=int(payload["cwd_node_id"]),
        nodes=nodes,
        path_to_id={str(path): int(node_id) for path, node_id in dict(payload.get("path_to_id") or {}).items()},
        children_by_parent=children_by_parent,
        workspace_metadata=dict(payload.get("workspace_metadata") or {}),
        revision=int(payload.get("revision") or 1),
        checkpoint_revision=int(payload.get("checkpoint_revision") or 0),
        dirty_content_node_ids={
            int(node_id)
            for node_id in list(
                payload.get("dirty_content_node_ids")
                or payload.get("dirty_node_ids")
                or []
            )
        },
        dirty_structure_node_ids={int(node_id) for node_id in list(payload.get("dirty_structure_node_ids") or [])},
        dirty_session=bool(payload.get("dirty_session")),
        dirty_workspace_metadata=bool(payload.get("dirty_workspace_metadata")),
        next_temp_id=int(payload.get("next_temp_id") or -1),
    )


def _deserialize_legacy_workspace_mirror(payload: dict[str, object]) -> WorkspaceMirror:
    tenant_key = str(payload.get("tenant_key") or "default")
    nodes = deserialize_workspace_nodes(json.dumps(dict(payload.get("nodes") or {}), ensure_ascii=False))
    for node in nodes.values():
        if not str(getattr(node, "tenant_id", "") or "").strip():
            node.tenant_id = tenant_key
    children_by_parent: dict[int | None, list[int]] = {}
    for raw_parent, raw_child_ids in dict(payload.get("children_by_parent") or {}).items():
        parent_id = None if raw_parent == "null" else int(raw_parent)
        children_by_parent[parent_id] = [int(item) for item in list(raw_child_ids or [])]
    return WorkspaceMirror(
        tenant_key=tenant_key,
        scope_key=str(payload.get("scope_key") or effective_workspace_scope()),
        workspace_id=int(payload["workspace_id"]),
        chapter_id=int(payload["chapter_id"]),
        root_id=int(payload["root_id"]),
        session_id=int(payload["session_id"]),
        cwd_node_id=int(payload["cwd_node_id"]),
        nodes=nodes,
        path_to_id={str(path): int(node_id) for path, node_id in dict(payload.get("path_to_id") or {}).items()},
        children_by_parent=children_by_parent,
        workspace_metadata=dict(payload.get("workspace_metadata") or {}),
        revision=int(payload.get("revision") or 1),
        checkpoint_revision=int(payload.get("checkpoint_revision") or 0),
        dirty_content_node_ids={
            int(node_id)
            for node_id in list(
                payload.get("dirty_content_node_ids")
                or payload.get("dirty_node_ids")
                or []
            )
        },
        dirty_structure_node_ids={int(node_id) for node_id in list(payload.get("dirty_structure_node_ids") or [])},
        dirty_session=bool(payload.get("dirty_session")),
        dirty_workspace_metadata=bool(payload.get("dirty_workspace_metadata")),
        next_temp_id=int(payload.get("next_temp_id") or -1),
    )


def load_workspace_mirror_by_base_key(client, base_key: str) -> WorkspaceMirror | None:
    raw_value = client.get(workspace_mirror_key(base_key))
    if not raw_value:
        return None
    payload = json.loads(raw_value)
    if "nodes" in payload:
        return _deserialize_legacy_workspace_mirror(payload)
    return deserialize_workspace_mirror(
        raw_value,
        raw_nodes_value=client.get(workspace_mirror_nodes_key(base_key)),
        raw_dirty_nodes_value=client.get(workspace_mirror_dirty_nodes_key(base_key)),
    )


def get_workspace_mirror(
    workspace_id: int,
    chapter_id: int | None = None,
    tenant_key: str | None = None,
    scope_key: str | None = None,
) -> WorkspaceMirror | None:
    active = active_workspace_mirror(workspace_id)
    if active and (chapter_id is None or int(active.chapter_id) == int(chapter_id)):
        return active
    from iruka_vfs import service

    client = service._get_redis_client()
    resolved_tenant_key = effective_tenant_key(tenant_key)
    resolved_scope_key = effective_workspace_scope(scope_key)
    base_key = client.get(workspace_index_key(resolved_tenant_key, workspace_id, resolved_scope_key))
    if not base_key:
        return None
    mirror = load_workspace_mirror_by_base_key(client, str(base_key))
    if not mirror:
        return None
    if chapter_id is not None and int(mirror.chapter_id) != int(chapter_id):
        return None
    return mirror


def enqueue_workspace_checkpoint(base_key: str, *, due_at: float | None = None, force: bool = False) -> None:
    from iruka_vfs import service

    client = service._get_redis_client()
    if not force and client.get(workspace_dead_letter_payload_key(base_key)):
        return
    scheduled_at = due_at if due_at is not None else time.time() + max(VFS_CHECKPOINT_DEBOUNCE_SECONDS, 0.0)
    client.set(workspace_due_key(base_key), str(scheduled_at))
    if int(client.sadd(workspace_enqueued_key(), base_key) or 0) == 1:
        client.rpush(workspace_queue_key(), base_key)


def set_workspace_mirror(mirror: WorkspaceMirror) -> None:
    from iruka_vfs import service

    client = service._get_redis_client()
    base_key = workspace_base_key(mirror.tenant_key, mirror.workspace_id, mirror.scope_key)
    client.set(workspace_index_key(mirror.tenant_key, mirror.workspace_id, mirror.scope_key), base_key)
    dirty_node_ids = mirror.dirty_content_node_ids | mirror.dirty_structure_node_ids
    use_full_snapshot = (
        not dirty_node_ids
        or bool(mirror.dirty_structure_node_ids)
        or any(int(node_id) <= 0 for node_id in dirty_node_ids)
        or not client.get(workspace_mirror_nodes_key(base_key))
    )
    client.set(workspace_mirror_key(base_key), serialize_workspace_mirror_meta(mirror))
    if use_full_snapshot:
        client.set(workspace_mirror_nodes_key(base_key), serialize_workspace_nodes(mirror.nodes))
        client.delete(workspace_mirror_dirty_nodes_key(base_key))
    elif dirty_node_ids:
        dirty_nodes = {
            int(node_id): mirror.nodes[int(node_id)]
            for node_id in dirty_node_ids
            if int(node_id) in mirror.nodes
        }
        client.set(workspace_mirror_dirty_nodes_key(base_key), serialize_workspace_nodes(dirty_nodes))
    if mirror_has_dirty_state(mirror):
        client.sadd(workspace_dirty_set_key(), base_key)
        enqueue_workspace_checkpoint(base_key)
    else:
        client.srem(workspace_dirty_set_key(), base_key)


def delete_workspace_mirror(workspace_id: int, tenant_id: str | None = None, scope_key: str | None = None) -> None:
    from iruka_vfs import service

    client = service._get_redis_client()
    tenant_key = effective_tenant_key(tenant_id)
    resolved_scope_key = effective_workspace_scope(scope_key)
    base_key = client.get(workspace_index_key(tenant_key, workspace_id, resolved_scope_key))
    if not base_key:
        return
    client.delete(workspace_mirror_key(base_key))
    client.delete(workspace_mirror_nodes_key(base_key))
    client.delete(workspace_mirror_dirty_nodes_key(base_key))
    client.delete(workspace_error_key(base_key))
    client.delete(workspace_due_key(base_key))
    client.delete(workspace_index_key(tenant_key, workspace_id, resolved_scope_key))
    client.srem(workspace_dirty_set_key(), base_key)
    client.srem(workspace_enqueued_key(), base_key)


def workspace_lock(
    mirror: WorkspaceMirror | None = None,
    *,
    workspace_id: int | None = None,
) -> tuple[redis.lock.Lock, str]:
    from iruka_vfs import service

    client = service._get_redis_client()
    if mirror is not None:
        base_key = workspace_base_key(mirror.tenant_key, mirror.workspace_id, mirror.scope_key)
    elif workspace_id is not None:
        base_key = str(
            client.get(workspace_index_key(effective_tenant_key(), workspace_id, effective_workspace_scope())) or ""
        )
        if not base_key:
            raise ValueError(f"workspace mirror index missing: {workspace_id}")
    else:
        raise ValueError("missing mirror or workspace_id")
    return client.lock(workspace_lock_key(base_key), timeout=30, blocking_timeout=5), base_key


def clone_node(node: VirtualFileNode) -> VirtualFileNode:
    return VirtualFileNode(
        id=int(node.id or 0),
        tenant_id=str(getattr(node, "tenant_id", "") or effective_tenant_key()),
        workspace_id=int(node.workspace_id),
        parent_id=int(node.parent_id) if node.parent_id is not None else None,
        name=str(node.name or ""),
        node_type=str(node.node_type or "file"),
        content_text=str(node.content_text or ""),
        version_no=int(node.version_no or 1),
        created_at=node.created_at,
        updated_at=node.updated_at,
    )


def _node_matches_payload(node: VirtualFileNode | None, payload: dict[str, object]) -> bool:
    if node is None:
        return False
    return (
        int(node.parent_id) if node.parent_id is not None else None
    ) == payload["parent_id"] and str(node.name or "") == payload["name"] and str(
        node.node_type or "file"
    ) == str(payload["node_type"] or "file") and str(node.content_text or "") == str(
        payload["content_text"] or ""
    ) and int(node.version_no or 1) == int(payload["version_no"] or 1)


def _snapshot_dirty_batch_locked(mirror: WorkspaceMirror) -> dict[str, object]:
    dirty_content_ids = sorted(int(node_id) for node_id in mirror.dirty_content_node_ids)[:MEMORY_CACHE_FLUSH_BATCH]
    dirty_structure_ids = sorted(int(node_id) for node_id in mirror.dirty_structure_node_ids)[:MEMORY_CACHE_FLUSH_BATCH]
    dirty_ids = list(dict.fromkeys(dirty_structure_ids + dirty_content_ids))[:MEMORY_CACHE_FLUSH_BATCH]
    dirty_payloads: list[dict[str, object]] = []
    for node_id in dirty_ids:
        node = mirror.nodes.get(node_id)
        if not node:
            continue
        dirty_payloads.append(
            {
                "node_id": int(node.id),
                "parent_id": int(node.parent_id) if node.parent_id is not None else None,
                "name": str(node.name or ""),
                "node_type": str(node.node_type or "file"),
                "content_text": str(node.content_text or ""),
                "version_no": int(node.version_no or 1),
            }
        )
    return {
        "snapshot_revision": int(mirror.revision),
        "dirty_content_ids": set(dirty_content_ids),
        "dirty_structure_ids": set(dirty_structure_ids),
        "dirty_ids": dirty_ids,
        "dirty_payloads": dirty_payloads,
        "session_dirty": bool(mirror.dirty_session),
        "metadata_dirty": bool(mirror.dirty_workspace_metadata),
        "cwd_node_id": int(mirror.cwd_node_id),
        "workspace_metadata": dict(mirror.workspace_metadata),
        "tenant_key": str(mirror.tenant_key),
        "workspace_id": int(mirror.workspace_id),
        "chapter_id": int(mirror.chapter_id),
    }


def _remap_payload_node_ids(
    payloads: list[dict[str, object]],
    remapped_ids: list[tuple[int, int]],
) -> list[dict[str, object]]:
    if not remapped_ids:
        return payloads
    remap = {int(temp_id): int(real_id) for temp_id, real_id in remapped_ids}
    remapped_payloads: list[dict[str, object]] = []
    for payload in payloads:
        item = dict(payload)
        node_id = int(item["node_id"])
        parent_id = item["parent_id"]
        item["node_id"] = remap.get(node_id, node_id)
        if parent_id is not None:
            item["parent_id"] = remap.get(int(parent_id), int(parent_id))
        remapped_payloads.append(item)
    return remapped_payloads


def _confirm_flushed_snapshot_locked(
    current: WorkspaceMirror,
    *,
    snapshot: dict[str, object],
    remapped_ids: list[tuple[int, int]],
) -> None:
    snapshot_revision = int(snapshot["snapshot_revision"])
    flushed_payloads = _remap_payload_node_ids(list(snapshot["dirty_payloads"]), remapped_ids)
    remap = {int(temp_id): int(real_id) for temp_id, real_id in remapped_ids}
    content_ids = {remap.get(int(node_id), int(node_id)) for node_id in set(snapshot["dirty_content_ids"])}
    structure_ids = {remap.get(int(node_id), int(node_id)) for node_id in set(snapshot["dirty_structure_ids"])}

    for temp_id, real_id in remapped_ids:
        node = current.nodes.pop(temp_id, None)
        if not node:
            continue
        node.id = real_id
        if node.parent_id is not None and int(node.parent_id) == temp_id:
            node.parent_id = real_id
        current.nodes[real_id] = node
        for child_ids in current.children_by_parent.values():
            for idx, child_id in enumerate(child_ids):
                if child_id == temp_id:
                    child_ids[idx] = real_id
        current.path_to_id = {
            path: (real_id if node_id == temp_id else node_id)
            for path, node_id in current.path_to_id.items()
        }
        if current.cwd_node_id == temp_id:
            current.cwd_node_id = real_id

    for payload in flushed_payloads:
        node_id = int(payload["node_id"])
        current_node = current.nodes.get(node_id)
        if node_id in content_ids and _node_matches_payload(current_node, payload):
            current.dirty_content_node_ids.discard(node_id)
        if node_id in structure_ids and _node_matches_payload(current_node, payload):
            current.dirty_structure_node_ids.discard(node_id)

    if bool(snapshot["session_dirty"]) and int(current.cwd_node_id) == int(snapshot["cwd_node_id"]):
        current.dirty_session = False
    if bool(snapshot["metadata_dirty"]) and dict(current.workspace_metadata) == dict(snapshot["workspace_metadata"]):
        current.dirty_workspace_metadata = False

    current.checkpoint_revision = max(int(current.checkpoint_revision), snapshot_revision)

def ensure_children_sorted_locked(mirror: WorkspaceMirror, parent_id: int | None) -> None:
    child_ids = mirror.children_by_parent.get(parent_id, [])
    child_ids.sort(key=lambda node_id: (mirror.nodes[node_id].node_type, mirror.nodes[node_id].name))


def mirror_node_path_locked(mirror: WorkspaceMirror, node: VirtualFileNode) -> str:
    if node.parent_id is None:
        return "/"
    names = [node.name]
    parent_id = node.parent_id
    while parent_id is not None:
        parent = mirror.nodes.get(parent_id)
        if not parent or parent.parent_id is None:
            break
        names.append(parent.name)
        parent_id = parent.parent_id
    return "/" + "/".join(reversed(names))


def rebuild_workspace_mirror_indexes_locked(mirror: WorkspaceMirror) -> None:
    mirror.path_to_id = {}
    mirror.children_by_parent = {}
    for node in mirror.nodes.values():
        mirror.children_by_parent.setdefault(node.parent_id, []).append(int(node.id))
    for parent_id in list(mirror.children_by_parent.keys()):
        ensure_children_sorted_locked(mirror, parent_id)
    for node in mirror.nodes.values():
        mirror.path_to_id[mirror_node_path_locked(mirror, node)] = int(node.id)


def build_workspace_mirror(
    db: Session,
    workspace: AgentWorkspace,
    chapter: Chapter | int,
    *,
    session: VirtualShellSession,
) -> WorkspaceMirror:
    nodes = _repositories.node.list_workspace_nodes(db, workspace.id, workspace_tenant_key(workspace))
    cloned: dict[int, VirtualFileNode] = {}
    for source in nodes:
        cloned_node = clone_node(source)
        cloned[int(cloned_node.id)] = cloned_node
    root = next((node for node in cloned.values() if node.parent_id is None), None)
    if root is None:
        raise ValueError(f"virtual root not found for workspace {workspace.id}")
    mirror = WorkspaceMirror(
        tenant_key=workspace_tenant_key(workspace),
        scope_key=workspace_scope_for_db(db),
        workspace_id=int(workspace.id),
        chapter_id=int(chapter.id if hasattr(chapter, "id") else chapter),
        root_id=int(root.id),
        session_id=int(session.id or 0),
        cwd_node_id=int(session.cwd_node_id or root.id),
        nodes=cloned,
        path_to_id={},
        children_by_parent={},
        workspace_metadata=dict(workspace.metadata_json or {}),
        revision=1,
        checkpoint_revision=1,
    )
    rebuild_workspace_mirror_indexes_locked(mirror)
    return mirror


def ensure_workspace_checkpoint_worker(engine: Engine) -> None:
    if runtime_state.workspace_checkpoint_worker_started:
        return
    with runtime_state.redis_client_lock:
        if runtime_state.workspace_checkpoint_worker_started:
            return
        runtime_state.workspace_checkpoint_session_maker = sessionmaker(
            bind=engine,
            autoflush=False,
            autocommit=False,
            class_=Session,
        )
        worker = threading.Thread(
            target=workspace_checkpoint_worker,
            name="vfs-workspace-checkpoint-worker",
            daemon=True,
        )
        worker.start()
        runtime_state.workspace_checkpoint_worker_started = True


def workspace_checkpoint_worker() -> None:
    if runtime_state.workspace_checkpoint_session_maker is None:
        return
    from iruka_vfs import service

    client = service._get_redis_client()
    while True:
        try:
            item = client.blpop(workspace_queue_key(), timeout=max(int(MEMORY_CACHE_FLUSH_INTERVAL_SECONDS), 1))
        except Exception:
            continue
        if not item:
            continue
        _, base_key = item
        try:
            if client.get(workspace_dead_letter_payload_key(str(base_key))):
                client.srem(workspace_enqueued_key(), str(base_key))
                client.delete(workspace_due_key(str(base_key)))
                continue
            due_raw = client.get(workspace_due_key(str(base_key)))
            if due_raw:
                try:
                    due_at = float(due_raw)
                except (TypeError, ValueError):
                    due_at = 0.0
                remaining = due_at - time.time()
                if remaining > 0:
                    time.sleep(min(remaining, max(VFS_CHECKPOINT_DEBOUNCE_SECONDS, 0.01)))
                    client.rpush(workspace_queue_key(), str(base_key))
                    service._cache_metric_inc("checkpoint_requeue")
                    continue
            ok = flush_workspace_mirror(None, base_key=str(base_key))
            client.srem(workspace_enqueued_key(), str(base_key))
            client.delete(workspace_due_key(str(base_key)))
            current = load_workspace_mirror_by_base_key(client, str(base_key))
            if current:
                if mirror_has_dirty_state(current):
                    enqueue_workspace_checkpoint(str(base_key))
                    service._cache_metric_inc("checkpoint_requeue")
                    continue
            if not ok:
                error_raw = client.get(workspace_error_key(str(base_key)))
                error_payload = json.loads(error_raw) if error_raw else {"error_message": "flush returned false"}
                dead_lettered, failure_count = _record_checkpoint_failure(
                    client,
                    str(base_key),
                    reason="flush-returned-false",
                    error_payload=dict(error_payload),
                )
                if dead_lettered:
                    service._cache_metric_inc("checkpoint_dead_letter")
                    client.srem(workspace_enqueued_key(), str(base_key))
                    client.delete(workspace_due_key(str(base_key)))
                    continue
                enqueue_workspace_checkpoint(
                    str(base_key),
                    due_at=time.time() + _checkpoint_retry_delay_seconds(failure_count),
                    force=True,
                )
                service._cache_metric_inc("checkpoint_retry")
                continue
            _clear_checkpoint_failure_state(client, str(base_key))
        except Exception as exc:
            service._cache_metric_inc("flush_error")
            error_payload = {
                "ts": datetime.utcnow().isoformat(),
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "traceback": traceback.format_exc(limit=20),
            }
            client.set(
                workspace_error_key(str(base_key)),
                json.dumps(error_payload, ensure_ascii=False),
            )
            client.srem(workspace_enqueued_key(), str(base_key))
            client.delete(workspace_due_key(str(base_key)))
            dead_lettered, failure_count = _record_checkpoint_failure(
                client,
                str(base_key),
                reason="worker-exception",
                error_payload=error_payload,
            )
            if dead_lettered:
                service._cache_metric_inc("checkpoint_dead_letter")
                continue
            enqueue_workspace_checkpoint(
                str(base_key),
                due_at=time.time() + _checkpoint_retry_delay_seconds(failure_count),
                force=True,
            )
            service._cache_metric_inc("checkpoint_retry")


def flush_workspace_mirror(mirror: WorkspaceMirror | None, *, base_key: str | None = None) -> bool:
    if runtime_state.workspace_checkpoint_session_maker is None:
        return False
    from iruka_vfs import service

    client = service._get_redis_client()
    resolved_base_key = base_key or workspace_base_key(mirror.tenant_key, mirror.workspace_id)
    lock = client.lock(workspace_lock_key(resolved_base_key), timeout=30, blocking_timeout=1)
    if not lock.acquire(blocking=True):
        return False
    try:
        if mirror is None:
            mirror = load_workspace_mirror_by_base_key(client, resolved_base_key)
            if not mirror:
                client.srem(workspace_dirty_set_key(), resolved_base_key)
                return True
        with mirror.lock:
            snapshot = _snapshot_dirty_batch_locked(mirror)
            dirty_ids = list(snapshot["dirty_ids"])
            if not dirty_ids and not bool(snapshot["session_dirty"]) and not bool(snapshot["metadata_dirty"]):
                client.srem(workspace_dirty_set_key(), resolved_base_key)
                return True

        db = runtime_state.workspace_checkpoint_session_maker()
        try:
            remapped_ids: list[tuple[int, int]] = []
            for payload in list(snapshot["dirty_payloads"]):
                node_id = int(payload["node_id"])
                if node_id > 0:
                    _repositories.node.update_node_content(
                        db,
                        node_id=node_id,
                        tenant_key=str(snapshot["tenant_key"]),
                        parent_id=payload["parent_id"],
                        name=payload["name"],
                        node_type=payload["node_type"],
                        content_text=payload["content_text"],
                        version_no=payload["version_no"],
                    )
                    service._cache_metric_inc("flush_ok")
                    continue

                row = _repositories.node.create_node(
                    db,
                    tenant_key=str(snapshot["tenant_key"]),
                    workspace_id=int(snapshot["workspace_id"]),
                    parent_id=payload["parent_id"],
                    name=payload["name"],
                    node_type=payload["node_type"],
                    content_text=payload["content_text"],
                    version_no=payload["version_no"],
                )
                remapped_ids.append((node_id, int(row.id)))
                service._cache_metric_inc("flush_ok")

            if bool(snapshot["session_dirty"]):
                _repositories.session.update_session_cwd(
                    db,
                    session_id=int(mirror.session_id),
                    tenant_key=str(snapshot["tenant_key"]),
                    cwd_node_id=int(snapshot["cwd_node_id"]),
                )
            if bool(snapshot["metadata_dirty"]):
                _repositories.workspace.update_workspace_metadata(
                    db,
                    workspace_id=int(snapshot["workspace_id"]),
                    tenant_key=str(snapshot["tenant_key"]),
                    metadata_json=dict(snapshot["workspace_metadata"]),
                )
            db.commit()
            runtime_seed = service._get_registered_runtime_seed(int(snapshot["workspace_id"]), str(snapshot["tenant_key"]))
            primary_file = runtime_seed.primary_file if runtime_seed else None
            chapter_path = str(dict(snapshot["workspace_metadata"]).get("virtual_chapter_file") or "")
            if (
                primary_file is not None
                and isinstance(primary_file, WritableFileSource)
                and primary_file.virtual_path == chapter_path
                and primary_file.write_text is not None
            ):
                chapter_node_id = mirror.path_to_id.get(chapter_path)
                chapter_node = mirror.nodes.get(int(chapter_node_id)) if chapter_node_id is not None else None
                if chapter_node is not None:
                    primary_file.write_text(str(chapter_node.content_text or ""))
        except Exception as exc:
            db.rollback()
            service._cache_metric_inc("flush_error")
            client.set(
                workspace_error_key(resolved_base_key),
                json.dumps(
                    {
                        "workspace_id": int(snapshot["workspace_id"]) if "snapshot" in locals() else None,
                        "chapter_id": int(snapshot["chapter_id"]) if "snapshot" in locals() else None,
                        "tenant_key": str(snapshot["tenant_key"]) if "snapshot" in locals() else None,
                        "ts": datetime.utcnow().isoformat(),
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                        "traceback": traceback.format_exc(limit=20),
                    },
                    ensure_ascii=False,
                ),
            )
            return False
        try:
            current = load_workspace_mirror_by_base_key(client, resolved_base_key)
            if not current:
                client.srem(workspace_dirty_set_key(), resolved_base_key)
                client.delete(workspace_error_key(resolved_base_key))
                return True
            with current.lock:
                _confirm_flushed_snapshot_locked(current, snapshot=snapshot, remapped_ids=remapped_ids)
                set_workspace_mirror(current)
                client.delete(workspace_error_key(resolved_base_key))
                _clear_checkpoint_failure_state(client, resolved_base_key)
        finally:
            db.close()
    finally:
        try:
            lock.release()
        except Exception:
            pass
    return True
