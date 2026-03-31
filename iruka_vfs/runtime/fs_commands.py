from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath

from sqlalchemy.orm import Session

from iruka_vfs.models import VirtualCommandResult


def exec_touch(db: Session, session, args: list[str]) -> VirtualCommandResult:
    from iruka_vfs import service

    if not args:
        return VirtualCommandResult("", "touch: missing file operand", 1, {})

    created: list[str] = []
    existing: list[str] = []
    for raw_path in args:
        node = service._resolve_path(db, session.workspace_id, session.cwd_node_id, raw_path)
        resolved_target = service._resolve_target_path_for_write(db, session, raw_path, node=node)
        if not resolved_target:
            return VirtualCommandResult("", f"touch: cannot touch '{raw_path}': invalid parent path", 1, {})
        allowed, deny_reason = service._allow_write_path(db, session, resolved_target)
        if not allowed:
            return VirtualCommandResult("", f"touch: {deny_reason}", 1, {"path": resolved_target})
        conflict = service._detect_ambiguous_create_target(db, session, raw_path, node=node)
        if conflict is not None:
            return VirtualCommandResult(
                "",
                service._format_ambiguous_create_target_message(conflict, source="touch"),
                1,
                conflict,
            )
        if node:
            if node.node_type != "file":
                return VirtualCommandResult("", f"touch: cannot touch '{raw_path}': Not a file", 1, {})
            updated = service._mutate_workspace_mirror(
                session.workspace_id,
                tenant_key=getattr(session, "tenant_id", None),
                mutate=lambda mirror: _mutate_touch_existing_file(mirror, node),
            )
            if updated is None:
                node.updated_at = datetime.utcnow()
                service._repositories.node.touch_node(db, node=node)
            existing.append(service._node_path(db, node))
            continue

        parent, name = service._resolve_parent_for_create(db, session.workspace_id, session.cwd_node_id, raw_path)
        if not parent:
            return VirtualCommandResult("", f"touch: cannot touch '{raw_path}': invalid parent path", 1, {})
        file_node = service._get_or_create_child_file(db, session.workspace_id, parent.id, name, "")
        created.append(service._node_path(db, file_node))

    summary = []
    if created:
        summary.append(f"created={len(created)}")
    if existing:
        summary.append(f"existing={len(existing)}")
    return VirtualCommandResult("touch " + ", ".join(summary or ["ok"]), "", 0, {"created": created, "existing": existing})


def _mutate_touch_existing_file(mirror, node):
    mirror_node = mirror.nodes.get(int(node.id), node)
    mirror_node.updated_at = datetime.utcnow()
    mirror.dirty_content_node_ids.add(int(mirror_node.id))
    mirror.revision += 1
    return True, True


def exec_mkdir(db: Session, session, args: list[str]) -> VirtualCommandResult:
    from iruka_vfs import service

    if not args:
        return VirtualCommandResult("", "mkdir: missing operand", 1, {})

    parents = False
    targets: list[str] = []
    for arg in args:
        if arg == "-p":
            parents = True
            continue
        if arg.startswith("-"):
            return VirtualCommandResult("", f"mkdir: unsupported option: {arg}", 1, {})
        targets.append(arg)
    if not targets:
        return VirtualCommandResult("", "mkdir: missing operand", 1, {})

    created: list[str] = []
    existing: list[str] = []
    for raw_path in targets:
        resolved_target = (
            service._normalize_virtual_path(db, session, raw_path)
            if parents
            else service._resolve_target_path_for_write(db, session, raw_path)
        )
        if not resolved_target:
            return VirtualCommandResult("", f"mkdir: cannot create directory '{raw_path}': invalid path", 1, {})
        allowed, deny_reason = service._allow_write_path(db, session, resolved_target)
        if not allowed:
            reason = deny_reason or f"path is read-only ({resolved_target})"
            return VirtualCommandResult("", f"mkdir: {reason}", 1, {"path": resolved_target})

        node = service._resolve_path(db, session.workspace_id, session.cwd_node_id, raw_path)
        if node:
            if node.node_type != "dir":
                return VirtualCommandResult("", f"mkdir: cannot create directory '{raw_path}': File exists", 1, {})
            if parents:
                existing.append(service._node_path(db, node))
                continue
            return VirtualCommandResult("", f"mkdir: cannot create directory '{raw_path}': File exists", 1, {})

        if parents:
            try:
                created_path = service._mkdir_parents(db, session, raw_path)
            except ValueError as exc:
                return VirtualCommandResult("", str(exc), 1, {"path": resolved_target})
        else:
            parent, name = service._resolve_parent_for_create(db, session.workspace_id, session.cwd_node_id, raw_path)
            if not parent or not name:
                return VirtualCommandResult("", f"mkdir: cannot create directory '{raw_path}': No such file or directory", 1, {})
            dir_node = service._get_or_create_child_dir(db, session.workspace_id, int(parent.id), name)
            created_path = service._node_path(db, dir_node)
        created.append(created_path)

    summary = []
    if created:
        summary.append(f"created={len(created)}")
    if existing:
        summary.append(f"existing={len(existing)}")
    return VirtualCommandResult(
        "mkdir " + ", ".join(summary or ["ok"]),
        "",
        0,
        {"created": created, "existing": existing, "parents": parents},
    )


