from __future__ import annotations

import re
import shlex
from typing import Any

from iruka_vfs.parse_errors import (
    ParseErrorDetail,
    invalid_heredoc_error_detail,
    make_parse_error,
    shlex_parse_error_detail,
    unsupported_or_error_detail,
)


def split_chain(raw_cmd: str) -> list[dict[str, str]]:
    heredoc_cmd = _split_chain_with_heredoc(raw_cmd)
    if heredoc_cmd is not None:
        return heredoc_cmd

    stripped = raw_cmd.strip()
    if not stripped:
        return [{"op": ";", "cmd": ""}]

    pieces: list[dict[str, str]] = []
    current_op = ";"
    current_chars: list[str] = []
    in_single = False
    in_double = False
    escaped = False
    idx = 0

    while idx < len(stripped):
        token = stripped[idx]
        if escaped:
            current_chars.append(token)
            escaped = False
            idx += 1
            continue
        if token == "\\":
            current_chars.append(token)
            escaped = True
            idx += 1
            continue
        if token == "'" and not in_double:
            in_single = not in_single
            current_chars.append(token)
            idx += 1
            continue
        if token == '"' and not in_single:
            in_double = not in_double
            current_chars.append(token)
            idx += 1
            continue
        if not in_single and not in_double:
            if stripped.startswith("&&", idx):
                segment = "".join(current_chars).strip()
                if segment:
                    pieces.append({"op": current_op, "cmd": segment})
                current_chars = []
                current_op = "&&"
                idx += 2
                continue
            if token == ";":
                segment = "".join(current_chars).strip()
                if segment:
                    pieces.append({"op": current_op, "cmd": segment})
                current_chars = []
                current_op = ";"
                idx += 1
                continue
        current_chars.append(token)
        idx += 1

    segment = "".join(current_chars).strip()
    if segment:
        pieces.append({"op": current_op, "cmd": segment})
    return pieces or [{"op": ";", "cmd": stripped}]


def parse_pipeline_and_redirect(cmd: str) -> tuple[dict[str, Any], str | None]:
    parsed, detail = parse_pipeline_and_redirect_detailed(cmd)
    return parsed, detail.render() if detail else None


def parse_pipeline_and_redirect_detailed(cmd: str) -> tuple[dict[str, Any], ParseErrorDetail | None]:
    cmd, compat = _extract_compatible_shell_tails(cmd)
    cmd, here_string_text, here_string_error = _extract_here_string(cmd)
    if here_string_error:
        return {}, here_string_error
    unsupported_error = _detect_unsupported_shell_syntax(cmd)
    if unsupported_error:
        return {}, unsupported_error

    cmd, stdin_text, heredoc_error = _extract_heredoc(cmd)
    if heredoc_error:
        return {}, heredoc_error

    try:
        tokens = list(shell_tokens(cmd))
    except ValueError as exc:
        return {}, shlex_parse_error_detail(cmd, str(exc))

    if not tokens:
        return {"pipeline": [], "redirect": None}, None

    pipeline: list[list[str]] = []
    current: list[str] = []
    redirect: dict[str, str] | None = None
    merge_stderr = False
    discard_stderr = bool(compat["discard_stderr"])
    ignore_error = bool(compat["ignore_error"])
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        if token == "2>&1":
            merge_stderr = True
            idx += 1
            continue
        if token == "|":
            if not current:
                return {}, make_parse_error("empty_command_before_pipe", "empty command before pipe")
            pipeline.append(current)
            current = []
            idx += 1
            continue
        if token in {">", ">>", ">|"}:
            if idx + 1 >= len(tokens):
                return {}, make_parse_error("missing_redirect_target", "redirect target is missing")
            redirect = {"op": token, "path": tokens[idx + 1], "force": token == ">|"}
            idx += 2
            if idx < len(tokens) and tokens[idx] == "--force":
                redirect["force"] = True
                idx += 1
            if idx < len(tokens):
                return {}, make_parse_error("trailing_tokens_after_redirect", "trailing tokens after redirect target")
            break
        current.append(token)
        idx += 1

    if current:
        pipeline.append(current)
    if not pipeline:
        return {}, make_parse_error("empty_command", "empty command")
    return {
        "pipeline": pipeline,
        "redirect": redirect,
        "merge_stderr": merge_stderr,
        "discard_stderr": discard_stderr,
        "ignore_error": ignore_error,
        "or_fallback": compat["or_fallback"],
        "stdin_text": stdin_text or here_string_text,
    }, None


