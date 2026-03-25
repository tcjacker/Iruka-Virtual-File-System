from __future__ import annotations

import re
import shlex
from typing import Any


def split_chain(raw_cmd: str) -> list[dict[str, str]]:
    tokens = re.split(r"\s*(&&|;)\s*", raw_cmd.strip())
    if len(tokens) == 1:
        return [{"op": ";", "cmd": raw_cmd.strip()}]

    pieces: list[dict[str, str]] = []
    current_op = ";"
    for token in tokens:
        token = token.strip()
        if not token:
            continue
        if token in {"&&", ";"}:
            current_op = token
            continue
        pieces.append({"op": current_op, "cmd": token})
    return pieces or [{"op": ";", "cmd": raw_cmd.strip()}]


def parse_pipeline_and_redirect(cmd: str) -> tuple[dict[str, Any], str | None]:
    try:
        tokens = list(shell_tokens(cmd))
    except ValueError as exc:
        return {}, f"parse error: {exc}"

    if not tokens:
        return {"pipeline": [], "redirect": None}, None

    pipeline: list[list[str]] = []
    current: list[str] = []
    redirect: dict[str, str] | None = None
    merge_stderr = False
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        if token == "2>&1":
            merge_stderr = True
            idx += 1
            continue
        if token == "|":
            if not current:
                return {}, "parse error: empty command before pipe"
            pipeline.append(current)
            current = []
            idx += 1
            continue
        if token in {">", ">>", ">|"}:
            if idx + 1 >= len(tokens):
                return {}, "parse error: redirect target is missing"
            redirect = {"op": token, "path": tokens[idx + 1], "force": token == ">|"}
            idx += 2
            if idx < len(tokens) and tokens[idx] == "--force":
                redirect["force"] = True
                idx += 1
            if idx < len(tokens):
                return {}, "parse error: trailing tokens after redirect target"
            break
        current.append(token)
        idx += 1

    if current:
        pipeline.append(current)
    if not pipeline:
        return {}, "parse error: empty command"
    return {"pipeline": pipeline, "redirect": redirect, "merge_stderr": merge_stderr}, None


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
            if idx + 1 < len(args) and not args[idx + 1].startswith("--"):
                values[token] = args[idx + 1]
                idx += 2
                continue
            values["flags"].add(token)
            idx += 1
            continue
        values.setdefault("_", []).append(token)
        idx += 1
    return values
