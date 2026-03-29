from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import PurePosixPath
import shlex
from typing import Any

from sqlalchemy.orm import Session


BOOTSTRAP_MAX_DEPTH = 4
BOOTSTRAP_MAX_SCANNED_DIRS = 48
BOOTSTRAP_MAX_SCANNED_FILES = 64
BOOTSTRAP_MAX_SUGGESTED_TARGETS = 20
BOOTSTRAP_MAX_UNIQUE_HINTS = 12


@dataclass(frozen=True)
class PathGuidanceBundle:
    workspace_bootstrap: str
    unique_filename_index: dict[str, str]
    path_shortcuts: list[str]
    discovery_hint: str


def build_workspace_path_guidance(
    service,
    db: Session,
    workspace: Any,
    *,
    workspace_outline: str,
) -> PathGuidanceBundle:
    workspace_root = service._get_or_create_root(db, int(workspace.id))
    sampled_file_paths = _sample_bootstrap_file_paths(
        service,
        db,
        int(workspace.id),
        workspace_root.id,
    )
    ranked_paths = _rank_bootstrap_paths(sampled_file_paths)
    suggested_targets = ranked_paths[:BOOTSTRAP_MAX_SUGGESTED_TARGETS]
    unique_filename_index = _build_unique_filename_index(ranked_paths)
    path_shortcuts = _build_path_shortcuts(unique_filename_index)
    workspace_bootstrap = _render_workspace_bootstrap(
        workspace_outline=workspace_outline,
        suggested_targets=suggested_targets,
        unique_filename_index=unique_filename_index,
    )
    discovery_hint = (
        "If a path is unknown, start with find /workspace -name <file>, then cat, then edit/patch. "
        "Prefer exact known paths, path_shortcuts, or unique_filename_index entries instead of guessing /workspace/<name>. "
        "If a basename appears exactly once, reuse that exact path directly before trying a guessed root-level path. "
        "Use >| when overwriting an existing file. Limited shell tails 2>/dev/null, || true, || :, and || help are supported."
    )
    return PathGuidanceBundle(
        workspace_bootstrap=workspace_bootstrap,
        unique_filename_index=unique_filename_index,
        path_shortcuts=path_shortcuts,
        discovery_hint=discovery_hint,
    )


def format_missing_path_error(command: str, target: str, *, directory_style: bool = False, db: Session | None = None, session=None) -> str:
    suffix = "No such file or directory" if directory_style else "No such file"
    return f"{command}: {target}: {suffix}.{_search_hint(target, command=command, db=db, session=session, directory_style=directory_style)}"


def _search_hint(target: str, *, command: str | None = None, db: Session | None = None, session=None, directory_style: bool = False) -> str:
    basename = str(target or "").rstrip("/").split("/")[-1]
    suggested_path = _find_unique_named_path(db, session, basename, raw_target=target)
    if suggested_path:
        if command in {"cat", "edit", "patch", "wc", "head", "rm"} and not directory_style:
            return (
                f" Most likely existing path: {suggested_path}. "
                f"Exact retry: {command} {shlex.quote(suggested_path)}. "
                f"Do not recreate /workspace/{basename} when that exact file already exists elsewhere."
            )
        return (
            f" Most likely existing path: {suggested_path}. "
            f"Exact retry: find /workspace -name {shlex.quote(basename)}. "
            f"If workspace_bootstrap or unique_filename_index lists that basename once, reuse the exact path above."
        )
    if basename and basename not in {".", ".."}:
        return (
            f" Try: find /workspace -name {shlex.quote(basename)} -> cat -> edit/patch. "
            f"If workspace_bootstrap shows a unique filename hint, use that exact path directly."
        )
    return " Try: ls -la /workspace, find /workspace -type f, or inspect workspace_bootstrap"


def _find_unique_named_path(db: Session | None, session, basename: str, *, raw_target: str) -> str | None:
    from iruka_vfs import service

    if db is None or session is None or not basename or basename in {".", ".."}:
        return None
    workspace_root = service._resolve_path(db, session.workspace_id, session.cwd_node_id, "/workspace")
    if workspace_root is None:
        return None
    normalized_target = service._normalize_virtual_path(db, session, raw_target) or raw_target
    same_name_paths = [
        path
        for path in service._find_paths(db, session.workspace_id, workspace_root, name_pattern=basename)
        if path != normalized_target
    ]
    if len(same_name_paths) != 1:
        return None
    return same_name_paths[0]


def _sample_bootstrap_file_paths(service, db: Session, workspace_id: int, root_id: int) -> list[str]:
    queue: deque[tuple[int, int]] = deque([(root_id, 0)])
    scanned_dirs = 0
    collected_files: list[str] = []

    while queue and scanned_dirs < BOOTSTRAP_MAX_SCANNED_DIRS and len(collected_files) < BOOTSTRAP_MAX_SCANNED_FILES:
        node_id, depth = queue.popleft()
        if depth > BOOTSTRAP_MAX_DEPTH:
            continue
        scanned_dirs += 1
        children = service._list_children(db, workspace_id, node_id)
        for child in children:
            if child.node_type == "file":
                collected_files.append(service._node_path(db, child))
                if len(collected_files) >= BOOTSTRAP_MAX_SCANNED_FILES:
                    break
                continue
            if depth < BOOTSTRAP_MAX_DEPTH:
                queue.append((int(child.id), depth + 1))

    return collected_files


def _rank_bootstrap_paths(paths: list[str]) -> list[str]:
    def score(path: str) -> tuple[int, int, int, str]:
        pure = PurePosixPath(path)
        depth = max(0, len(pure.parts) - 2)
        suffix = pure.suffix.lower()
        suffix_score = 0 if suffix in {".md", ".txt", ".py"} else 1
        basename_score = 0 if pure.name.lower() in {"readme.md", "changelog.md"} else 1
        return (depth, suffix_score, basename_score, path)

    return sorted(set(paths), key=score)


def _build_unique_filename_index(ranked_paths: list[str]) -> dict[str, str]:
    basename_map: dict[str, list[str]] = {}
    for path in ranked_paths:
        basename = path.rstrip("/").split("/")[-1]
        basename_map.setdefault(basename, []).append(path)
    unique_name_paths = [
        (name, paths[0])
        for name, paths in sorted(basename_map.items())
        if len(paths) == 1
    ]
    return dict(unique_name_paths[:BOOTSTRAP_MAX_UNIQUE_HINTS])


def _build_path_shortcuts(unique_filename_index: dict[str, str]) -> list[str]:
    return [f"{name}: cat {path}" for name, path in unique_filename_index.items()]


def _render_workspace_bootstrap(*, workspace_outline: str, suggested_targets: list[str], unique_filename_index: dict[str, str]) -> str:
    lines = [
        "Workspace bootstrap:",
        workspace_outline,
    ]
    if suggested_targets:
        lines.append("Suggested targets:")
        lines.extend(f"- {path}" for path in suggested_targets)
    if unique_filename_index:
        lines.append("Unique filename hints:")
        lines.extend(f"- {name} -> {path}" for name, path in unique_filename_index.items())
    lines.append("Path workflow:")
    lines.append("- Reuse an exact suggested path above when it matches the filename you need.")
    lines.append("- If a filename hint is unique, use that exact path directly instead of guessing a root-level path.")
    lines.append("- Otherwise use: find /workspace -name <file>")
    lines.append("- Then read with cat before edit/patch or >| overwrite.")
    return "\n".join(lines)
