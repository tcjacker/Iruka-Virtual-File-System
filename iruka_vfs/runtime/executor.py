from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy.orm import Session

from iruka_vfs.command_parser import parse_pipeline_and_redirect, split_chain
from iruka_vfs.models import VirtualCommandResult


HELP_TEXT = """Virtual workspace shell

Supported commands:
- pwd
- cd <path>
- ls [path]
  ls -l / ls -la also work and show type, size, version, and mtime
- cat <file>
- find [path] [-type f|d] [-name <glob>]
- rg [-l|-c] <pattern> [path...]
- grep [-l|-c|-v] <pattern> [path...]
- wc -l <file>
- mkdir [-p] <path>
- touch <file>
- cp <source> <target>
- mv <source> <target>
- rm <file>
- head [-n <count>] [file...]
- sort [file...]
- basename <path>
- dirname <path>
- edit <file> --find <text> --replace <text> [--all]
- patch --path <file> --find <text> --replace <text>
- patch --path <file> --unified <diff>
- tree
- xargs <command> [args...]
- echo <text>
- help

Discovery tips:
- When the target path is unknown, use: find /workspace -name <file> -> cat -> edit/patch
- Use find /workspace -name brief.md when you know the filename but not the path
- Reuse exact paths from workspace_bootstrap or unique_filename_index before guessing /workspace/<file>
- find also supports a limited -exec form, for example: find /workspace -type f -exec grep -l TODO {} \\;
- Use tree when you need the top-level directory layout
- Each bash result also includes workspace_outline, workspace_bootstrap, and unique_filename_index for path discovery
- Limited shell-compat tails are supported: 2>/dev/null and restricted || fallbacks (true, :, help)

Write rules:
- All writes must stay under /workspace
- > creates or writes a file but does not overwrite an existing file
- >| overwrites an existing file explicitly
- >> appends to an existing file
- When rewriting an existing file after reading it, prefer >| directly
- Limited heredoc is supported, for example: cat <<'EOF' > /workspace/file ... EOF
- Use host write_file(..., overwrite=True) or shell >| only after confirmation

Do not use unsupported shell syntax such as:
- ||
- <, <<<, 1>, 2>, &>
- $(...) or `...`
"""


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
        if isinstance(result.artifacts, dict):
            artifacts.update(result.artifacts)

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
    discard_stderr = bool(parsed.get("discard_stderr"))
    ignore_error = bool(parsed.get("ignore_error"))
    or_fallback = list(parsed.get("or_fallback") or [])
    input_text = str(parsed.get("stdin_text") or "")
    pipeline_artifacts: list[dict[str, Any]] = []
    last_result = VirtualCommandResult("", "", 0, {})

    for argv in pipeline:
        last_result = exec_argv(db, session, argv, input_text=input_text)
        pipeline_artifacts.append({"argv": argv, "exit_code": last_result.exit_code, "artifacts": last_result.artifacts})
        if last_result.exit_code != 0:
            stdout = last_result.stdout
            stderr = last_result.stderr
            if discard_stderr:
                stderr = ""
            if merge_stderr and stderr:
                stdout = (stdout + ("\n" if stdout and not stdout.endswith("\n") else "") + stderr).strip()
                stderr = ""
            artifacts = {"pipeline": pipeline_artifacts}
            if isinstance(last_result.artifacts, dict):
                artifacts.update(last_result.artifacts)
            if ignore_error:
                artifacts["ignored_error"] = True
                artifacts["or_fallback"] = or_fallback
                return VirtualCommandResult(stdout=stdout, stderr="" if discard_stderr else stderr, exit_code=0, artifacts=artifacts)
            if or_fallback:
                fallback_result = exec_argv(db, session, or_fallback, input_text="")
                artifacts["or_fallback"] = or_fallback
                artifacts["fallback_exit_code"] = fallback_result.exit_code
                if isinstance(fallback_result.artifacts, dict):
                    artifacts["fallback_artifacts"] = fallback_result.artifacts
                combined_stdout = "\n".join(
                    chunk for chunk in [stdout, fallback_result.stdout] if chunk
                ).strip()
                combined_stderr = "\n".join(
                    chunk for chunk in [stderr, "" if discard_stderr else fallback_result.stderr] if chunk
                ).strip()
                return VirtualCommandResult(
                    stdout=combined_stdout,
                    stderr=combined_stderr,
                    exit_code=fallback_result.exit_code,
                    artifacts=artifacts,
                )
            return VirtualCommandResult(stdout=stdout, stderr=stderr, exit_code=last_result.exit_code, artifacts=artifacts)
        input_text = last_result.stdout

    effective_stdout = last_result.stdout
    effective_stderr = last_result.stderr
    if discard_stderr:
        effective_stderr = ""
    if merge_stderr and effective_stderr:
        effective_stdout = (effective_stdout + ("\n" if effective_stdout and not effective_stdout.endswith("\n") else "") + effective_stderr).strip()
        effective_stderr = ""

    if redirect:
        write_result = apply_redirect(db, session, output_text=effective_stdout, redirect=redirect)
        if write_result.exit_code != 0:
            artifacts = {"pipeline": pipeline_artifacts}
            if isinstance(write_result.artifacts, dict):
                artifacts.update(write_result.artifacts)
            return VirtualCommandResult("", write_result.stderr, write_result.exit_code, artifacts)
        return VirtualCommandResult("", "", 0, {"pipeline": pipeline_artifacts, "redirect": write_result.artifacts})

    artifacts = {"pipeline": pipeline_artifacts}
    if isinstance(last_result.artifacts, dict):
        artifacts.update(last_result.artifacts)
    return VirtualCommandResult(effective_stdout, effective_stderr, last_result.exit_code, artifacts)


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
        changed = service._mutate_workspace_mirror(
            session.workspace_id,
            tenant_key=getattr(session, "tenant_id", None),
            mutate=lambda mirror: _mutate_cd(mirror, int(node.id)),
        )
        if changed is None:
            session.updated_at = datetime.utcnow()
            db.add(session)
            db.flush()
        return VirtualCommandResult("", "", 0, {"cwd": service._node_path(db, node)})

    if name == "ls":
        flags = {arg for arg in args if arg.startswith("-")}
        targets = [arg for arg in args if not arg.startswith("-")]
        unsupported_flags = sorted(flag for flag in flags if flag not in {"-l", "-a", "-la", "-al"})
        if unsupported_flags:
            return VirtualCommandResult(
                "",
                _format_ls_unsupported_option(unsupported_flags[0]),
                1,
                {"unsupported_option": unsupported_flags[0]},
            )
        target = targets[0] if targets else "."
        node = service._resolve_path(db, session.workspace_id, session.cwd_node_id, target)
        if not node:
            return VirtualCommandResult("", _format_ls_missing_target(db, session, target), 1, {})
        long_format = bool(flags & {"-l", "-la", "-al"})
        if node.node_type == "file":
            label = _format_ls_entry(node, long_format=long_format)
            return VirtualCommandResult(label, "", 0, {"path": service._node_path(db, node), "flags": sorted(flags)})
        children = service._list_children(db, session.workspace_id, node.id)
        listing = [_format_ls_entry(item, long_format=long_format) for item in children]
        return VirtualCommandResult(
            "\n".join(listing),
            "",
            0,
            {"path": service._node_path(db, node), "count": len(listing), "flags": sorted(flags)},
        )

    if name == "cat":
        if not args:
            return VirtualCommandResult(input_text, "", 0, {})
        outputs: list[str] = []
        files: list[str] = []
        for target in args:
            node = service._resolve_path(db, session.workspace_id, session.cwd_node_id, target)
            if not node or node.node_type != "file":
                return VirtualCommandResult("", service._format_missing_path_error("cat", target, db=db, session=session), 1, {})
            outputs.append(service._get_node_content(db, node))
            files.append(service._node_path(db, node))
        return VirtualCommandResult("\n".join(outputs), "", 0, {"files": files})

    if name == "find":
        return service._exec_find(db, session, args)

    if name in {"rg", "grep"}:
        flags = _expand_short_flags([arg for arg in args if arg.startswith("-")])
        supported_flags = {"-l", "-c"} | ({"-v"} if name == "grep" else set())
        unsupported_flags = sorted(flag for flag in flags if flag not in supported_flags)
        if unsupported_flags:
            return VirtualCommandResult("", _format_search_unsupported_option(name, unsupported_flags[0]), 1, {})
        if "-l" in flags and "-c" in flags:
            return VirtualCommandResult("", f"{name}: use either -l or -c, not both", 1, {})
        non_flags = [arg for arg in args if not arg.startswith("-")]
        if len(non_flags) < 1:
            return VirtualCommandResult("", f"{name}: missing pattern", 1, {})
        pattern = non_flags[0]
        targets = non_flags[1:]
        list_only = "-l" in flags
        count_only = "-c" in flags
        invert_match = "-v" in flags
        if not targets:
            matched = service._search_text_lines(input_text, pattern, invert_match=invert_match)
            match_count = len(matched)
            if count_only:
                return VirtualCommandResult(
                    str(match_count),
                    "",
                    0 if match_count else 1,
                    {"match_count": match_count, "source": "stdin", "flags": sorted(flags)},
                )
            if not matched:
                return VirtualCommandResult("", "", 1, {"match_count": 0, "source": "stdin", "flags": sorted(flags)})
            if list_only:
                return VirtualCommandResult("stdin", "", 0, {"match_count": 1, "source": "stdin", "flags": sorted(flags)})
            return VirtualCommandResult("\n".join(matched), "", 0, {"match_count": match_count, "source": "stdin", "flags": sorted(flags)})
        outputs: list[str] = []
        match_count = 0
        seen_outputs: set[str] = set()
        for target in targets:
            node = service._resolve_path(db, session.workspace_id, session.cwd_node_id, target)
            if not node:
                return VirtualCommandResult("", service._format_missing_path_error(name, target, directory_style=True, db=db, session=session), 1, {})
            if list_only:
                matches = service._search_matching_file_paths(
                    db, session.workspace_id, node, pattern, invert_match=invert_match
                )
                match_count += len(matches)
            elif count_only:
                counts = service._search_match_counts(
                    db, session.workspace_id, node, pattern, invert_match=invert_match
                )
                matches = _format_count_outputs(counts, single_target=len(targets) == 1 and node.node_type == "file")
                match_count += sum(count for _, count in counts)
            else:
                matches = service._search_nodes(
                    db, session.workspace_id, node, pattern, invert_match=invert_match
                )
                match_count += len(matches)
            for item in matches:
                if item in seen_outputs:
                    continue
                seen_outputs.add(item)
                outputs.append(item)
        if not outputs:
            return VirtualCommandResult("", "", 1, {"match_count": 0})
        return VirtualCommandResult(
            "\n".join(outputs),
            "",
            0,
            {"match_count": match_count, "pattern": pattern, "flags": sorted(flags), "targets": list(targets)},
        )

    if name == "xargs":
        if not args:
            return VirtualCommandResult("", "xargs: missing command", 1, {})
        stdin_paths = [line.strip() for line in input_text.splitlines() if line.strip()]
        if not stdin_paths:
            return VirtualCommandResult("", "", 0, {"input_count": 0})
        result = exec_argv(db, session, args + stdin_paths, input_text="")
        artifacts = dict(result.artifacts or {})
        artifacts["input_count"] = len(stdin_paths)
        artifacts["expanded_argv"] = args + stdin_paths
        return VirtualCommandResult(result.stdout, result.stderr, result.exit_code, artifacts)

    if name == "wc":
        return service._exec_wc(db, session, args, input_text=input_text)
    if name == "mkdir":
        return service._exec_mkdir(db, session, args)
    if name == "cp":
        return service._exec_cp(db, session, args)
    if name == "mv":
        return service._exec_mv(db, session, args)
    if name == "rm":
        return service._exec_rm(db, session, args)
    if name == "head":
        return service._exec_head(db, session, args, input_text=input_text)
    if name == "sort":
        return service._exec_sort(db, session, args, input_text=input_text)
    if name == "basename":
        return service._exec_basename(args)
    if name == "dirname":
        return service._exec_dirname(args)
    if name == "edit":
        return service._exec_edit(db, session, args)
    if name == "patch":
        return service._exec_patch(db, session, args)
    if name == "tree":
        return VirtualCommandResult(service.render_virtual_tree(db, session.workspace_id), "", 0, {})
    if name == "echo":
        return VirtualCommandResult(" ".join(args), "", 0, {})
    if name == ":":
        return VirtualCommandResult("", "", 0, {"noop": True})
    if name == "help":
        return VirtualCommandResult(
            HELP_TEXT,
            "",
            0,
            {
                "supported_commands": [
                    "pwd",
                    "cd",
                    "ls",
                    "cat",
                    "find",
                    "rg",
                    "grep",
                    "wc",
                    "mkdir",
                    "touch",
                    "cp",
                    "mv",
                    "rm",
                    "head",
                    "sort",
                    "basename",
                    "dirname",
                    "edit",
                    "patch",
                    "tree",
                    "xargs",
                    "echo",
                    "help",
                ],
                "redirects": [">", ">|", ">>"],
                "heredoc": {"supported": True, "pattern": "cat <<'EOF' > /workspace/file ... EOF"},
                "compat_tails": ["2>/dev/null", "|| true", "|| :", "|| help"],
                "write_root": "/workspace",
            },
        )
    if name == "touch":
        return service._exec_touch(db, session, args)

    return VirtualCommandResult("", f"unsupported command: {name}. Try: help", 127, {})


