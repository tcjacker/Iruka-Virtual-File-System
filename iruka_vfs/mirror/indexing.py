from __future__ import annotations

from sqlalchemy.orm import Session

from iruka_vfs.models import WorkspaceMirror


def ensure_children_sorted_locked(mirror: WorkspaceMirror, parent_id: int | None) -> None:
    child_ids = mirror.children_by_parent.get(parent_id, [])
    child_ids.sort(key=lambda node_id: (mirror.nodes[node_id].node_type, mirror.nodes[node_id].name))


def mirror_node_path_locked(mirror: WorkspaceMirror, node) -> str:
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
    workspace,
    *,
    session,
) -> WorkspaceMirror:
    from iruka_vfs import workspace_mirror as mirror_api

    nodes = mirror_api._repositories.node.list_workspace_nodes(db, workspace.id, mirror_api.workspace_tenant_key(workspace))
    cloned: dict[int, object] = {}
    for source in nodes:
        cloned_node = mirror_api.clone_node(source)
        cloned[int(cloned_node.id)] = cloned_node
    root = next((node for node in cloned.values() if node.parent_id is None), None)
    if root is None:
        raise ValueError(f"virtual root not found for workspace {workspace.id}")
    mirror = WorkspaceMirror(
        tenant_key=mirror_api.workspace_tenant_key(workspace),
        scope_key=mirror_api.workspace_scope_for_db(db),
        workspace_id=int(workspace.id),
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

