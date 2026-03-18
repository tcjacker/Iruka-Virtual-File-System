from __future__ import annotations

from datetime import datetime

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
        if node:
            if node.node_type != "file":
                return VirtualCommandResult("", f"touch: cannot touch '{raw_path}': Not a file", 1, {})
            mirror = service._get_workspace_mirror(session.workspace_id, tenant_key=getattr(session, "tenant_id", None))
            if mirror:
                with mirror.lock:
                    mirror_node = mirror.nodes.get(int(node.id), node)
                    mirror_node.updated_at = datetime.utcnow()
                    mirror.dirty_content_node_ids.add(int(mirror_node.id))
                    mirror.revision += 1
            else:
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
        return VirtualCommandResult("", "wc: only -l is supported", 1, {})
    if opts and "-l" not in opts:
        return VirtualCommandResult("", "wc: only -l is supported", 1, {})

    if not files:
        return VirtualCommandResult(str(count_lines(input_text)), "", 0, {"source": "stdin"})

    lines: list[str] = []
    total = 0
    for target in files:
        node = service._resolve_path(db, session.workspace_id, session.cwd_node_id, target)
        if not node or node.node_type != "file":
            return VirtualCommandResult("", f"wc: {target}: No such file", 1, {})
        count = count_lines(service._get_node_content(db, node))
        total += count
        lines.append(f"{count} {service._node_path(db, node)}")
    if len(files) > 1:
        lines.append(f"{total} total")
    return VirtualCommandResult("\n".join(lines), "", 0, {"files": files, "total": total})
