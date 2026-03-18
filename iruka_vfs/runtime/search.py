from __future__ import annotations

import re

from sqlalchemy.orm import Session

from iruka_vfs.constants import REGEX_META_CHARS


def safe_compile(pattern: str) -> re.Pattern[str] | None:
    try:
        return re.compile(pattern)
    except re.error:
        return None


def search_text_lines(text: str, pattern: str) -> list[str]:
    regex = safe_compile(pattern)
    matches: list[str] = []
    for line in text.splitlines():
        hit = bool(regex.search(line)) if regex else pattern.lower() in line.lower()
        if hit:
            matches.append(line)
    return matches


def search_nodes(db: Session, workspace_id: int, node, pattern: str) -> list[str]:
    from iruka_vfs import service

    regex = safe_compile(pattern)
    file_nodes = collect_files_for_search(db, workspace_id, node, pattern=pattern, regex=regex)
    matches: list[str] = []
    for item in file_nodes:
        content_text = service._get_node_content(db, item)
        for i, line in enumerate(content_text.splitlines(), start=1):
            hit = bool(regex.search(line)) if regex else pattern.lower() in line.lower()
            if hit:
                matches.append(f"{search_display_path(db, item)}:{i}:{line}")
    return matches


def search_display_path(db: Session, node) -> str:
    from iruka_vfs import service

    if node.parent_id is None and node.name.startswith("/"):
        return node.name
    return service._node_path(db, node)


def collect_files_for_search(
    db: Session,
    workspace_id: int,
    node,
    *,
    pattern: str,
    regex: re.Pattern[str] | None,
) -> list:
    from iruka_vfs import service

    mirror = service._get_workspace_mirror(workspace_id, tenant_key=getattr(node, "tenant_id", None))
    if mirror:
        with mirror.lock:
            if node.node_type == "file":
                return [mirror.nodes.get(int(node.id), node)]
            return collect_files(db, workspace_id, int(node.id))
    bind = db.get_bind()
    dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
    if dialect_name != "postgresql":
        return [node] if node.node_type == "file" else collect_files(db, workspace_id, node.id)

    base_path = service._node_path(db, node)
    root_id = int(node.id)
    tenant_key = service._effective_tenant_key(getattr(node, "tenant_id", None))

    rows = service._repositories.node.search_subtree_files(
        db,
        tenant_key=tenant_key,
        workspace_id=workspace_id,
        root_id=root_id,
        pattern=pattern,
        use_case_insensitive=regex is None,
        use_literal_case_sensitive=regex is not None and not REGEX_META_CHARS.search(pattern),
    )

    out: list = []
    for row in rows:
        rel_path = str(row["rel_path"] or "")
        virtual_path = base_path if not rel_path else f"{base_path.rstrip('/')}/{rel_path}" if base_path != "/" else f"/{rel_path}"
        out.append(
            service.VirtualFileNode(
                id=int(row["id"]),
                tenant_id=tenant_key,
                workspace_id=workspace_id,
                parent_id=None,
                name=virtual_path,
                node_type="file",
                content_text=str(row["content_text"] or ""),
                version_no=1,
            )
        )
    return out


def collect_files(db: Session, workspace_id: int, parent_id: int) -> list:
    from iruka_vfs import service

    mirror = service._get_workspace_mirror(workspace_id)
    if mirror:
        with mirror.lock:
            out: list = []
            stack = [parent_id]
            while stack:
                node_id = stack.pop()
                for child_id in mirror.children_by_parent.get(node_id, []):
                    child = mirror.nodes[child_id]
                    if child.node_type == "file":
                        out.append(child)
                    else:
                        stack.append(child.id)
            return out
    out: list = []
    stack = [parent_id]
    while stack:
        node_id = stack.pop()
        children = service._list_children(db, workspace_id, node_id)
        for child in children:
            if child.node_type == "file":
                out.append(child)
            else:
                stack.append(child.id)
    return out
