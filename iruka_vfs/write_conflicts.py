from __future__ import annotations


def build_overwrite_conflict(path: str, *, source: str) -> dict[str, object]:
    return {
        "ok": False,
        "conflict": True,
        "path": str(path),
        "reason": "already_exists",
        "requires_confirmation": True,
        "suggested_overwrite_mode": ">|" if source == "redirect" else "overwrite=True",
        "source": str(source),
    }


def format_overwrite_conflict_message(path: str, *, source: str) -> str:
    label = "write_file" if source == "host_write" else "redirect"
    if source == "host_write":
        return f"{label}: file already exists: {path}. Retry with overwrite=True to replace it."
    return (
        f"{label}: file already exists: {path}. "
        f"To overwrite this exact file, rerun the same command with >| {path}"
    )
