from __future__ import annotations

from datetime import datetime
import hashlib
import json
import threading
import traceback

import redis
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from iruka_vfs.constants import MEMORY_CACHE_FLUSH_BATCH, MEMORY_CACHE_FLUSH_INTERVAL_SECONDS
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


def workspace_error_key(base_key: str) -> str:
    return f"{base_key}:checkpoint-error"


def workspace_queue_key() -> str:
    return f"{settings.redis_key_namespace}:vfs:checkpoint-queue"


def workspace_enqueued_key() -> str:
    return f"{settings.redis_key_namespace}:vfs:checkpoint-enqueued"


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
        "dirty_node_ids": sorted(int(node_id) for node_id in mirror.dirty_node_ids),
        "dirty_session": bool(mirror.dirty_session),
        "dirty_workspace_metadata": bool(mirror.dirty_workspace_metadata),
        "next_temp_id": int(mirror.next_temp_id),
        "nodes": {
            str(node_id): {
                "id": int(node.id),
                "workspace_id": int(node.workspace_id),
                "parent_id": int(node.parent_id) if node.parent_id is not None else None,
                "name": str(node.name or ""),
                "node_type": str(node.node_type or "file"),
                "content_text": str(node.content_text or ""),
                "version_no": int(node.version_no or 1),
            }
            for node_id, node in mirror.nodes.items()
        },
    }
    return json.dumps(payload, ensure_ascii=False)


