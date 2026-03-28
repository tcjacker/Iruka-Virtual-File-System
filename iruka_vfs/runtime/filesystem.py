from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from iruka_vfs.memory_cache import cache_metric_inc as _cache_metric_inc
from iruka_vfs.memory_cache import update_cache_after_write as _update_cache_after_write


def get_or_create_session(db: Session, workspace_id: int):
    from iruka_vfs import service

    tenant_key = service._effective_tenant_key()
    mirror = service._get_workspace_mirror(workspace_id, tenant_key=tenant_key)
    if mirror:
        with mirror.lock:
            return service.VirtualShellSession(
                id=int(mirror.session_id),
                tenant_id=tenant_key,
                workspace_id=int(mirror.workspace_id),
                cwd_node_id=int(mirror.cwd_node_id),
                env_json={"PWD": service.VFS_ROOT},
                status="active",
            )
    session = service._repositories.session.get_active_session(db, workspace_id, tenant_key)
    if session:
        return session

    root = get_or_create_root(db, workspace_id)
    workspace_dir = get_or_create_child_dir(db, workspace_id, root.id, "workspace")
    return service._repositories.session.create_session(
        db,
        tenant_key=tenant_key,
        workspace_id=workspace_id,
        cwd_node_id=workspace_dir.id,
        env_json={"PWD": "/workspace"},
        status="active",
    )


def get_or_create_root(db: Session, workspace_id: int):
    from iruka_vfs import service

    tenant_key = service._effective_tenant_key()
    root = service._repositories.node.get_root(db, workspace_id, tenant_key)
    if root:
        return root
    return service._repositories.node.create_node(
        db,
        tenant_key=tenant_key,
        workspace_id=workspace_id,
        parent_id=None,
        name="",
        node_type="dir",
        content_text="",
        version_no=1,
    )


def get_or_create_child_dir(db: Session, workspace_id: int, parent_id: int, name: str):
    from iruka_vfs import service

    tenant_key = service._effective_tenant_key()
    mirror_node = service._mutate_workspace_mirror(
        workspace_id,
        tenant_key=tenant_key,
        mutate=lambda mirror: _mutate_get_or_create_child_dir(service, mirror, tenant_key, workspace_id, parent_id, name),
    )
    if mirror_node is not None:
        return mirror_node
    node = service._repositories.node.get_child(
        db,
        tenant_key=tenant_key,
        workspace_id=workspace_id,
        parent_id=parent_id,
        name=name,
        node_type="dir",
    )
    if node:
        return node
    return service._repositories.node.create_node(
        db,
        tenant_key=tenant_key,
        workspace_id=workspace_id,
        parent_id=parent_id,
        name=name,
        node_type="dir",
        content_text="",
        version_no=1,
    )