def count_lines(text: str) -> int:
    return text.count("\n")


def exec_wc(db: Session, session, args: list[str], *, input_text: str) -> VirtualCommandResult:
    from iruka_vfs import service

    if not args:
        return VirtualCommandResult(str(count_lines(input_text)), "", 0, {"source": "stdin"})

    opts = [arg for arg in args if arg.startswith("-")]
    files = [arg for arg in args if not arg.startswith("-")]
    if any(opt != "-l" for opt in opts):
        return VirtualCommandResult("", "wc: only -l is supported. Use grep -c PATTERN <paths> to count matches.", 1, {})
    if opts and "-l" not in opts:
        return VirtualCommandResult("", "wc: only -l is supported. Use grep -c PATTERN <paths> to count matches.", 1, {})

    if not files:
        return VirtualCommandResult(str(count_lines(input_text)), "", 0, {"source": "stdin"})

    lines: list[str] = []
    total = 0
    for target in files:
        node = service._resolve_path(db, session.workspace_id, session.cwd_node_id, target)
        if not node or node.node_type != "file":
            return VirtualCommandResult("", service._format_missing_path_error("wc", target, db=db, session=session), 1, {})
        count = count_lines(service._get_node_content(db, node))
        total += count
        lines.append(f"{count} {service._node_path(db, node)}")
    if len(files) > 1:
        lines.append(f"{total} total")
    return VirtualCommandResult("\n".join(lines), "", 0, {"files": files, "total": total})


def exec_cp(db: Session, session, args: list[str]) -> VirtualCommandResult:
    from iruka_vfs import service
    from iruka_vfs.write_conflicts import build_overwrite_conflict, format_overwrite_conflict_message

    if len(args) != 2:
        return VirtualCommandResult("", "cp: require <source> <target>", 1, {})
    source, target = args
    source_node = service._resolve_path(db, session.workspace_id, session.cwd_node_id, source)
    if not source_node or source_node.node_type != "file":
        return VirtualCommandResult("", service._format_missing_path_error("cp", source, db=db, session=session), 1, {})
    target_node = service._resolve_path(db, session.workspace_id, session.cwd_node_id, target)
    if target_node:
        if target_node.node_type == "dir":
            target = f"{service._node_path(db, target_node).rstrip('/')}/{source_node.name}"
            target_node = service._resolve_path(db, session.workspace_id, session.cwd_node_id, target)
        else:
            conflict = build_overwrite_conflict(service._node_path(db, target_node), source="redirect")
            return VirtualCommandResult("", format_overwrite_conflict_message(service._node_path(db, target_node), source="redirect"), 1, conflict)
    resolved_target = service._resolve_target_path_for_write(db, session, target, node=target_node)
    if not resolved_target:
        return VirtualCommandResult("", f"cp: cannot create '{target}': invalid parent path", 1, {})
    allowed, deny_reason = service._allow_write_path(db, session, resolved_target)
    if not allowed:
        return VirtualCommandResult("", f"cp: {deny_reason}", 1, {"path": resolved_target})
    conflict = service._detect_ambiguous_create_target(db, session, target, node=target_node)
    if conflict is not None:
        return VirtualCommandResult("", service._format_ambiguous_create_target_message(conflict, source="redirect"), 1, conflict)
    parent, name = service._resolve_parent_for_create(db, session.workspace_id, session.cwd_node_id, target)
    if not parent or not name:
        return VirtualCommandResult("", f"cp: cannot create '{target}': invalid parent path", 1, {})
    created = service._get_or_create_child_file(
        db,
        session.workspace_id,
        int(parent.id),
        name,
        service._get_node_content(db, source_node),
    )
    return VirtualCommandResult(
        f"copied {service._node_path(db, source_node)} -> {service._node_path(db, created)}",
        "",
        0,
        {"source": service._node_path(db, source_node), "target": service._node_path(db, created)},
    )


