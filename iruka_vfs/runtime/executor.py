from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from iruka_vfs.command_parser import parse_pipeline_and_redirect, split_chain
from iruka_vfs.models import VirtualCommandResult

SUPPORTED_COMMANDS = {
    "pwd",
    "cd",
    "ls",
    "cat",
    "rg",
    "grep",
    "wc",
    "mkdir",
    "edit",
    "patch",
    "tree",
    "echo",
    "touch",
}


def _unsupported_command_error(name: str) -> str:
    message = f"unsupported command: {name}"
    hints: list[str] = []
    if name.endswith(":"):
        hints.append("remove trailing punctuation")
    hints.append(f"supported commands: {', '.join(sorted(SUPPORTED_COMMANDS))}")
    return f"{message}. {'; '.join(hints)}"


def run_command_chain(db: Session, session, raw_cmd: str) -> VirtualCommandResult:
    pieces = split_chain(raw_cmd)
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    artifacts: dict[str, Any] = {"results": []}
    last_exit = 0

    for item in pieces:
        op = item["op"]
        cmd = item["cmd"]
        if op == "&&" and last_exit != 0:
            continue
        result = run_single_command(db, session, cmd)
        last_exit = result.exit_code
        if result.stdout:
            stdout_chunks.append(result.stdout)
        if result.stderr:
            stderr_chunks.append(result.stderr)
        artifacts["results"].append({"cmd": cmd, "exit_code": result.exit_code, "artifacts": result.artifacts})

    return VirtualCommandResult(
        stdout="\n".join(chunk for chunk in stdout_chunks if chunk).strip(),
        stderr="\n".join(chunk for chunk in stderr_chunks if chunk).strip(),
        exit_code=last_exit,
        artifacts=artifacts,
    )


def run_single_command(db: Session, session, cmd: str) -> VirtualCommandResult:
    from iruka_vfs import service

    parsed, parse_error = parse_pipeline_and_redirect(cmd)
    if parse_error:
        return VirtualCommandResult("", parse_error, 2, {})

    pipeline = parsed.get("pipeline") or []
    redirect = parsed.get("redirect")
    merge_stderr = bool(parsed.get("merge_stderr"))
    input_text = ""
    pipeline_artifacts: list[dict[str, Any]] = []
    last_result = VirtualCommandResult("", "", 0, {})

    for argv in pipeline:
        last_result = exec_argv(db, session, argv, input_text=input_text)
        pipeline_artifacts.append({"argv": argv, "exit_code": last_result.exit_code, "artifacts": last_result.artifacts})
        if last_result.exit_code != 0:
            stdout = last_result.stdout
            stderr = last_result.stderr
            if merge_stderr and stderr:
                stdout = (stdout + ("\n" if stdout and not stdout.endswith("\n") else "") + stderr).strip()
                stderr = ""
            return VirtualCommandResult(stdout=stdout, stderr=stderr, exit_code=last_result.exit_code, artifacts={"pipeline": pipeline_artifacts})
        input_text = last_result.stdout

    effective_stdout = last_result.stdout
    effective_stderr = last_result.stderr
    if merge_stderr and effective_stderr:
        effective_stdout = (effective_stdout + ("\n" if effective_stdout and not effective_stdout.endswith("\n") else "") + effective_stderr).strip()
        effective_stderr = ""

    if redirect:
        write_result = apply_redirect(db, session, output_text=effective_stdout, redirect=redirect)
        if write_result.exit_code != 0:
            return VirtualCommandResult("", write_result.stderr, write_result.exit_code, {"pipeline": pipeline_artifacts})
        return VirtualCommandResult("", "", 0, {"pipeline": pipeline_artifacts, "redirect": write_result.artifacts})

    return VirtualCommandResult(effective_stdout, effective_stderr, last_result.exit_code, {"pipeline": pipeline_artifacts})