def shell_tokens(cmd: str) -> list[str]:
    lexer = shlex.shlex(cmd, posix=True, punctuation_chars="|>&")
    lexer.whitespace_split = True
    lexer.commenters = ""
    tokens = list(lexer)
    normalized: list[str] = []
    for token in tokens:
        if token == "2>&1":
            normalized.append(token)
            continue
        if token in {"2>", "&1"}:
            normalized.append(token)
            continue
        if token == ">>":
            normalized.append(token)
            continue
        if token and set(token) <= {">"} and token != ">":
            normalized.extend(">" for _ in token)
            continue
        normalized.append(token)

    merged: list[str] = []
    i = 0
    while i < len(normalized):
        if i + 2 < len(normalized) and normalized[i] == "2" and normalized[i + 1] == ">&" and normalized[i + 2] == "1":
            merged.append("2>&1")
            i += 3
            continue
        if i + 1 < len(normalized) and normalized[i] == "2>" and normalized[i + 1] == "&1":
            merged.append("2>&1")
            i += 2
            continue
        merged.append(normalized[i])
        i += 1
    return merged


def parse_options(args: list[str]) -> dict[str, Any]:
    values: dict[str, Any] = {"flags": set()}
    idx = 0
    while idx < len(args):
        token = args[idx]
        if token.startswith("--"):
            next_idx = idx + 1
            parts: list[str] = []
            while next_idx < len(args) and not args[next_idx].startswith("--"):
                parts.append(args[next_idx])
                next_idx += 1
            if parts:
                values[token] = " ".join(parts)
                idx = next_idx
                continue
            values["flags"].add(token)
            idx += 1
            continue
        values.setdefault("_", []).append(token)
        idx += 1
    return values


def _split_chain_with_heredoc(raw_cmd: str) -> list[dict[str, str]] | None:
    if "<<" not in raw_cmd:
        return None
    lines = raw_cmd.splitlines(keepends=True)
    if not lines:
        return None
    header = lines[0].rstrip("\r\n")
    if "<<" not in header:
        return None

    tokens = re.split(r"\s*(&&|;)\s*", header.strip())
    if len(tokens) == 1:
        return [{"op": ";", "cmd": raw_cmd.strip()}]

    pieces: list[dict[str, str]] = []
    current_op = ";"
    command_tokens: list[str] = []
    for token in tokens:
        token = token.strip()
        if not token:
            continue
        if token in {"&&", ";"}:
            current_op = token
            continue
        command_tokens.append(token)
        pieces.append({"op": current_op, "cmd": token})
    if not pieces:
        return [{"op": ";", "cmd": raw_cmd.strip()}]

    pieces[-1]["cmd"] = pieces[-1]["cmd"] + ("\n" + "".join(lines[1:]) if len(lines) > 1 else "")
    return pieces


def _extract_heredoc(cmd: str) -> tuple[str, str, ParseErrorDetail | None]:
    if "<<" not in cmd:
        return cmd, "", None

    lines = cmd.splitlines(keepends=True)
    if not lines:
        return cmd, "", None
    header = lines[0].rstrip("\r\n")
    match = re.search(r"<<\s*(?:'([^']+)'|\"([^\"]+)\"|([^\s|;&>]+))", header)
    if not match:
        return "", "", invalid_heredoc_error_detail(_first_command_name(header))

    delimiter = next(group for group in match.groups() if group is not None)
    header_without_heredoc = (header[: match.start()] + header[match.end() :]).strip()
    body_lines = lines[1:]
    collected: list[str] = []
    terminator_found = False
    terminator_idx = -1
    for idx, line in enumerate(body_lines):
        if line.rstrip("\r\n") == delimiter:
            terminator_found = True
            terminator_idx = idx
            break
        collected.append(line)
    if not terminator_found:
        return "", "", make_parse_error("missing_heredoc_terminator", f"heredoc terminator not found: {delimiter}")
    if "<<" in header_without_heredoc:
        return "", "", make_parse_error("multiple_heredocs", "multiple heredocs are not supported")
    trailing = "".join(body_lines[terminator_idx + 1 :]).strip()
    if trailing:
        return (
            "",
            "",
            make_parse_error(
                "multiple_heredoc_write_blocks",
                "multiple heredoc write blocks in a single raw command are not supported.",
                suggestion="Split them into two commands with `;` or `&&`.",
                example="Template: `cat <<'EOF' >| /workspace/a ... EOF ; cat <<'EOF' >| /workspace/b ... EOF`",
            ),
        )
    return header_without_heredoc, "".join(collected), None


def _extract_here_string(cmd: str) -> tuple[str, str, ParseErrorDetail | None]:
    split_idx = _top_level_here_string_index(cmd)
    if split_idx is None:
        return cmd, "", None

    primary = cmd[:split_idx].rstrip()
    raw_rhs = cmd[split_idx + 3 :].strip()
    if not primary:
        return "", "", make_parse_error("missing_here_string_command", "here-string redirect <<< is missing a command before it")
    if not raw_rhs:
        return "", "", make_parse_error("missing_here_string_input", "here-string redirect <<< is missing input text")
    if "$(" in raw_rhs or "`" in raw_rhs:
        return (
            "",
            "",
            make_parse_error(
                "unsupported_here_string_substitution",
                "here-string command substitution is not supported.",
                suggestion="Use `cat <file> | <command>`, `echo <text> | <command>`, or `cat <<'EOF' | <command>` instead.",
            ),
        )

    try:
        parts = list(shlex.split(raw_rhs, posix=True))
    except ValueError as exc:
        return "", "", make_parse_error("invalid_here_string_text", f"invalid here-string text: {exc}")
    if not parts:
        return "", "", make_parse_error("missing_here_string_input", "here-string redirect <<< is missing input text")
    return primary, " ".join(parts) + "\n", None