def exec_mv(db: Session, session, args: list[str]) -> VirtualCommandResult:
    from iruka_vfs import service
    from iruka_vfs.write_conflicts import build_overwrite_conflict, format_overwrite_conflict_message

    if len(args) != 2:
        return VirtualCommandResult("", "mv: require <source> <target>", 1, {})
    source, target = args
    source_node = service._resolve_path(db, session.workspace_id, session.cwd_node_id, source)
    if not source_node or source_node.node_type != "file":
        return VirtualCommandResult("", service._format_missing_path_error("mv", source, db=db, session=session), 1, {})
    target_node = service._resolve_path(db, session.workspace_id, session.cwd_node_id, target)
    if target_node:
        if target_node.node_type == "dir":
            target = f"{service._node_path(db, target_node).rstrip('/')}/{source_node.name}"
            target_node = service._resolve_path(db, session.workspace_id, session.cwd_node_id, target)
        else:
            conflict = build_overwrite_conflict(service._node_path(db, target_node), source="redirect")
            return VirtualCommandResult("", format_overwrite_conflict_message(service._node_path(db, target_node), source="redirect"), 1, conflict)
    resolved_target = service._resolve_target_path_for_write(db, session, target, node=target_node)
    if not resolved_target:
        return VirtualCommandResult("", f"mv: cannot move to '{target}': invalid parent path", 1, {})
    allowed, deny_reason = service._allow_write_path(db, session, resolved_target)
    if not allowed:
        return VirtualCommandResult("", f"mv: {deny_reason}", 1, {"path": resolved_target})
    parent, name = service._resolve_parent_for_create(db, session.workspace_id, session.cwd_node_id, target)
    if not parent or not name:
        return VirtualCommandResult("", f"mv: cannot move to '{target}': invalid parent path", 1, {})
    if service._node_path(db, source_node) == resolved_target:
        return VirtualCommandResult(f"moved {resolved_target} -> {resolved_target}", "", 0, {"source": resolved_target, "target": resolved_target})
    version_no = service.move_node(db, source_node, parent_id=int(parent.id), name=name)
    moved_path = service._node_path(db, service._resolve_path(db, session.workspace_id, session.cwd_node_id, resolved_target))
    return VirtualCommandResult(
        f"moved {source} -> {moved_path} (version {version_no})",
        "",
        0,
        {"source": source, "target": moved_path, "version": version_no},
    )


def exec_rm(db: Session, session, args: list[str]) -> VirtualCommandResult:
    from iruka_vfs import service

    if len(args) != 1:
        return VirtualCommandResult("", "rm: require exactly one file path", 1, {})
    target = args[0]
    if target.startswith("-"):
        return VirtualCommandResult("", f"rm: unsupported option: {target}", 1, {})
    node = service._resolve_path(db, session.workspace_id, session.cwd_node_id, target)
    if not node or node.node_type != "file":
        return VirtualCommandResult("", service._format_missing_path_error("rm", target, db=db, session=session), 1, {})
    node_path = service._node_path(db, node)
    allowed, deny_reason = service._allow_write_path(db, session, node_path)
    if not allowed:
        return VirtualCommandResult("", f"rm: {deny_reason}", 1, {"path": node_path})
    service.delete_node(db, node)
    return VirtualCommandResult(f"removed {node_path}", "", 0, {"path": node_path})


