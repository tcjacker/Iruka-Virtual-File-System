from __future__ import annotations

from dataclasses import dataclass
from typing import Any


GUIDANCE_STATE_KEY = "_agent_task_guidance_state"
MAX_TRACKED_PATHS = 32


@dataclass(frozen=True)
class TaskGuidanceBundle:
    state: dict[str, list[str]]
    task_guidance: dict[str, Any]
    verification_hint: str
    modified_paths: list[str]


@dataclass(frozen=True)
class ShellGuidancePayload:
    artifacts: dict[str, Any]
    response_fields: dict[str, Any]


def build_task_guidance(previous_state: dict[str, Any] | None, result_artifacts: dict[str, Any] | None) -> TaskGuidanceBundle:
    current_summary = _summarize_result_artifacts(result_artifacts or {})
    state = _normalize_state(previous_state)
    updated_state = _apply_summary(state, current_summary)

    modified_paths = [
        path for path in updated_state["written_paths"] if path not in set(updated_state["removed_paths"])
    ]
    required_paths = [
        path for path in modified_paths if path not in set(updated_state["verified_paths"]) and path not in set(updated_state["removed_paths"])
    ]
    possible_missing_targets = [
        path
        for path in updated_state["target_candidates"]
        if path not in set(modified_paths) and path not in set(updated_state["removed_paths"])
    ]
    if not modified_paths:
        possible_missing_targets = []
    recently_verified_paths = [
        path for path in current_summary["verification_reads"] if path in set(updated_state["verified_paths"])
    ]
    suggested_readback = f"cat {' '.join(required_paths)}" if required_paths else ""

    task_guidance = {
        "write_summary": {
            "recent_paths": current_summary["written_paths"],
            "modified_paths": modified_paths,
            "removed_paths": list(updated_state["removed_paths"]),
        },
        "verification": {
            "changed_paths": modified_paths,
            "required_paths": required_paths,
            "pending_verification_paths": required_paths,
            "verified_paths": list(updated_state["verified_paths"]),
            "recently_verified_paths": _unique_ordered(recently_verified_paths),
            "possible_missing_targets": possible_missing_targets,
            "suggested_readback": suggested_readback,
        },
    }

    verification_hint = _build_verification_hint(
        modified_paths=modified_paths,
        required_paths=required_paths,
        possible_missing_targets=possible_missing_targets,
        suggested_readback=suggested_readback,
    )
    return TaskGuidanceBundle(
        state=updated_state,
        task_guidance=task_guidance,
        verification_hint=verification_hint,
        modified_paths=modified_paths,
    )


def assemble_shell_guidance_payload(
    base_artifacts: dict[str, Any],
    *,
    workspace_outline: str,
    path_guidance,
    task_guidance: TaskGuidanceBundle,
) -> ShellGuidancePayload:
    artifacts = dict(base_artifacts)
    artifacts["workspace_outline"] = workspace_outline
    artifacts["workspace_bootstrap"] = path_guidance.workspace_bootstrap
    artifacts["unique_filename_index"] = path_guidance.unique_filename_index
    artifacts["path_shortcuts"] = path_guidance.path_shortcuts
    artifacts["discovery_hint"] = path_guidance.discovery_hint
    artifacts["task_guidance"] = task_guidance.task_guidance
    artifacts["verification_hint"] = task_guidance.verification_hint
    artifacts["modified_paths"] = task_guidance.modified_paths
    return ShellGuidancePayload(
        artifacts=artifacts,
        response_fields={
            "workspace_outline": workspace_outline,
            "workspace_bootstrap": path_guidance.workspace_bootstrap,
            "unique_filename_index": path_guidance.unique_filename_index,
            "path_shortcuts": path_guidance.path_shortcuts,
            "discovery_hint": path_guidance.discovery_hint,
            "task_guidance": task_guidance.task_guidance,
            "verification_hint": task_guidance.verification_hint,
            "modified_paths": task_guidance.modified_paths,
        },
    )


def _build_verification_hint(
    *,
    modified_paths: list[str],
    required_paths: list[str],
    possible_missing_targets: list[str],
    suggested_readback: str,
) -> str:
    if required_paths:
        message = (
            f"Before finishing, verify every changed file with: {suggested_readback}. "
            f"Outstanding paths: {', '.join(required_paths)}. "
            "Reuse these exact paths in the final answer."
        )
        if possible_missing_targets:
            message = f"{message} Also review possible untouched targets: {', '.join(possible_missing_targets)}."
        return message
    if modified_paths:
        message = (
            f"Changed files tracked in this session: {', '.join(modified_paths)}. "
            "Reuse these exact paths in the final answer."
        )
        if possible_missing_targets:
            message = f"{message} Possible untouched targets: {', '.join(possible_missing_targets)}."
        return message
    return ""