def _mutate_get_or_create_child_dir(service, mirror, tenant_key: str, workspace_id: int, parent_id: int, name: str):
    parent = mirror.nodes.get(parent_id)
    if not parent or parent.node_type != "dir":
        raise ValueError(f"invalid virtual parent: {parent_id}")
    parent_path = service._mirror_node_path_locked(mirror, parent)
    target_path = f"{parent_path.rstrip('/')}/{name}" if parent_path != "/" else f"/{name}"
    existing_id = mirror.path_to_id.get(target_path)
    if existing_id is not None:
        return mirror.nodes[existing_id], False
    node = service.VirtualFileNode(
        id=mirror.next_temp_id,
        tenant_id=tenant_key,
        workspace_id=workspace_id,
        parent_id=parent_id,
        name=name,
        node_type="dir",
        content_text="",
        version_no=1,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    mirror.next_temp_id -= 1
    mirror.nodes[int(node.id)] = node
    mirror.children_by_parent.setdefault(parent_id, []).append(int(node.id))
    service._ensure_children_sorted_locked(mirror, parent_id)
    mirror.path_to_id[target_path] = int(node.id)
    mirror.dirty_structure_node_ids.add(int(node.id))
    mirror.revision += 1
    _cache_metric_inc("write_ops")
    return node, True


def mkdir_parents(db: Session, session, raw_path: str) -> str:
    from iruka_vfs import service

    if raw_path.startswith("/"):
        current = get_or_create_root(db, session.workspace_id)
        parts = [item for item in raw_path.split("/") if item]
    else:
        current = must_get_node(db, session.cwd_node_id)
        parts = [item for item in raw_path.split("/") if item]
    if not parts:
        raise ValueError("mkdir: invalid path")
    for part in parts:
        if part in {".", ""}:
            continue
        if part == "..":
            if current.parent_id is not None:
                current = must_get_node(db, int(current.parent_id))
            continue
        child = service._resolve_path(db, session.workspace_id, int(current.id), part)
        if child:
            if child.node_type != "dir":
                raise ValueError(f"mkdir: cannot create directory '{raw_path}': File exists")
            current = child
            continue
        current = get_or_create_child_dir(db, session.workspace_id, int(current.id), part)
    return service._node_path(db, current)


def get_or_create_child_file(db: Session, workspace_id: int, parent_id: int, name: str, content: str):
    from iruka_vfs import service

    tenant_key = service._effective_tenant_key()
    mirror_node = service._mutate_workspace_mirror(
        workspace_id,
        tenant_key=tenant_key,
        mutate=lambda mirror: _mutate_get_or_create_child_file(
            service,
            mirror,
            tenant_key,
            workspace_id,
            parent_id,
            name,
            content,
        ),
    )
    if mirror_node is not None:
        return mirror_node
    node = service._repositories.node.get_child(
        db,
        tenant_key=tenant_key,
        workspace_id=workspace_id,
        parent_id=parent_id,
        name=name,
        node_type="file",
    )
    if node:
        return node
    return service._repositories.node.create_node(
        db,
        tenant_key=tenant_key,
        workspace_id=workspace_id,
        parent_id=parent_id,
        name=name,
        node_type="file",
        content_text=content,
        version_no=1,
    )


def _mutate_get_or_create_child_file(
    service,
    mirror,
    tenant_key: str,
    workspace_id: int,
    parent_id: int,
    name: str,
    content: str,
):
    parent = mirror.nodes.get(parent_id)
    if not parent or parent.node_type != "dir":
        raise ValueError(f"invalid virtual parent: {parent_id}")
    parent_path = service._mirror_node_path_locked(mirror, parent)
    target_path = f"{parent_path.rstrip('/')}/{name}" if parent_path != "/" else f"/{name}"
    existing_id = mirror.path_to_id.get(target_path)
    if existing_id is not None:
        return mirror.nodes[existing_id], False
    node = service.VirtualFileNode(
        id=mirror.next_temp_id,
        tenant_id=tenant_key,
        workspace_id=workspace_id,
        parent_id=parent_id,
        name=name,
        node_type="file",
        content_text=content,
        version_no=1,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    mirror.next_temp_id -= 1
    mirror.nodes[int(node.id)] = node
    mirror.children_by_parent.setdefault(parent_id, []).append(int(node.id))
    service._ensure_children_sorted_locked(mirror, parent_id)
    mirror.path_to_id[target_path] = int(node.id)
    mirror.dirty_structure_node_ids.add(int(node.id))
    mirror.revision += 1
    _cache_metric_inc("write_ops")
    return node, True


def write_file(db: Session, node, content: str, *, op: str) -> int:
    from iruka_vfs import service

    next_version = service._mutate_workspace_mirror(
        int(node.workspace_id),
        tenant_key=getattr(node, "tenant_id", None),
        mutate=lambda mirror: _mutate_write_file(service, mirror, node, content),
    )
    if next_version is not None:
        return int(next_version)
    if not service.MEMORY_CACHE_ENABLED:
        base_version = node.version_no
        next_version = base_version + 1
        node.content_text = content
        node.version_no = next_version
        node.updated_at = datetime.utcnow()
        service._repositories.node.touch_node(db, node=node)
        return int(next_version)

    next_version = _update_cache_after_write(node, content, op=op)
    return int(next_version)


def _mutate_write_file(service, mirror, node, content: str):
    mirror_node = mirror.nodes.get(int(node.id))
    if mirror_node is None:
        mirror.nodes[int(node.id)] = node
        mirror_node = node
        service._rebuild_workspace_mirror_indexes_locked(mirror)
    base_version = int(mirror_node.version_no or 1)
    next_version = base_version + 1
    mirror_node.content_text = content
    mirror_node.version_no = next_version
    mirror_node.updated_at = datetime.utcnow()
    mirror.dirty_content_node_ids.add(int(mirror_node.id))
    mirror.revision += 1
    _cache_metric_inc("write_ops")
    return next_version, True


def move_node(db: Session, node, *, parent_id: int, name: str) -> int:
    from iruka_vfs import service

    next_version = service._mutate_workspace_mirror(
        int(node.workspace_id),
        tenant_key=getattr(node, "tenant_id", None),
        mutate=lambda mirror: _mutate_move_node(service, mirror, node, parent_id=parent_id, name=name),
    )
    if next_version is not None:
        _persist_node_metadata(db, node, parent_id=parent_id, name=name, version_no=next_version)
        return int(next_version)
    next_version = int(getattr(node, "version_no", 1) or 1) + 1
    _persist_node_metadata(db, node, parent_id=parent_id, name=name, version_no=next_version)
    return int(next_version)


def _mutate_move_node(service, mirror, node, *, parent_id: int, name: str):
    mirror_node = mirror.nodes.get(int(node.id))
    if mirror_node is None:
        mirror.nodes[int(node.id)] = node
        mirror_node = node
        service._rebuild_workspace_mirror_indexes_locked(mirror)
    mirror_node.parent_id = int(parent_id)
    mirror_node.name = str(name)
    mirror_node.version_no = int(mirror_node.version_no or 1) + 1
    mirror_node.updated_at = datetime.utcnow()
    mirror.dirty_structure_node_ids.add(int(mirror_node.id))
    mirror.revision += 1
    service._rebuild_workspace_mirror_indexes_locked(mirror)
    _cache_metric_inc("write_ops")
    return int(mirror_node.version_no), True


def delete_node(db: Session, node) -> None:
    from iruka_vfs import service

    tenant_key = getattr(node, "tenant_id", None)
    service._mutate_workspace_mirror(
        int(node.workspace_id),
        tenant_key=tenant_key,
        mutate=lambda mirror: _mutate_delete_node(service, mirror, node),
    )
    _persist_delete_node(db, node)


def _mutate_delete_node(service, mirror, node):
    node_id = int(node.id)
    mirror_node = mirror.nodes.pop(node_id, None)
    if mirror_node is None:
        return False, False
    parent_id = int(mirror_node.parent_id) if mirror_node.parent_id is not None else None
    if parent_id is not None:
        children = mirror.children_by_parent.get(parent_id, [])
        mirror.children_by_parent[parent_id] = [child_id for child_id in children if int(child_id) != node_id]
    mirror.dirty_structure_node_ids.discard(node_id)
    mirror.dirty_content_node_ids.discard(node_id)
    service._rebuild_workspace_mirror_indexes_locked(mirror)
    mirror.revision += 1
    _cache_metric_inc("write_ops")
    return True, True


def _persist_node_metadata(db: Session, node, *, parent_id: int, name: str, version_no: int) -> None:
    from iruka_vfs import service

    tenant_key = service._effective_tenant_key(getattr(node, "tenant_id", None))
    service._repositories.node.update_node_content(
        db,
        node_id=int(node.id),
        tenant_key=tenant_key,
        parent_id=int(parent_id),
        name=str(name),
        node_type=str(getattr(node, "node_type", "file") or "file"),
        content_text=str(getattr(node, "content_text", "") or ""),
        version_no=int(version_no),
    )


def _persist_delete_node(db: Session, node) -> None:
    from iruka_vfs import service

    repository = service._repositories.node
    if hasattr(repository, "state"):
        repository.state.nodes.pop(int(node.id), None)
        return
    if db is not None:
        db.delete(node)
        db.flush()


def must_get_node(db: Session, node_id: int | None):
    from iruka_vfs import service

    if not node_id:
        raise ValueError("missing virtual node id")
    from iruka_vfs.service_ops.state import workspace_state_uses_redis

    if not workspace_state_uses_redis():
        active = service._active_workspace_mirror()
        mirrors = [active] if active else []
        for mirror in mirrors:
            with mirror.lock:
                node = mirror.nodes.get(int(node_id))
                if node:
                    return node
    tenant_key = service._effective_tenant_key()
    node = service._repositories.node.get_node(db, node_id, tenant_key)
    if not node:
        raise ValueError(f"virtual node not found: {node_id}")
    return node
