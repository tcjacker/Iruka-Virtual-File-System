from __future__ import annotations


def path_is_under(path: str, root: str) -> bool:
    norm_root = root.rstrip("/")
    if not norm_root:
        norm_root = "/"
    norm_path = path.rstrip("/") or "/"
    return norm_path == norm_root or norm_path.startswith(norm_root + "/")