def _summarize_result_artifacts(result_artifacts: dict[str, Any]) -> dict[str, list[str]]:
    read_paths: list[str] = []
    verification_reads: list[str] = []
    written_paths: list[str] = []
    removed_paths: list[str] = []
    target_candidates: list[str] = []
    events: list[dict[str, str]] = []

    for result_entry in result_artifacts.get("results", []):
        entry_artifacts = result_entry.get("artifacts") or {}
        pipeline = entry_artifacts.get("pipeline") or []
        for stage in pipeline:
            argv = stage.get("argv") or []
            if not argv:
                continue
            summary = _summarize_command(argv[0], stage.get("artifacts") or {})
            read_paths.extend(summary["read_paths"])
            verification_reads.extend(summary["verification_reads"])
            written_paths.extend(summary["written_paths"])
            removed_paths.extend(summary["removed_paths"])
            target_candidates.extend(summary["target_candidates"])
            events.extend(summary["events"])
        redirect = entry_artifacts.get("redirect")
        if isinstance(redirect, dict):
            path = redirect.get("path")
            if isinstance(path, str) and path:
                written_paths.append(path)
                target_candidates.append(path)
                events.append({"kind": "write", "path": path})

    return {
        "read_paths": _unique_ordered(read_paths),
        "verification_reads": _unique_ordered(verification_reads),
        "written_paths": _unique_ordered(written_paths),
        "removed_paths": _unique_ordered(removed_paths),
        "target_candidates": _unique_ordered(target_candidates),
        "events": events,
    }


def _summarize_command(command: str, artifacts: dict[str, Any]) -> dict[str, list[str]]:
    read_paths: list[str] = []
    verification_reads: list[str] = []
    written_paths: list[str] = []
    removed_paths: list[str] = []
    target_candidates: list[str] = []
    events: list[dict[str, str]] = []
    files = [path for path in artifacts.get("files", []) if isinstance(path, str)]
    path = artifacts.get("path")
    source = artifacts.get("source")
    target = artifacts.get("target")

    if command in {"cat", "head", "wc", "sort"}:
        read_paths.extend(files)
        verification_reads.extend(files)
        target_candidates.extend(files)
        events.extend({"kind": "read", "path": file_path} for file_path in files)
        events.extend({"kind": "verify_read", "path": file_path} for file_path in files)
    elif command in {"edit", "patch"}:
        if isinstance(path, str) and path:
            read_paths.append(path)
            written_paths.append(path)
            target_candidates.append(path)
            events.append({"kind": "read", "path": path})
            events.append({"kind": "write", "path": path})
    elif command == "cp":
        if isinstance(source, str) and source:
            read_paths.append(source)
            events.append({"kind": "read", "path": source})
        if isinstance(target, str) and target:
            written_paths.append(target)
            target_candidates.append(target)
            events.append({"kind": "write", "path": target})
    elif command == "mv":
        if isinstance(source, str) and source:
            read_paths.append(source)
            events.append({"kind": "read", "path": source})
        if isinstance(target, str) and target:
            written_paths.append(target)
            target_candidates.append(target)
            events.append({"kind": "write", "path": target})
    elif command == "rm":
        if isinstance(path, str) and path:
            removed_paths.append(path)
            target_candidates.append(path)
            events.append({"kind": "remove", "path": path})
    elif command == "touch":
        for created in artifacts.get("created", []):
            if isinstance(created, str) and created:
                written_paths.append(created)
                target_candidates.append(created)
                events.append({"kind": "write", "path": created})

    return {
        "read_paths": _unique_ordered(read_paths),
        "verification_reads": _unique_ordered(verification_reads),
        "written_paths": _unique_ordered(written_paths),
        "removed_paths": _unique_ordered(removed_paths),
        "target_candidates": _unique_ordered(target_candidates),
        "events": events,
    }


def _apply_summary(state: dict[str, list[str]], summary: dict[str, list[str]]) -> dict[str, list[str]]:
    read_paths = list(state["read_paths"])
    written_paths = list(state["written_paths"])
    verified_paths = list(state["verified_paths"])
    removed_paths = list(state["removed_paths"])
    target_candidates = list(state["target_candidates"])

    for path in summary["target_candidates"]:
        _append_unique(target_candidates, path)

    for event in summary["events"]:
        kind = event["kind"]
        path = event["path"]
        if kind == "read":
            _append_unique(read_paths, path)
            continue
        if kind == "write":
            _append_unique(written_paths, path)
            if path in verified_paths:
                verified_paths = [item for item in verified_paths if item != path]
            continue
        if kind == "remove":
            _append_unique(removed_paths, path)
            continue
        if kind == "verify_read":
            if path in written_paths and path not in verified_paths and path not in removed_paths:
                verified_paths.append(path)

    return {
        "read_paths": _trim_paths(read_paths),
        "written_paths": _trim_paths(written_paths),
        "verified_paths": _trim_paths(verified_paths),
        "removed_paths": _trim_paths(removed_paths),
        "target_candidates": _trim_paths(target_candidates),
    }


def _normalize_state(previous_state: dict[str, Any] | None) -> dict[str, list[str]]:
    state = previous_state or {}
    return {
        "read_paths": _trim_paths(_coerce_path_list(state.get("read_paths"))),
        "written_paths": _trim_paths(_coerce_path_list(state.get("written_paths"))),
        "verified_paths": _trim_paths(_coerce_path_list(state.get("verified_paths"))),
        "removed_paths": _trim_paths(_coerce_path_list(state.get("removed_paths"))),
        "target_candidates": _trim_paths(_coerce_path_list(state.get("target_candidates"))),
    }


def _coerce_path_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return _unique_ordered([item for item in value if isinstance(item, str) and item])


def _trim_paths(paths: list[str]) -> list[str]:
    return paths[-MAX_TRACKED_PATHS:]


def _append_unique(items: list[str], path: str) -> None:
    if path and path not in items:
        items.append(path)


def _unique_ordered(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for path in paths:
        if not path or path in seen:
            continue
        seen.add(path)
        out.append(path)
    return out