def _detect_unsupported_shell_syntax(cmd: str) -> ParseErrorDetail | None:
    stripped = cmd.strip()
    if not stripped:
        return None
    if "||" in stripped:
        fallback = _top_level_or_parts(stripped)
        if fallback is not None:
            _, fallback_text = fallback
            return unsupported_or_error_detail(fallback_text)
        return unsupported_or_error_detail("")
    if "$(" in stripped or "`" in stripped:
        return make_parse_error("unsupported_command_substitution", "command substitution is not supported; use plain commands only")
    if "<<<" in stripped:
        return make_parse_error(
            "unsupported_here_string_redirect",
            "unsupported here-string redirect <<<.",
            suggestion="Use `echo <text> | <command>`, `cat <file> | <command>`, or `cat <<'EOF' | <command>` instead.",
        )
    if "&>" in stripped:
        return make_parse_error("&>_not_supported", "&> redirect is not supported; only >, >>, >|, and 2>&1 are supported")
    if re.search(r"(^|[^0-9])(?:1>|2>)", stripped):
        return make_parse_error(
            "unsupported_general_stderr_redirect",
            "general 1>/2> redirects are not supported.",
            suggestion="Use `2>/dev/null` to discard stderr, `2>&1` to merge stderr into stdout, or remove the stderr redirect tail and run the main command directly.",
        )
    if _contains_plain_input_redirect(stripped):
        return make_parse_error("unsupported_input_redirect", "input redirect < is not supported")
    return None


def _contains_plain_input_redirect(cmd: str) -> bool:
    idx = 0
    while idx < len(cmd):
        char = cmd[idx]
        if char != "<":
            idx += 1
            continue
        next_char = cmd[idx + 1] if idx + 1 < len(cmd) else ""
        prev_char = cmd[idx - 1] if idx > 0 else ""
        if next_char == "<":
            idx += 2
            continue
        if prev_char == "<":
            idx += 1
            continue
        return True
    return False


def _extract_compatible_shell_tails(cmd: str) -> tuple[str, dict[str, bool]]:
    stripped = cmd.strip()
    compat = {"discard_stderr": False, "ignore_error": False, "or_fallback": None}
    if not stripped:
        return cmd, compat

    if re.search(r"(?:^|\s)2>/dev/null(?:\s|$)", stripped):
        stripped = re.sub(r"(?:^|\s)2>/dev/null(?=\s|$)", " ", stripped)
        compat["discard_stderr"] = True

    or_parts = _top_level_or_parts(stripped)
    if or_parts is None:
        return stripped, compat

    primary, fallback_text = or_parts
    fallback_name = fallback_text.strip()
    if fallback_name == "true":
        compat["ignore_error"] = True
        compat["or_fallback"] = ["true"]
        return primary, compat
    if fallback_name == ":":
        compat["ignore_error"] = True
        compat["or_fallback"] = [":"]
        return primary, compat
    if fallback_name == "help":
        compat["or_fallback"] = ["help"]
        return primary, compat

    return stripped, compat


def _top_level_or_parts(cmd: str) -> tuple[str, str] | None:
    in_single = False
    in_double = False
    escaped = False
    idx = 0
    while idx < len(cmd) - 1:
        token = cmd[idx]
        if escaped:
            escaped = False
            idx += 1
            continue
        if token == "\\":
            escaped = True
            idx += 1
            continue
        if token == "'" and not in_double:
            in_single = not in_single
            idx += 1
            continue
        if token == '"' and not in_single:
            in_double = not in_double
            idx += 1
            continue
        if not in_single and not in_double and cmd.startswith("||", idx):
            return cmd[:idx].rstrip(), cmd[idx + 2 :].strip()
        idx += 1
    return None


def _top_level_here_string_index(cmd: str) -> int | None:
    in_single = False
    in_double = False
    escaped = False
    idx = 0
    while idx < len(cmd) - 2:
        token = cmd[idx]
        if escaped:
            escaped = False
            idx += 1
            continue
        if token == "\\":
            escaped = True
            idx += 1
            continue
        if token == "'" and not in_double:
            in_single = not in_single
            idx += 1
            continue
        if token == '"' and not in_single:
            in_double = not in_double
            idx += 1
            continue
        if not in_single and not in_double and cmd.startswith("<<<", idx):
            return idx
        idx += 1
    return None
def _first_command_name(raw: str) -> str:
    stripped = raw.strip()
    if not stripped:
        return ""
    try:
        tokens = shlex.split(stripped, posix=True)
    except ValueError:
        tokens = stripped.split()
    return tokens[0] if tokens else ""
