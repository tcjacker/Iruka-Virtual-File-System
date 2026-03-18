from __future__ import annotations

import json
from typing import Any


def summarize_artifacts_for_log(value: Any, *, depth: int = 0) -> Any:
    if depth >= 3:
        return {"truncated": True, "reason": "max_depth"}
    if isinstance(value, dict):
        summary: dict[str, Any] = {}
        for key in (
            "path",
            "op",
            "version",
            "replacements",
            "patch_id",
            "match_count",
            "count",
            "source",
            "pattern",
            "cwd",
            "created",
            "existing",
            "files",
            "conflicts",
            "results",
            "pipeline",
            "redirect",
            "logging",
        ):
            if key not in value:
                continue
            item = value[key]
            if key in {"created", "existing", "files"} and isinstance(item, list):
                summary[key] = {"count": len(item)}
            elif key == "results" and isinstance(item, list):
                summary[key] = [
                    {
                        "cmd": entry.get("cmd"),
                        "exit_code": entry.get("exit_code"),
                    }
                    for entry in item[:8]
                    if isinstance(entry, dict)
                ]
                if len(item) > 8:
                    summary["results_truncated"] = len(item) - 8
            elif key == "pipeline" and isinstance(item, list):
                summary[key] = [
                    {
                        "argv0": (entry.get("argv") or [""])[0] if isinstance(entry, dict) else "",
                        "exit_code": entry.get("exit_code") if isinstance(entry, dict) else None,
                    }
                    for entry in item[:8]
                ]
                if len(item) > 8:
                    summary["pipeline_truncated"] = len(item) - 8
            elif key == "redirect" and isinstance(item, dict):
                summary[key] = {k: item.get(k) for k in ("path", "op", "version") if k in item}
            elif key == "logging" and isinstance(item, dict):
                summary[key] = item
            elif key == "conflicts" and isinstance(item, list):
                summary[key] = {"count": len(item)}
            else:
                summary[key] = summarize_artifacts_for_log(item, depth=depth + 1)
        return summary
    if isinstance(value, list):
        return {"count": len(value)}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(type(value).__name__)


def prepare_artifacts_for_log(artifacts: dict[str, Any], *, max_chars: int) -> dict[str, Any]:
    summarized = summarize_artifacts_for_log(artifacts)
    encoded = json.dumps(summarized, ensure_ascii=False)
    if len(encoded) <= max_chars:
        return summarized
    return {
        "truncated": True,
        "original_chars": len(encoded),
        "max_chars": max_chars,
        "summary": {
            "results": summarized.get("results"),
            "pipeline": summarized.get("pipeline"),
            "redirect": summarized.get("redirect"),
            "logging": summarized.get("logging"),
        },
    }


def truncate_for_log(text_value: str, limit: int) -> tuple[str, dict[str, Any]]:
    normalized = text_value or ""
    safe_limit = max(int(limit), 0)
    original_len = len(normalized)
    if original_len <= safe_limit or safe_limit <= 0:
        if safe_limit <= 0 and original_len > 0:
            return "", {"truncated": True, "original_length": original_len, "stored_length": 0}
        return normalized, {"truncated": False, "original_length": original_len, "stored_length": original_len}

    suffix = f"\n...[truncated {original_len - safe_limit} chars]"
    if safe_limit <= len(suffix):
        clipped = normalized[:safe_limit]
    else:
        clipped = normalized[: safe_limit - len(suffix)] + suffix
    return clipped, {"truncated": True, "original_length": original_len, "stored_length": len(clipped)}