def exec_sort(db: Session, session, args: list[str], *, input_text: str) -> VirtualCommandResult:
    from iruka_vfs import service

    if any(arg.startswith("-") for arg in args):
        first_flag = next(arg for arg in args if arg.startswith("-"))
        return VirtualCommandResult("", f"sort: unsupported option: {first_flag}", 1, {})
    if not args:
        lines = input_text.splitlines()
        return VirtualCommandResult("\n".join(sorted(lines)), "", 0, {"source": "stdin"})
    gathered: list[str] = []
    files: list[str] = []
    for target in args:
        node = service._resolve_path(db, session.workspace_id, session.cwd_node_id, target)
        if not node or node.node_type != "file":
            return VirtualCommandResult("", service._format_missing_path_error("sort", target, db=db, session=session), 1, {})
        gathered.extend(service._get_node_content(db, node).splitlines())
        files.append(service._node_path(db, node))
    return VirtualCommandResult("\n".join(sorted(gathered)), "", 0, {"files": files})


def exec_head(db: Session, session, args: list[str], *, input_text: str) -> VirtualCommandResult:
    from iruka_vfs import service

    line_count = 10
    targets: list[str] = []
    idx = 0
    while idx < len(args):
        token = args[idx]
        if token == "-n":
            if idx + 1 >= len(args):
                return VirtualCommandResult("", "head: missing line count after -n", 1, {})
            try:
                line_count = int(args[idx + 1])
            except ValueError:
                return VirtualCommandResult("", f"head: invalid line count: {args[idx + 1]}", 1, {})
            if line_count < 0:
                return VirtualCommandResult("", f"head: invalid line count: {args[idx + 1]}", 1, {})
            idx += 2
            continue
        if token.startswith("-") and len(token) > 1 and token[1:].isdigit():
            line_count = int(token[1:])
            idx += 1
            continue
        if token.startswith("-"):
            return VirtualCommandResult("", f"head: unsupported option: {token}", 1, {})
        targets.append(token)
        idx += 1

    if not targets:
        return VirtualCommandResult(
            "\n".join(input_text.splitlines()[:line_count]),
            "",
            0,
            {"source": "stdin", "line_count": line_count},
        )

    gathered: list[str] = []
    files: list[str] = []
    for target in targets:
        node = service._resolve_path(db, session.workspace_id, session.cwd_node_id, target)
        if not node or node.node_type != "file":
            return VirtualCommandResult("", service._format_missing_path_error("head", target, db=db, session=session), 1, {})
        gathered.extend(service._get_node_content(db, node).splitlines())
        files.append(service._node_path(db, node))
    return VirtualCommandResult(
        "\n".join(gathered[:line_count]),
        "",
        0,
        {"files": files, "line_count": line_count},
    )


def exec_basename(args: list[str]) -> VirtualCommandResult:
    if len(args) != 1:
        return VirtualCommandResult("", "basename: require exactly one path", 1, {})
    target = args[0]
    if target == "/":
        return VirtualCommandResult("/", "", 0, {"path": target})
    return VirtualCommandResult(PurePosixPath(target.rstrip("/") or target).name, "", 0, {"path": target})


def exec_dirname(args: list[str]) -> VirtualCommandResult:
    if len(args) != 1:
        return VirtualCommandResult("", "dirname: require exactly one path", 1, {})
    target = args[0]
    if target == "/":
        return VirtualCommandResult("/", "", 0, {"path": target})
    parent = str(PurePosixPath(target).parent)
    return VirtualCommandResult(parent or ".", "", 0, {"path": target})