def _mutate_cd(mirror, cwd_node_id: int):
    if int(mirror.cwd_node_id) == cwd_node_id:
        return False, False
    mirror.cwd_node_id = cwd_node_id
    mirror.dirty_session = True
    mirror.revision += 1
    return True, True


def _format_ls_entry(node, *, long_format: bool) -> str:
    display_name = f"{node.name}/" if node.node_type == "dir" else node.name
    if not long_format:
        return display_name
    type_label = "dir" if node.node_type == "dir" else "file"
    size_bytes = len(getattr(node, "content_text", "") or "") if node.node_type == "file" else 0
    version_no = int(getattr(node, "version_no", 1) or 1)
    updated_at = _format_ls_timestamp(getattr(node, "updated_at", None))
    return f"{type_label:<4} size={size_bytes} version={version_no} mtime={updated_at} {display_name}"


def _format_ls_timestamp(value: datetime | None) -> str:
    if not isinstance(value, datetime):
        return "-"
    return value.strftime("%Y-%m-%dT%H:%M:%S")


def _format_ls_missing_target(db: Session, session, target: str) -> str:
    missing = _format_missing_target_hint("ls", target, db=db, session=session, directory_style=True)
    return missing.replace("ls: ", "ls: cannot access '", 1).replace(": No such file or directory.", "': No such file or directory.", 1)


