from __future__ import annotations

import json

import redis

from iruka_vfs.models import WorkspaceMirror


def _serialize_node_payload(node) -> dict[str, object]:
    from iruka_vfs import workspace_mirror as mirror_api

    return {
        "id": int(node.id),
        "tenant_id": str(getattr(node, "tenant_id", "") or mirror_api.effective_tenant_key()),
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


def serialize_workspace_nodes(nodes: dict[int, object]) -> str:
    return json.dumps({str(node_id): _serialize_node_payload(node) for node_id, node in nodes.items()}, ensure_ascii=False)


def deserialize_workspace_nodes(raw_value: str | None) -> dict[int, object]:
    from iruka_vfs import workspace_mirror as mirror_api

    nodes: dict[int, object] = {}
    if not raw_value:
        return nodes
    payload = json.loads(raw_value)
    for raw_node_id, node_payload in dict(payload or {}).items():
        node_id = int(raw_node_id)
        nodes[node_id] = mirror_api.VirtualFileNode(
            id=int(node_payload["id"]),
            tenant_id=str(node_payload.get("tenant_id") or mirror_api.effective_tenant_key()),
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
    from iruka_vfs import workspace_mirror as mirror_api

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
        scope_key=str(payload.get("scope_key") or mirror_api.effective_workspace_scope()),
        workspace_id=int(payload["workspace_id"]),
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
            for node_id in list(payload.get("dirty_content_node_ids") or payload.get("dirty_node_ids") or [])
        },
        dirty_structure_node_ids={int(node_id) for node_id in list(payload.get("dirty_structure_node_ids") or [])},
        dirty_session=bool(payload.get("dirty_session")),
        dirty_workspace_metadata=bool(payload.get("dirty_workspace_metadata")),
        next_temp_id=int(payload.get("next_temp_id") or -1),
    )


def _deserialize_legacy_workspace_mirror(payload: dict[str, object]) -> WorkspaceMirror:
    from iruka_vfs import workspace_mirror as mirror_api

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
        scope_key=str(payload.get("scope_key") or mirror_api.effective_workspace_scope()),
        workspace_id=int(payload["workspace_id"]),
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
            for node_id in list(payload.get("dirty_content_node_ids") or payload.get("dirty_node_ids") or [])
        },
        dirty_structure_node_ids={int(node_id) for node_id in list(payload.get("dirty_structure_node_ids") or [])},
        dirty_session=bool(payload.get("dirty_session")),
        dirty_workspace_metadata=bool(payload.get("dirty_workspace_metadata")),
        next_temp_id=int(payload.get("next_temp_id") or -1),
    )


def load_workspace_mirror_by_base_key(client, base_key: str) -> WorkspaceMirror | None:
    from iruka_vfs import workspace_mirror as mirror_api

    raw_value = client.get(mirror_api.workspace_mirror_key(base_key))
    if not raw_value:
        return None
    payload = json.loads(raw_value)
    if "nodes" in payload:
        return _deserialize_legacy_workspace_mirror(payload)
    return deserialize_workspace_mirror(
        raw_value,
        raw_nodes_value=client.get(mirror_api.workspace_mirror_nodes_key(base_key)),
        raw_dirty_nodes_value=client.get(mirror_api.workspace_mirror_dirty_nodes_key(base_key)),
    )


def get_workspace_mirror(
    workspace_id: int,
    tenant_key: str | None = None,
    scope_key: str | None = None,
) -> WorkspaceMirror | None:
    from iruka_vfs import service
    from iruka_vfs import workspace_mirror as mirror_api

    active = mirror_api.active_workspace_mirror(workspace_id)
    if active:
        return active
    client = service._get_redis_client()
    resolved_tenant_key = mirror_api.effective_tenant_key(tenant_key)
    resolved_scope_key = mirror_api.effective_workspace_scope(scope_key)
    base_key = client.get(mirror_api.workspace_index_key(resolved_tenant_key, workspace_id, resolved_scope_key))
    if not base_key:
        return None
    mirror = load_workspace_mirror_by_base_key(client, str(base_key))
    if not mirror:
        return None
    return mirror


