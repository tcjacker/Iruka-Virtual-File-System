from __future__ import annotations

import fnmatch
import re
import shlex

from sqlalchemy.orm import Session

from iruka_vfs.constants import REGEX_META_CHARS
from iruka_vfs.models import VirtualCommandResult


def _search_hint(target: str) -> str:
    basename = str(target or "").rstrip("/").split("/")[-1]
    if basename and basename not in {".", ".."}:
        return f" Try: find /workspace -name {shlex.quote(basename)} or tree"
    return " Try: ls -la /workspace or tree"


def format_missing_path_error(command: str, target: str, *, directory_style: bool = False) -> str:
    suffix = "No such file or directory" if directory_style else "No such file"
    return f"{command}: {target}: {suffix}.{_search_hint(target)}"


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


def exec_find(db: Session, session, args: list[str]) -> VirtualCommandResult:
    from iruka_vfs import service

    target = "."
    name_pattern: str | None = None
    type_filter: str | None = None
    positional: list[str] = []
    idx = 0

    while idx < len(args):
        token = args[idx]
        if token == "-name":
            if idx + 1 >= len(args):
                return VirtualCommandResult("", "find: missing pattern after -name", 1, {})
            name_pattern = args[idx + 1]
            idx += 2
            continue
        if token == "-type":
            if idx + 1 >= len(args):
                return VirtualCommandResult("", "find: missing type after -type", 1, {})
            raw_type = args[idx + 1]
            if raw_type not in {"f", "d"}:
                return VirtualCommandResult("", f"find: unsupported -type value: {raw_type}", 1, {})
            type_filter = "file" if raw_type == "f" else "dir"
            idx += 2
            continue
        if token.startswith("-"):
            return VirtualCommandResult("", f"find: unsupported option: {token}", 1, {})
        positional.append(token)
        idx += 1

    if positional:
        if len(positional) > 1:
            return VirtualCommandResult("", "find: only a single search root is supported", 1, {})
        target = positional[0]

    node = service._resolve_path(db, session.workspace_id, session.cwd_node_id, target)
    if not node:
        return VirtualCommandResult("", format_missing_path_error("find", target, directory_style=True), 1, {})

    matches = find_paths(db, session.workspace_id, node, name_pattern=name_pattern, node_type=type_filter)
    if not matches:
        return VirtualCommandResult(
            "",
            "",
            1,
            {
                "path": service._node_path(db, node),
                "match_count": 0,
                "name_pattern": name_pattern,
                "type_filter": type_filter,
            },
        )
    return VirtualCommandResult(
        "\n".join(matches),
        "",
        0,
        {
            "path": service._node_path(db, node),
            "match_count": len(matches),
            "name_pattern": name_pattern,
            "type_filter": type_filter,
        },
    )


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


def find_paths(
    db: Session,
    workspace_id: int,
    node,
    *,
    name_pattern: str | None = None,
    node_type: str | None = None,
) -> list[str]:
    from iruka_vfs import service

    mirror = service._get_workspace_mirror(workspace_id, tenant_key=getattr(node, "tenant_id", None))
    if mirror:
        with mirror.lock:
            out: list[str] = []
            start = mirror.nodes.get(int(node.id), node)
            _collect_find_paths_locked(service, mirror, start, out, name_pattern=name_pattern, node_type=node_type)
            return out

    out: list[str] = []
    _collect_find_paths(db, workspace_id, node, out, name_pattern=name_pattern, node_type=node_type)
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


def _collect_find_paths_locked(service, mirror, node, out: list[str], *, name_pattern: str | None, node_type: str | None) -> None:
    node_path = service._mirror_node_path_locked(mirror, node)
    if _find_node_matches(node, name_pattern=name_pattern, node_type=node_type):
        out.append(node_path)
    if node.node_type != "dir":
        return
    child_ids = sorted(mirror.children_by_parent.get(int(node.id), []), key=lambda child_id: str(mirror.nodes[child_id].name))
    for child_id in child_ids:
        child = mirror.nodes[child_id]
        _collect_find_paths_locked(service, mirror, child, out, name_pattern=name_pattern, node_type=node_type)


def _collect_find_paths(
    db: Session,
    workspace_id: int,
    node,
    out: list[str],
    *,
    name_pattern: str | None,
    node_type: str | None,
) -> None:
    from iruka_vfs import service

    node_path = service._node_path(db, node)
    if _find_node_matches(node, name_pattern=name_pattern, node_type=node_type):
        out.append(node_path)
    if node.node_type != "dir":
        return
    for child in service._list_children(db, workspace_id, node.id):
        _collect_find_paths(db, workspace_id, child, out, name_pattern=name_pattern, node_type=node_type)


def _find_node_matches(node, *, name_pattern: str | None, node_type: str | None) -> bool:
    if node_type and str(getattr(node, "node_type", "") or "") != node_type:
        return False
    if name_pattern is None:
        return True
    return fnmatch.fnmatchcase(str(getattr(node, "name", "") or ""), name_pattern)
