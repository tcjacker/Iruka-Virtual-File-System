from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from iruka_vfs.dependency_resolution import resolve_vfs_repositories
from iruka_vfs.dependencies import get_vfs_dependencies

_dependencies = get_vfs_dependencies()
VirtualFileNode = _dependencies.VirtualFileNode


def resolve_parent_for_create(
    db: Session,
    workspace_id: int,
    cwd_node_id: int,
    raw_path: str,
) -> tuple[VirtualFileNode | None, str]:
    from iruka_vfs import service

    cleaned = raw_path.rstrip("/")
    if not cleaned:
        return None, ""
    parent_path, _, leaf = cleaned.rpartition("/")
    if not leaf:
        return None, ""
    if not parent_path:
        base = "/" if raw_path.startswith("/") else "."
        parent = service._resolve_path(db, workspace_id, cwd_node_id, base)
        return parent, leaf
    parent = service._resolve_path(db, workspace_id, cwd_node_id, parent_path)
    if not parent or parent.node_type != "dir":
        return None, leaf
    return parent, leaf


def resolve_path(db: Session, workspace_id: int, cwd_node_id: int, path: str) -> VirtualFileNode | None:
    from iruka_vfs import service

    tenant_key = service._effective_tenant_key()
    mirror = service._get_workspace_mirror(workspace_id, tenant_key=tenant_key)
    if mirror:
        with mirror.lock:
            if not path:
                return None
            if path == "/":
                return mirror.nodes.get(mirror.root_id)
            if path.startswith("/"):
                current = mirror.nodes.get(mirror.root_id)
                parts = [item for item in path.split("/") if item]
            else:
                current = mirror.nodes.get(cwd_node_id)
                parts = [item for item in path.split("/") if item]
            if not current:
                return None
            for part in parts:
                if part == ".":
                    continue
                if part == "..":
                    if current.parent_id is None:
                        continue
                    parent = mirror.nodes.get(current.parent_id)
                    if not parent:
                        return None
                    current = parent
                    continue
                child = next(
                    (
                        mirror.nodes[child_id]
                        for child_id in mirror.children_by_parent.get(int(current.id), [])
                        if mirror.nodes[child_id].name == part
                    ),
                    None,
                )
                if not child:
                    return None
                current = child
            return current
    if not path:
        return None
    if path == "/":
        return service._get_or_create_root(db, workspace_id)

    if path.startswith("/"):
        current = service._get_or_create_root(db, workspace_id)
        parts = [item for item in path.split("/") if item]
    else:
        current = service._must_get_node(db, cwd_node_id)
        parts = [item for item in path.split("/") if item]

    repositories = resolve_vfs_repositories()

    for part in parts:
        if part == ".":
            continue
        if part == "..":
            if current.parent_id is None:
                continue
            if db is None:
                parent = repositories.node.get_node(db, int(current.parent_id), tenant_key)
            else:
                parent = db.scalars(
                    select(VirtualFileNode).where(
                        VirtualFileNode.tenant_id == tenant_key,
                        VirtualFileNode.id == current.parent_id,
                    )
                ).first()
            if not parent:
                return None
            current = parent
            continue

        if db is None:
            child = repositories.node.get_child(
                db,
                tenant_key=tenant_key,
                workspace_id=workspace_id,
                parent_id=int(current.id),
                name=part,
                node_type="dir",
            ) or repositories.node.get_child(
                db,
                tenant_key=tenant_key,
                workspace_id=workspace_id,
                parent_id=int(current.id),
                name=part,
                node_type="file",
            )
        else:
            child = db.scalars(
                select(VirtualFileNode).where(
                    VirtualFileNode.tenant_id == tenant_key,
                    VirtualFileNode.workspace_id == workspace_id,
                    VirtualFileNode.parent_id == current.id,
                    VirtualFileNode.name == part,
                )
            ).first()
        if not child:
            return None
        current = child

    return current


def list_children(db: Session, workspace_id: int, parent_id: int) -> list[VirtualFileNode]:
    from iruka_vfs import service

    tenant_key = service._effective_tenant_key()
    mirror = service._get_workspace_mirror(workspace_id, tenant_key=tenant_key)
    if mirror:
        with mirror.lock:
            return [mirror.nodes[child_id] for child_id in mirror.children_by_parent.get(parent_id, [])]
    if db is None:
        return [
            node
            for node in resolve_vfs_repositories().node.list_workspace_nodes(db, workspace_id, tenant_key)
            if int(getattr(node, "parent_id", -1) or -1) == int(parent_id)
        ]
    return db.scalars(
        select(VirtualFileNode)
        .where(
            VirtualFileNode.tenant_id == tenant_key,
            VirtualFileNode.workspace_id == workspace_id,
            VirtualFileNode.parent_id == parent_id,
        )
        .order_by(VirtualFileNode.node_type.asc(), VirtualFileNode.name.asc())
    ).all()


def node_path(db: Session, node: VirtualFileNode) -> str:
    from iruka_vfs import service

    tenant_key = service._effective_tenant_key(getattr(node, "tenant_id", None))
    mirror = service._get_workspace_mirror(int(node.workspace_id), tenant_key=tenant_key)
    if mirror:
        with mirror.lock:
            mirror_node = mirror.nodes.get(int(node.id), node)
            return service._mirror_node_path_locked(mirror, mirror_node)
    if node.parent_id is None:
        return "/"
    names = [node.name]
    parent_id = node.parent_id
    repositories = resolve_vfs_repositories()
    while parent_id is not None:
        if db is None:
            parent = repositories.node.get_node(db, int(parent_id), tenant_key)
        else:
            parent = db.scalars(
                select(VirtualFileNode).where(
                    VirtualFileNode.tenant_id == tenant_key,
                    VirtualFileNode.id == parent_id,
                )
            ).first()
        if not parent:
            break
        if parent.parent_id is None:
            break
        names.append(parent.name)
        parent_id = parent.parent_id
    return "/" + "/".join(reversed(names))
