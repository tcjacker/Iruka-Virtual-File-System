from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.orm import Session

from iruka_vfs.command_parser import parse_options as _parse_options
from iruka_vfs.models import VirtualCommandResult


def _usage_example(command: str) -> str:
    if command == "edit":
        return "edit /workspace/file.txt --find old --replace new"
    return "patch --path /workspace/file.txt --find old --replace new"


def exec_edit(db: Session, session, args: list[str], *, input_text: str = "") -> VirtualCommandResult:
    from iruka_vfs import service

    if not args:
        return VirtualCommandResult("", "edit: missing path", 1, {})

    path = args[0]
    opts = _parse_options(args[1:])
    find_text = opts.get("--find")
    replace_text = opts.get("--replace")
    replace_all = "--all" in opts["flags"]
    node = service._resolve_path(db, session.workspace_id, session.cwd_node_id, path)
    if not node or node.node_type != "file":
        return VirtualCommandResult("", service._format_missing_path_error("edit", path, db=db, session=session), 1, {})
    node_path = service._node_path(db, node)
    allowed, deny_reason = service._allow_write_path(db, session, node_path)
    if not allowed:
        return VirtualCommandResult("", f"edit: {deny_reason}", 1, {"path": node_path})

    before = service._get_node_content(db, node)
    if find_text is None or replace_text is None:
        if input_text:
            version_no = service._write_file(db, node, input_text, op="edit_rewrite")
            return VirtualCommandResult(
                f"rewrote {node_path} from heredoc -> version {version_no}",
                "",
                0,
                {"path": node_path, "version": version_no, "rewrite_mode": "heredoc"},
            )
        return VirtualCommandResult(
            "",
            f"edit: require --find and --replace, or provide heredoc input for a full rewrite. Example: {_usage_example('edit')}",
            1,
            {},
        )
    if find_text not in before:
        return VirtualCommandResult("", "edit: target text not found", 1, {"path": node_path})

    if replace_all:
        after = before.replace(find_text, replace_text)
        count = before.count(find_text)
    else:
        after = before.replace(find_text, replace_text, 1)
        count = 1

    version_no = service._write_file(db, node, after, op="edit")
    return VirtualCommandResult(
        f"edited {count} occurrence(s) in {node_path} -> version {version_no}",
        "",
        0,
        {"path": node_path, "version": version_no, "replacements": count},
    )


def exec_patch(db: Session, session, args: list[str], *, input_text: str = "") -> VirtualCommandResult:
    from iruka_vfs import service

    if args and args[0] == "apply":
        args = args[1:]
    opts = _parse_options(args)
    path = opts.get("--path")
    if not path:
        return VirtualCommandResult(
            "",
            "patch: require --path. Example: patch --path /workspace/file.txt --find old --replace new",
            1,
            {},
        )

    node = service._resolve_path(db, session.workspace_id, session.cwd_node_id, path)
    if not node or node.node_type != "file":
        return VirtualCommandResult("", service._format_missing_path_error("patch", path, db=db, session=session), 1, {})
    node_path = service._node_path(db, node)
    allowed, deny_reason = service._allow_write_path(db, session, node_path)
    if not allowed:
        return VirtualCommandResult("", f"patch: {deny_reason}", 1, {"path": node_path})

    unified = opts.get("--unified") or (input_text if input_text else None)
    find_text = opts.get("--find")
    replace_text = opts.get("--replace")
    before = service._get_node_content(db, node)

    if unified:
        after, conflicts = apply_unified_patch(before, unified)
        patch_id = service._next_ephemeral_patch_id()
        if conflicts:
            return VirtualCommandResult(
                "",
                "patch: rejected hunks: " + json.dumps(conflicts, ensure_ascii=False),
                1,
                {"patch_id": patch_id, "conflicts": conflicts},
            )
        version_no = service._write_file(db, node, after, op="patch")
        return VirtualCommandResult(
            f"patch applied to {node_path} -> version {version_no}",
            "",
            0,
            {"patch_id": patch_id, "path": node_path, "version": version_no},
        )

    if find_text is None or replace_text is None:
        return VirtualCommandResult(
            "",
            "patch: require either --unified, heredoc diff input, or (--find and --replace). "
            "Examples: patch --path /workspace/file.txt --unified '@@ -1,1 +1,1 @@ ...' "
            "or patch --path /workspace/file.txt --find old --replace new",
            1,
            {},
        )
    if find_text not in before:
        return VirtualCommandResult("", "patch: target text not found", 1, {})

    after = before.replace(find_text, replace_text, 1)
    patch_id = service._next_ephemeral_patch_id()
    version_no = service._write_file(db, node, after, op="patch")
    return VirtualCommandResult(
        f"patch applied to {node_path} -> version {version_no}",
        "",
        0,
        {"patch_id": patch_id, "path": node_path, "version": version_no},
    )


def apply_unified_patch(before: str, diff_text: str) -> tuple[str, list[dict[str, Any]]]:
    original = before.splitlines()
    lines = diff_text.splitlines()
    output: list[str] = []
    cursor = 0
    idx = 0
    conflicts: list[dict[str, Any]] = []

    while idx < len(lines):
        line = lines[idx]
        if not line.startswith("@@"):
            idx += 1
            continue

        match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
        if not match:
            conflicts.append({"line": idx + 1, "reason": "invalid hunk header"})
            break

        old_start = int(match.group(1))
        output.extend(original[cursor : max(old_start - 1, cursor)])
        pos = old_start - 1
        idx += 1

        while idx < len(lines) and not lines[idx].startswith("@@"):
            patch_line = lines[idx]
            if patch_line.startswith("\\"):
                idx += 1
                continue
            if not patch_line:
                marker = " "
                text = ""
            else:
                marker = patch_line[0]
                text = patch_line[1:]

            if marker == " ":
                if pos >= len(original) or original[pos] != text:
                    conflicts.append({"line": idx + 1, "reason": "context mismatch", "expected": text})
                    return before, conflicts
                output.append(original[pos])
                pos += 1
            elif marker == "-":
                if pos >= len(original) or original[pos] != text:
                    conflicts.append({"line": idx + 1, "reason": "remove mismatch", "expected": text})
                    return before, conflicts
                pos += 1
            elif marker == "+":
                output.append(text)
            else:
                conflicts.append({"line": idx + 1, "reason": f"unsupported marker: {marker}"})
                return before, conflicts
            idx += 1

        cursor = pos

    output.extend(original[cursor:])
    newline = "\n" if before.endswith("\n") else ""
    return "\n".join(output) + newline, conflicts


def build_simple_patch(path: str, before: str, after: str) -> str:
    return "\n".join(
        [
            f"--- {path}",
            f"+++ {path}",
            "@@ -1,1 +1,1 @@",
            f"-{before}",
            f"+{after}",
        ]
    )