def _format_ls_unsupported_option(flag: str) -> str:
    if flag == "-R":
        return "ls: unsupported option: -R. Try: tree for recursion or find /workspace -name <file>"
    return f"ls: unsupported option: {flag}"


def _format_search_unsupported_option(command: str, flag: str) -> str:
    supported = "-l, -c" if command == "rg" else "-l, -c, -v"
    return (
        f"{command}: unsupported option: {flag}. Supported options: {supported}. "
        f"Try: {command} -l PATTERN /workspace to locate files or {command} -c PATTERN <paths> to count matches."
    )


def _expand_short_flags(flags: list[str]) -> list[str]:
    expanded: list[str] = []
    for flag in flags:
        if flag.startswith("-") and not flag.startswith("--") and len(flag) > 2:
            expanded.extend(f"-{part}" for part in flag[1:])
            continue
        expanded.append(flag)
    return expanded


def _format_count_outputs(counts: list[tuple[str, int]], *, single_target: bool) -> list[str]:
    if single_target and len(counts) == 1:
        return [str(counts[0][1])]
    return [f"{path}:{count}" for path, count in counts]


def _format_missing_target_hint(command: str, target: str, *, db: Session, session, directory_style: bool) -> str:
    from iruka_vfs import service

    return service._format_missing_path_error(command, target, directory_style=directory_style, db=db, session=session)


