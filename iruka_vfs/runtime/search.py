from __future__ import annotations

import fnmatch
import re
import shlex

from sqlalchemy.orm import Session

from iruka_vfs.constants import REGEX_META_CHARS, VFS_ROOT
from iruka_vfs.models import VirtualCommandResult


def _search_hint(target: str, *, command: str | None = None, db: Session | None = None, session=None, directory_style: bool = False) -> str:
    basename = str(target or "").rstrip("/").split("/")[-1]
    suggested_path = _find_unique_named_path(db, session, basename, raw_target=target)
    if suggested_path:
        if command in {"cat", "edit", "patch", "wc"} and not directory_style:
            return (
                f" Most likely existing path: {suggested_path}. "
                f"Try: {command} {shlex.quote(suggested_path)}. "
                f"Do not recreate /workspace/{basename} when that exact file already exists elsewhere."
            )
        return (
            f" Most likely existing path: {suggested_path}. "
            f"Try: find /workspace -name {shlex.quote(basename)} or use the exact path above."
        )
    if basename and basename not in {".", ".."}:
        return f" Try: find /workspace -name {shlex.quote(basename)} -> cat -> edit/patch, or inspect tree"
    return " Try: ls -la /workspace, find /workspace -type f, or tree"


def _find_unique_named_path(db: Session | None, session, basename: str, *, raw_target: str) -> str | None:
    from iruka_vfs import service

    if db is None or session is None or not basename or basename in {".", ".."}:
        return None
    workspace_root = service._resolve_path(db, session.workspace_id, session.cwd_node_id, VFS_ROOT)
    if workspace_root is None:
        return None
    normalized_target = service._normalize_virtual_path(db, session, raw_target) or raw_target
    same_name_paths = [
        path
        for path in find_paths(db, session.workspace_id, workspace_root, name_pattern=basename)
        if path != normalized_target
    ]
    if len(same_name_paths) != 1:
        return None
    return same_name_paths[0]


def format_missing_path_error(command: str, target: str, *, directory_style: bool = False, db: Session | None = None, session=None) -> str:
    suffix = "No such file or directory" if directory_style else "No such file"
    return f"{command}: {target}: {suffix}.{_search_hint(target, command=command, db=db, session=session, directory_style=directory_style)}"


def safe_compile(pattern: str) -> re.Pattern[str] | None:
    try:
        return re.compile(pattern)
    except re.error:
        return None


def _line_matches(line: str, pattern: str, regex: re.Pattern[str] | None, *, invert_match: bool) -> bool:
    hit = bool(regex.search(line)) if regex else pattern.lower() in line.lower()
    return not hit if invert_match else hit


def search_text_lines(text: str, pattern: str, *, invert_match: bool = False) -> list[str]:
    regex = safe_compile(pattern)
    matches: list[str] = []
    for line in text.splitlines():
        if _line_matches(line, pattern, regex, invert_match=invert_match):
            matches.append(line)
    return matches


def count_text_matches(text: str, pattern: str, *, invert_match: bool = False) -> int:
    return len(search_text_lines(text, pattern, invert_match=invert_match))


def search_nodes(db: Session, workspace_id: int, node, pattern: str, *, invert_match: bool = False) -> list[str]:
    from iruka_vfs import service

    regex = safe_compile(pattern)
    file_nodes = collect_files_for_search(db, workspace_id, node, pattern=pattern, regex=regex)
    matches: list[str] = []
    for item in file_nodes:
        content_text = service._get_node_content(db, item)
        for i, line in enumerate(content_text.splitlines(), start=1):
            if _line_matches(line, pattern, regex, invert_match=invert_match):
                matches.append(f"{search_display_path(db, item)}:{i}:{line}")
    return matches


def search_matching_file_paths(db: Session, workspace_id: int, node, pattern: str, *, invert_match: bool = False) -> list[str]:
    from iruka_vfs import service

    regex = safe_compile(pattern)
    file_nodes = collect_files_for_search(db, workspace_id, node, pattern=pattern, regex=regex)
    matches: list[str] = []
    for item in file_nodes:
        content_text = service._get_node_content(db, item)
        file_hit = False
        for line in content_text.splitlines():
            if _line_matches(line, pattern, regex, invert_match=invert_match):
                file_hit = True
                break
        if file_hit:
            matches.append(search_display_path(db, item))
    return matches


def search_match_counts(db: Session, workspace_id: int, node, pattern: str, *, invert_match: bool = False) -> list[tuple[str, int]]:
    from iruka_vfs import service

    regex = safe_compile(pattern)
    file_nodes = collect_files_for_search(db, workspace_id, node, pattern=pattern, regex=regex)
    counts: list[tuple[str, int]] = []
    for item in file_nodes:
        content_text = service._get_node_content(db, item)
        match_count = 0
        for line in content_text.splitlines():
            if _line_matches(line, pattern, regex, invert_match=invert_match):
                match_count += 1
        if match_count:
            counts.append((search_display_path(db, item), match_count))
    return counts