def deserialize_workspace_mirror(raw_value: str) -> WorkspaceMirror:
    payload = json.loads(raw_value)
    nodes: dict[int, VirtualFileNode] = {}
    for raw_node_id, node_payload in dict(payload.get("nodes") or {}).items():
        node_id = int(raw_node_id)
        nodes[node_id] = VirtualFileNode(
            id=int(node_payload["id"]),
            workspace_id=int(node_payload["workspace_id"]),
            parent_id=int(node_payload["parent_id"]) if node_payload.get("parent_id") is not None else None,
            name=str(node_payload.get("name") or ""),
            node_type=str(node_payload.get("node_type") or "file"),
            content_text=str(node_payload.get("content_text") or ""),
            version_no=int(node_payload.get("version_no") or 1),
        )
    children_by_parent: dict[int | None, list[int]] = {}
    for raw_parent, raw_child_ids in dict(payload.get("children_by_parent") or {}).items():
        parent_id = None if raw_parent == "null" else int(raw_parent)
        children_by_parent[parent_id] = [int(item) for item in list(raw_child_ids or [])]
    return WorkspaceMirror(
        tenant_key=str(payload.get("tenant_key") or "default"),
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
        dirty_node_ids={int(node_id) for node_id in list(payload.get("dirty_node_ids") or [])},
        dirty_session=bool(payload.get("dirty_session")),
        dirty_workspace_metadata=bool(payload.get("dirty_workspace_metadata")),
        next_temp_id=int(payload.get("next_temp_id") or -1),
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
    raw_value = client.get(workspace_mirror_key(base_key))
    if not raw_value:
        return None
    mirror = deserialize_workspace_mirror(raw_value)
    if chapter_id is not None and int(mirror.chapter_id) != int(chapter_id):
        return None
    return mirror


def enqueue_workspace_checkpoint(base_key: str) -> None:
    from iruka_vfs import service

    client = service._get_redis_client()
    if int(client.sadd(workspace_enqueued_key(), base_key) or 0) == 1:
        client.rpush(workspace_queue_key(), base_key)


def set_workspace_mirror(mirror: WorkspaceMirror) -> None:
    from iruka_vfs import service

    client = service._get_redis_client()
    base_key = workspace_base_key(mirror.tenant_key, mirror.workspace_id, mirror.scope_key)
    client.set(workspace_index_key(mirror.tenant_key, mirror.workspace_id, mirror.scope_key), base_key)
    client.set(workspace_mirror_key(base_key), serialize_workspace_mirror(mirror))
    if mirror.dirty_node_ids or mirror.dirty_session or mirror.dirty_workspace_metadata:
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
    client.delete(workspace_error_key(base_key))
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
            ok = flush_workspace_mirror(None, base_key=str(base_key))
            client.srem(workspace_enqueued_key(), str(base_key))
            current_raw = client.get(workspace_mirror_key(str(base_key)))
            if current_raw:
                current = deserialize_workspace_mirror(current_raw)
                if current.dirty_node_ids or current.dirty_session or current.dirty_workspace_metadata:
                    enqueue_workspace_checkpoint(str(base_key))
                    continue
            if not ok:
                enqueue_workspace_checkpoint(str(base_key))
        except Exception as exc:
            service._cache_metric_inc("flush_error")
            client.set(
                workspace_error_key(str(base_key)),
                json.dumps(
                    {
                        "ts": datetime.utcnow().isoformat(),
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                        "traceback": traceback.format_exc(limit=20),
                    },
                    ensure_ascii=False,
                ),
            )


def flush_workspace_mirror(mirror: WorkspaceMirror | None, *, base_key: str | None = None) -> bool:
    if runtime_state.workspace_checkpoint_session_maker is None:
        return False
    from iruka_vfs import service

    client = service._get_redis_client()
    resolved_base_key = base_key or workspace_base_key(mirror.tenant_key, mirror.workspace_id)
    lock = client.lock(workspace_lock_key(resolved_base_key), timeout=30, blocking_timeout=1)
    if not lock.acquire(blocking=False):
        return False
    try:
        if mirror is None:
            raw_value = client.get(workspace_mirror_key(resolved_base_key))
            if not raw_value:
                client.srem(workspace_dirty_set_key(), resolved_base_key)
                return True
            mirror = deserialize_workspace_mirror(raw_value)
        with mirror.lock:
            dirty_ids = list(mirror.dirty_node_ids)[:MEMORY_CACHE_FLUSH_BATCH]
            session_dirty = bool(mirror.dirty_session)
            metadata_dirty = bool(mirror.dirty_workspace_metadata)
            if not dirty_ids and not session_dirty and not metadata_dirty:
                client.srem(workspace_dirty_set_key(), resolved_base_key)
                return True
            snapshot_revision = int(mirror.revision)
            dirty_payloads = []
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
            cwd_node_id = int(mirror.cwd_node_id)
            workspace_metadata = dict(mirror.workspace_metadata)
            tenant_key = str(mirror.tenant_key)
            workspace_id = int(mirror.workspace_id)
            chapter_id = int(mirror.chapter_id)
    finally:
        try:
            lock.release()
        except Exception:
            pass

    db = runtime_state.workspace_checkpoint_session_maker()
    try:
        remapped_ids: list[tuple[int, int]] = []
        for payload in dirty_payloads:
            node_id = int(payload["node_id"])
            if node_id > 0:
                _repositories.node.update_node_content(
                    db,
                    node_id=node_id,
                    tenant_key=tenant_key,
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
                tenant_key=tenant_key,
                workspace_id=mirror.workspace_id,
                parent_id=payload["parent_id"],
                name=payload["name"],
                node_type=payload["node_type"],
                content_text=payload["content_text"],
                version_no=payload["version_no"],
            )
            remapped_ids.append((node_id, int(row.id)))
            service._cache_metric_inc("flush_ok")

        if session_dirty:
            _repositories.session.update_session_cwd(
                db,
                session_id=mirror.session_id,
                tenant_key=tenant_key,
                cwd_node_id=cwd_node_id,
            )
        if metadata_dirty:
            _repositories.workspace.update_workspace_metadata(
                db,
                workspace_id=workspace_id,
                tenant_key=tenant_key,
                metadata_json=workspace_metadata,
            )
        db.commit()
        runtime_seed = service._get_registered_runtime_seed(workspace_id, tenant_key)
        primary_file = runtime_seed.primary_file if runtime_seed else None
        chapter_path = str(workspace_metadata.get("virtual_chapter_file") or "")
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
        confirm_lock = client.lock(workspace_lock_key(resolved_base_key), timeout=30, blocking_timeout=5)
        if not confirm_lock.acquire(blocking=True):
            return False
        try:
            current_raw = client.get(workspace_mirror_key(resolved_base_key))
            if not current_raw:
                client.srem(workspace_dirty_set_key(), resolved_base_key)
                client.delete(workspace_error_key(resolved_base_key))
                return True
            current = deserialize_workspace_mirror(current_raw)
            with current.lock:
                for temp_id, real_id in remapped_ids:
                    node = current.nodes.pop(temp_id, None)
                    if not node:
                        continue
                    node.id = real_id
                    current.nodes[real_id] = node
                    for child_ids in current.children_by_parent.values():
                        for idx, child_id in enumerate(child_ids):
                            if child_id == temp_id:
                                child_ids[idx] = real_id
                    current.path_to_id = {
                        path: (real_id if node_id == temp_id else node_id)
                        for path, node_id in current.path_to_id.items()
                    }
                    current.dirty_node_ids.discard(temp_id)
                    current.dirty_node_ids.add(real_id)
                    if current.cwd_node_id == temp_id:
                        current.cwd_node_id = real_id

                if int(current.revision) == snapshot_revision:
                    current.checkpoint_revision = max(int(current.checkpoint_revision), snapshot_revision)
                    current.dirty_node_ids.clear()
                    current.dirty_session = False
                    current.dirty_workspace_metadata = False
                else:
                    current.checkpoint_revision = max(int(current.checkpoint_revision), snapshot_revision)
                rebuild_workspace_mirror_indexes_locked(current)
                set_workspace_mirror(current)
                client.delete(workspace_error_key(resolved_base_key))
        finally:
            try:
                confirm_lock.release()
            except Exception:
                pass
    except Exception as exc:
        db.rollback()
        service._cache_metric_inc("flush_error")
        client.set(
            workspace_error_key(resolved_base_key),
            json.dumps(
                {
                    "workspace_id": workspace_id if "workspace_id" in locals() else None,
                    "chapter_id": chapter_id if "chapter_id" in locals() else None,
                    "tenant_key": tenant_key if "tenant_key" in locals() else None,
                    "ts": datetime.utcnow().isoformat(),
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "traceback": traceback.format_exc(limit=20),
                },
                ensure_ascii=False,
            ),
        )
        return False
    finally:
        db.close()
    return True