def apply_redirect(db: Session, session, *, output_text: str, redirect: dict[str, str]) -> VirtualCommandResult:
    from iruka_vfs import service
    from iruka_vfs.write_conflicts import build_overwrite_conflict, format_overwrite_conflict_message

    target_path = redirect["path"]
    op = redirect["op"]
    force = bool(redirect.get("force"))
    node = service._resolve_path(db, session.workspace_id, session.cwd_node_id, target_path)
    if node and node.node_type == "dir":
        return VirtualCommandResult("", f"redirect: {target_path}: is a directory", 1, {})

    resolved_target = service._resolve_target_path_for_write(db, session, target_path, node=node)
    if not resolved_target:
        return VirtualCommandResult("", f"redirect: cannot create {target_path}: invalid parent path", 1, {})
    allowed, deny_reason = service._allow_write_path(db, session, resolved_target)
    if not allowed:
        return VirtualCommandResult("", f"redirect: {deny_reason}", 1, {"path": resolved_target})
    conflict = service._detect_ambiguous_create_target(db, session, target_path, node=node)
    if conflict is not None:
        return VirtualCommandResult(
            "",
            service._format_ambiguous_create_target_message(conflict, source="redirect"),
            1,
            conflict,
        )

    if not node:
        parent, name = service._resolve_parent_for_create(db, session.workspace_id, session.cwd_node_id, target_path)
        if not parent:
            return VirtualCommandResult("", f"redirect: cannot create {target_path}: invalid parent path", 1, {})
        node = service._get_or_create_child_file(db, session.workspace_id, parent.id, name, "")
    elif op == ">" and not force:
        conflict = build_overwrite_conflict(service._node_path(db, node), source="redirect")
        conflict["op"] = op
        return VirtualCommandResult(
            "",
            format_overwrite_conflict_message(service._node_path(db, node), source="redirect"),
            1,
            conflict,
        )

    new_content = output_text
    if op == ">>":
        new_content = service._get_node_content(db, node) + output_text

    version_no = service._write_file(db, node, new_content, op="redirect_append" if op == ">>" else "redirect_write")
    return VirtualCommandResult("", "", 0, {"path": service._node_path(db, node), "op": op, "version": version_no})