def exec_argv(db: Session, session, argv: list[str], *, input_text: str = "") -> VirtualCommandResult:
    from iruka_vfs import service

    if not argv:
        return VirtualCommandResult("", "", 0, {})

    name = argv[0]
    args = argv[1:]

    if name == "pwd":
        cwd = service._must_get_node(db, session.cwd_node_id)
        return VirtualCommandResult(service._node_path(db, cwd), "", 0, {})

    if name == "cd":
        target = args[0] if args else "/workspace"
        node = service._resolve_path(db, session.workspace_id, session.cwd_node_id, target)
        if not node or node.node_type != "dir":
            return VirtualCommandResult("", f"cd: no such directory: {target}", 1, {})
        session.cwd_node_id = node.id
        mirror = service._get_workspace_mirror(session.workspace_id, tenant_key=getattr(session, "tenant_id", None))
        if mirror:
            with mirror.lock:
                mirror.cwd_node_id = int(node.id)
                mirror.dirty_session = True
                mirror.revision += 1
        else:
            session.updated_at = datetime.utcnow()
            db.add(session)
            db.flush()
        return VirtualCommandResult("", "", 0, {"cwd": service._node_path(db, node)})

    if name == "ls":
        target = args[0] if args else "."
        node = service._resolve_path(db, session.workspace_id, session.cwd_node_id, target)
        if not node:
            return VirtualCommandResult("", f"ls: cannot access '{target}': No such file or directory", 1, {})
        if node.node_type == "file":
            return VirtualCommandResult(node.name, "", 0, {"path": service._node_path(db, node)})
        children = service._list_children(db, session.workspace_id, node.id)
        listing = [f"{item.name}/" if item.node_type == "dir" else item.name for item in children]
        return VirtualCommandResult("\n".join(listing), "", 0, {"path": service._node_path(db, node), "count": len(listing)})

    if name == "cat":
        if not args:
            return VirtualCommandResult(input_text, "", 0, {})
        outputs: list[str] = []
        files: list[str] = []
        for target in args:
            node = service._resolve_path(db, session.workspace_id, session.cwd_node_id, target)
            if not node or node.node_type != "file":
                return VirtualCommandResult("", f"cat: {target}: No such file", 1, {})
            outputs.append(service._get_node_content(db, node))
            files.append(service._node_path(db, node))
        return VirtualCommandResult("\n".join(outputs), "", 0, {"files": files})

    if name in {"rg", "grep"}:
        if len(args) < 1:
            return VirtualCommandResult("", f"{name}: missing pattern", 1, {})
        pattern = args[0]
        if len(args) == 1:
            matched = service._search_text_lines(input_text, pattern)
            if not matched:
                return VirtualCommandResult("", "", 1, {"match_count": 0, "source": "stdin"})
            return VirtualCommandResult("\n".join(matched), "", 0, {"match_count": len(matched), "source": "stdin"})
        target = args[1]
        node = service._resolve_path(db, session.workspace_id, session.cwd_node_id, target)
        if not node:
            return VirtualCommandResult("", f"{name}: {target}: No such file or directory", 1, {})
        matches = service._search_nodes(db, session.workspace_id, node, pattern)
        if not matches:
            return VirtualCommandResult("", "", 1, {"match_count": 0})
        return VirtualCommandResult("\n".join(matches), "", 0, {"match_count": len(matches), "pattern": pattern})

    if name == "wc":
        return service._exec_wc(db, session, args, input_text=input_text)
    if name == "mkdir":
        return service._exec_mkdir(db, session, args)
    if name == "edit":
        return service._exec_edit(db, session, args)
    if name == "patch":
        return service._exec_patch(db, session, args)
    if name == "tree":
        return VirtualCommandResult(service.render_virtual_tree(db, session.workspace_id), "", 0, {})
    if name == "echo":
        return VirtualCommandResult(" ".join(args), "", 0, {})
    if name == "touch":
        return service._exec_touch(db, session, args)

    return VirtualCommandResult("", _unsupported_command_error(name), 127, {})


def apply_redirect(db: Session, session, *, output_text: str, redirect: dict[str, str]) -> VirtualCommandResult:
    from iruka_vfs import service

    target_path = redirect["path"]
    op = redirect["op"]
    node = service._resolve_path(db, session.workspace_id, session.cwd_node_id, target_path)
    if node and node.node_type == "dir":
        return VirtualCommandResult("", f"redirect: {target_path}: is a directory", 1, {})

    resolved_target = service._resolve_target_path_for_write(db, session, target_path, node=node)
    if not resolved_target:
        return VirtualCommandResult("", f"redirect: cannot create {target_path}: invalid parent path", 1, {})
    allowed, deny_reason = service._allow_write_path(db, session, resolved_target)
    if not allowed:
        return VirtualCommandResult("", f"redirect: {deny_reason}", 1, {"path": resolved_target})

    if not node:
        parent, name = service._resolve_parent_for_create(db, session.workspace_id, session.cwd_node_id, target_path)
        if not parent:
            return VirtualCommandResult("", f"redirect: cannot create {target_path}: invalid parent path", 1, {})
        node = service._get_or_create_child_file(db, session.workspace_id, parent.id, name, "")

    new_content = output_text
    if op == ">>":
        new_content = service._get_node_content(db, node) + output_text

    version_no = service._write_file(db, node, new_content, op="redirect_append" if op == ">>" else "redirect_write")
    return VirtualCommandResult("", "", 0, {"path": service._node_path(db, node), "op": op, "version": version_no})
