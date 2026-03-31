from __future__ import annotations

from sqlalchemy.orm import Session


def render_tree_lines(
    db: Session,
    workspace_id: int,
    parent_id: int,
    prefix: str,
    out: list[str],
    *,
    depth: int = 0,
    max_depth: int | None = None,
) -> None:
    from iruka_vfs import service

    if max_depth is not None and depth >= max_depth:
        return
    children = service._list_children(db, workspace_id, parent_id)
    for idx, node in enumerate(children):
        last = idx == len(children) - 1
        branch = "└── " if last else "├── "
        label = f"{node.name}/" if node.node_type == "dir" else node.name
        out.append(f"{prefix}{branch}{label}")
        if node.node_type == "dir":
            render_tree_lines(
                db,
                workspace_id,
                node.id,
                f"{prefix}{'    ' if last else '│   '}",
                out,
                depth=depth + 1,
                max_depth=max_depth,
            )


def render_virtual_tree(db: Session, workspace_id: int, *, max_depth: int | None = None) -> str:
    from iruka_vfs import service

    root = service._get_or_create_root(db, workspace_id)
    lines = ["/"]
    render_tree_lines(db, workspace_id, root.id, prefix="", out=lines, depth=0, max_depth=max_depth)
    return "\n".join(lines)