def search_display_path(db: Session, node) -> str:
    from iruka_vfs import service

    if node.parent_id is None and node.name.startswith("/"):
        return node.name
    return service._node_path(db, node)


def exec_find(db: Session, session, args: list[str]) -> VirtualCommandResult:
    from iruka_vfs import service

    target = "."
    name_patterns: list[str] = []
    type_filter: str | None = None
    exec_tokens: list[str] | None = None
    positional: list[str] = []
    idx = 0

    while idx < len(args):
        token = args[idx]
        if token == "-name":
            if idx + 1 >= len(args):
                return VirtualCommandResult("", "find: missing pattern after -name", 1, {})
            name_patterns.append(args[idx + 1])
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
        if token == "-exec":
            if exec_tokens is not None:
                return VirtualCommandResult("", "find: only a single -exec clause is supported", 1, {})
            end_idx = idx + 1
            while end_idx < len(args) and args[end_idx] != ";":
                end_idx += 1
            if end_idx >= len(args):
                return VirtualCommandResult(
                    "",
                    "find: missing ';' terminator for -exec. Example: find /workspace -type f -exec grep -l TODO {} \\;",
                    1,
                    {},
                )
            exec_tokens = args[idx + 1 : end_idx]
            if not exec_tokens:
                return VirtualCommandResult("", "find: missing command after -exec", 1, {})
            idx = end_idx + 1
            continue
        if token in {"(", ")"}:
            idx += 1
            continue
        if token == "-o":
            idx += 1
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
        return VirtualCommandResult("", format_missing_path_error("find", target, directory_style=True, db=db, session=session), 1, {})

    matches = _resolve_find_matches(
        db,
        session,
        node,
        name_patterns=name_patterns,
        type_filter=type_filter,
    )
    if exec_tokens is not None:
        return _run_find_exec(
            db,
            session,
            matches,
            exec_tokens,
            root_path=service._node_path(db, node),
            name_patterns=name_patterns,
            type_filter=type_filter,
        )
    if not matches:
        return VirtualCommandResult(
            "",
            "",
            1,
            {
                "path": service._node_path(db, node),
                "match_count": 0,
                "name_patterns": list(name_patterns),
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
            "name_patterns": list(name_patterns),
            "type_filter": type_filter,
        },
    )


def _resolve_find_matches(db: Session, session, node, *, name_patterns: list[str], type_filter: str | None) -> list[str]:
    candidates: list[str] = []
    if not name_patterns:
        return find_paths(db, session.workspace_id, node, node_type=type_filter)
    seen: set[str] = set()
    for pattern in name_patterns:
        for path in find_paths(db, session.workspace_id, node, name_pattern=pattern, node_type=type_filter):
            if path in seen:
                continue
            seen.add(path)
            candidates.append(path)
    candidates.sort()
    return candidates


def _run_find_exec(
    db: Session,
    session,
    matches: list[str],
    exec_tokens: list[str],
    *,
    root_path: str,
    name_patterns: list[str],
    type_filter: str | None,
) -> VirtualCommandResult:
    from iruka_vfs import service

    if "{}" not in exec_tokens:
        return VirtualCommandResult("", "find: -exec command must include {}", 1, {})

    stdout_lines: list[str] = []
    nested_results: list[dict[str, object]] = []
    for matched_path in matches:
        argv = [matched_path if token == "{}" else token for token in exec_tokens]
        result = service._exec_argv(db, session, argv, input_text="")
        nested_results.append({"argv": argv, "exit_code": result.exit_code, "artifacts": result.artifacts})
        if result.exit_code == 0 and result.stdout:
            stdout_lines.extend(line for line in result.stdout.splitlines() if line)
            continue
        if result.exit_code == 1 and argv and argv[0] in {"grep", "rg"}:
            continue
        return VirtualCommandResult(
            result.stdout,
            result.stderr,
            result.exit_code,
            {
                "path": root_path,
                "match_count": len(matches),
                "name_patterns": list(name_patterns),
                "type_filter": type_filter,
                "exec": exec_tokens,
                "exec_results": nested_results,
            },
        )

    deduped: list[str] = []
    seen_stdout: set[str] = set()
    for line in stdout_lines:
        if line in seen_stdout:
            continue
        seen_stdout.add(line)
        deduped.append(line)
    exit_code = 0 if deduped or matches else 0
    return VirtualCommandResult(
        "\n".join(deduped),
        "",
        exit_code,
        {
            "path": root_path,
            "match_count": len(matches),
            "name_patterns": list(name_patterns),
            "type_filter": type_filter,
            "exec": exec_tokens,
            "exec_results": nested_results,
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