def set_workspace_mirror(mirror: WorkspaceMirror) -> None:
    from iruka_vfs import service
    from iruka_vfs import workspace_mirror as mirror_api

    client = service._get_redis_client()
    base_key = mirror_api.workspace_base_key(mirror.tenant_key, mirror.workspace_id, mirror.scope_key)
    client.set(mirror_api.workspace_index_key(mirror.tenant_key, mirror.workspace_id, mirror.scope_key), base_key)
    dirty_node_ids = mirror.dirty_content_node_ids | mirror.dirty_structure_node_ids
    use_full_snapshot = (
        not dirty_node_ids
        or bool(mirror.dirty_structure_node_ids)
        or any(int(node_id) <= 0 for node_id in dirty_node_ids)
        or not client.get(mirror_api.workspace_mirror_nodes_key(base_key))
    )
    client.set(mirror_api.workspace_mirror_key(base_key), serialize_workspace_mirror_meta(mirror))
    if use_full_snapshot:
        client.set(mirror_api.workspace_mirror_nodes_key(base_key), serialize_workspace_nodes(mirror.nodes))
        client.delete(mirror_api.workspace_mirror_dirty_nodes_key(base_key))
    elif dirty_node_ids:
        dirty_nodes = {
            int(node_id): mirror.nodes[int(node_id)]
            for node_id in dirty_node_ids
            if int(node_id) in mirror.nodes
        }
        client.set(mirror_api.workspace_mirror_dirty_nodes_key(base_key), serialize_workspace_nodes(dirty_nodes))
    if mirror_api.mirror_has_dirty_state(mirror):
        client.sadd(mirror_api.workspace_dirty_set_key(), base_key)
        mirror_api.enqueue_workspace_checkpoint(base_key)
    else:
        client.srem(mirror_api.workspace_dirty_set_key(), base_key)


def delete_workspace_mirror(workspace_id: int, tenant_id: str | None = None, scope_key: str | None = None) -> None:
    from iruka_vfs import service
    from iruka_vfs import workspace_mirror as mirror_api

    client = service._get_redis_client()
    tenant_key = mirror_api.effective_tenant_key(tenant_id)
    resolved_scope_key = mirror_api.effective_workspace_scope(scope_key)
    base_key = client.get(mirror_api.workspace_index_key(tenant_key, workspace_id, resolved_scope_key))
    if not base_key:
        return
    client.delete(mirror_api.workspace_mirror_key(base_key))
    client.delete(mirror_api.workspace_mirror_nodes_key(base_key))
    client.delete(mirror_api.workspace_mirror_dirty_nodes_key(base_key))
    client.delete(mirror_api.workspace_error_key(base_key))
    client.delete(mirror_api.workspace_due_key(base_key))
    client.delete(mirror_api.workspace_index_key(tenant_key, workspace_id, resolved_scope_key))
    client.srem(mirror_api.workspace_dirty_set_key(), base_key)
    client.srem(mirror_api.workspace_enqueued_key(), base_key)


def workspace_lock(
    mirror: WorkspaceMirror | None = None,
    *,
    workspace_id: int | None = None,
) -> tuple[redis.lock.Lock, str]:
    from iruka_vfs import service
    from iruka_vfs import workspace_mirror as mirror_api

    client = service._get_redis_client()
    if mirror is not None:
        base_key = mirror_api.workspace_base_key(mirror.tenant_key, mirror.workspace_id, mirror.scope_key)
    elif workspace_id is not None:
        base_key = str(
            client.get(mirror_api.workspace_index_key(mirror_api.effective_tenant_key(), workspace_id, mirror_api.effective_workspace_scope())) or ""
        )
        if not base_key:
            raise ValueError(f"workspace mirror index missing: {workspace_id}")
    else:
        raise ValueError("missing mirror or workspace_id")
    return client.lock(mirror_api.workspace_lock_key(base_key), timeout=30, blocking_timeout=5), base_key


def clone_node(node):
    from iruka_vfs import workspace_mirror as mirror_api

    return mirror_api.VirtualFileNode(
        id=int(node.id or 0),
        tenant_id=str(getattr(node, "tenant_id", "") or mirror_api.effective_tenant_key()),
        workspace_id=int(node.workspace_id),
        parent_id=int(node.parent_id) if node.parent_id is not None else None,
        name=str(node.name or ""),
        node_type=str(node.node_type or "file"),
        content_text=str(node.content_text or ""),
        version_no=int(node.version_no or 1),
        created_at=node.created_at,
        updated_at=node.updated_at,
    )
