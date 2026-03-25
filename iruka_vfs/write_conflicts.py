from __future__ import annotations


def build_overwrite_conflict(path: str, *, source: str) -> dict[str, object]:
    return {
        "ok": False,
        "conflict": True,
        "path": str(path),
        "reason": "already_exists",
        "requires_confirmation": True,
        "source": str(source),
    }


def format_overwrite_conflict_message(path: str, *, source: str) -> str:
    label = "write_file" if source == "host_write" else "redirect"
    return f"{label}: file already exists, retry with overwrite confirmation: {path}"
