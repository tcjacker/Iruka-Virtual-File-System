from __future__ import annotations

from collections import deque
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy.orm import Session

from iruka_vfs.command_parser import split_chain
from iruka_vfs.command_runtime import run_command_chain
from iruka_vfs.constants import (
    ASYNC_COMMAND_LOGGING,
    VFS_ACCESS_MODE_AGENT,
    VFS_COMMAND_LOG_MAX_ARTIFACT_CHARS,
    VFS_COMMAND_LOG_MAX_STDERR_CHARS,
    VFS_COMMAND_LOG_MAX_STDOUT_CHARS,
    VFS_ROOT,
)
from iruka_vfs.memory_cache import ensure_mem_cache_worker
from iruka_vfs.runtime import must_get_node, truncate_for_log
from iruka_vfs.runtime.logging_support import prepare_artifacts_for_log as prepare_log_artifacts
from iruka_vfs.runtime_seed import WorkspaceSeed
from iruka_vfs.service_ops.bootstrap import ensure_virtual_workspace
from iruka_vfs.service_ops.state import (
    enqueue_virtual_command_log,
    ensure_async_log_worker,
    next_ephemeral_command_id,
)
from iruka_vfs.pathing import node_path
from iruka_vfs.workspace_mirror import (
    assert_workspace_tenant,
    mirror_has_dirty_state,
    set_active_workspace_mirror,
    set_active_workspace_scope,
    set_active_workspace_tenant,
    workspace_scope_for_db,
    ensure_workspace_checkpoint_worker,
)
from iruka_vfs.integrations.agent.access_mode import assert_workspace_access_mode


BOOTSTRAP_MAX_DEPTH = 4
BOOTSTRAP_MAX_SCANNED_DIRS = 48
BOOTSTRAP_MAX_SCANNED_FILES = 64
BOOTSTRAP_MAX_SUGGESTED_TARGETS = 20
BOOTSTRAP_MAX_UNIQUE_HINTS = 12


def _repositories():
    from iruka_vfs.dependency_resolution import resolve_vfs_repositories

    return resolve_vfs_repositories()


def _session_model():
    from iruka_vfs.dependencies import get_vfs_dependencies

    return get_vfs_dependencies().VirtualShellSession


def run_virtual_bash(
    db: Session,
    workspace: Any,
    raw_cmd: str,
    *,
    workspace_seed: WorkspaceSeed,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    from iruka_vfs import service

    scope_key = workspace_scope_for_db(db)
    try:
        tenant_key = assert_workspace_tenant(workspace, tenant_id)
        set_active_workspace_tenant(tenant_key)
        set_active_workspace_scope(scope_key)
        bind = db.get_bind()
        repositories = _repositories()
        ensure_async_log_worker(bind, repositories)
        ensure_workspace_checkpoint_worker(bind)
        ensure_mem_cache_worker(bind)
        ensure_virtual_workspace(db, workspace, workspace_seed, include_tree=False, tenant_id=tenant_key)
        assert_workspace_access_mode(
            workspace,
            tenant_key=tenant_key,
            required_mode=VFS_ACCESS_MODE_AGENT,
            scope_key=scope_key,
        )
        transaction = _execute_virtual_bash_transaction(service, db, workspace, tenant_key, scope_key, raw_cmd)
        if transaction is None:
            raise ValueError(f"workspace mirror missing for workspace {workspace.id}")
        mirror = transaction["mirror"]
        session = transaction["session"]
        result = transaction["result"]
        cwd_path = transaction["cwd_path"]
        started_at = transaction["started_at"]
        ended_at = transaction["ended_at"]
        workspace_outline = service.render_virtual_tree(db, workspace.id, max_depth=3)
        workspace_bootstrap, unique_filename_index = _build_workspace_bootstrap(
            service,
            db,
            workspace,
            workspace_outline=workspace_outline,
        )
        path_shortcuts = _build_path_shortcuts(unique_filename_index)
        discovery_hint = (
            "If a path is unknown, start with find /workspace -name <file>, then cat, then edit/patch. "
            "Prefer exact known paths, path_shortcuts, or unique_filename_index entries instead of guessing /workspace/<name>. "
            "If a basename appears exactly once, reuse that exact path directly before trying a guessed root-level path. "
            "Use >| when overwriting an existing file. Limited shell tails 2>/dev/null, || true, || :, and || help are supported."
        )
        log_stdout, stdout_meta = truncate_for_log(result.stdout, VFS_COMMAND_LOG_MAX_STDOUT_CHARS)
        log_stderr, stderr_meta = truncate_for_log(result.stderr, VFS_COMMAND_LOG_MAX_STDERR_CHARS)
        result_artifacts = dict(result.artifacts or {})
        result_artifacts["workspace_outline"] = workspace_outline
        result_artifacts["workspace_bootstrap"] = workspace_bootstrap
        result_artifacts["unique_filename_index"] = unique_filename_index
        result_artifacts["path_shortcuts"] = path_shortcuts
        result_artifacts["discovery_hint"] = discovery_hint
        log_artifacts = prepare_log_artifacts(
            result_artifacts,
            max_chars=VFS_COMMAND_LOG_MAX_ARTIFACT_CHARS,
        )
        log_artifacts["logging"] = {
            "stdout": stdout_meta,
            "stderr": stderr_meta,
        }
        log_payload = {
            "tenant_id": tenant_key,
            "session_id": session.id,
            "raw_cmd": raw_cmd,
            "parsed_json": {"segments": split_chain(raw_cmd)},
            "exit_code": result.exit_code,
            "stdout_text": log_stdout,
            "stderr_text": log_stderr,
            "artifacts_json": log_artifacts,
            "started_at": started_at,
            "ended_at": ended_at,
        }
        if ASYNC_COMMAND_LOGGING:
            enqueue_virtual_command_log(log_payload)
            command_id = next_ephemeral_command_id()
        else:
            command_id = repositories.command_log.create_command_log(db, log_payload)
    except Exception:
        db.rollback()
        raise
    finally:
        set_active_workspace_mirror(None)
        set_active_workspace_tenant(None)
        set_active_workspace_scope(None)

    return {
        "session_id": int(mirror.session_id),
        "command_id": command_id,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.exit_code,
        "artifacts": result_artifacts,
        "cwd": cwd_path,
        "workspace_outline": workspace_outline,
        "workspace_bootstrap": workspace_bootstrap,
        "unique_filename_index": unique_filename_index,
        "path_shortcuts": path_shortcuts,
        "discovery_hint": discovery_hint,
    }


def _execute_virtual_bash_transaction(service, db: Session, workspace: Any, tenant_key: str, scope_key: str | None, raw_cmd: str):
    from iruka_vfs.service_ops.state import workspace_state_uses_redis

    redis_runtime = workspace_state_uses_redis()

    def execute(mirror, _workspace_ref):
        session = _session_model()(
            id=int(mirror.session_id),
            tenant_id=tenant_key,
            workspace_id=workspace.id,
            cwd_node_id=int(mirror.cwd_node_id),
            env_json={"PWD": VFS_ROOT},
            status="active",
        )
        started_at = datetime.utcnow()
        set_active_workspace_tenant(tenant_key)
        set_active_workspace_scope(mirror.scope_key)
        if not redis_runtime:
            set_active_workspace_mirror(mirror)
        original_cwd_node_id = int(mirror.cwd_node_id)
        original_revision = int(mirror.revision)
        result = run_command_chain(db, session, raw_cmd)
        next_cwd_node_id = int(session.cwd_node_id or mirror.cwd_node_id)
        if redis_runtime:
            current = service._get_workspace_mirror_api(workspace.id, tenant_key=tenant_key, scope_key=scope_key)
            if not current:
                raise ValueError(f"workspace mirror missing for workspace {workspace.id}")
            if next_cwd_node_id != int(current.cwd_node_id):
                current.cwd_node_id = next_cwd_node_id
                current.dirty_session = True
                current.revision += 1
            if int(current.revision) != int(mirror.revision) or mirror_has_dirty_state(current):
                service._set_workspace_mirror_api(current)
            cwd_source = current
        else:
            if next_cwd_node_id != original_cwd_node_id:
                mirror.cwd_node_id = next_cwd_node_id
                mirror.dirty_session = True
                mirror.revision += 1
            if int(mirror.revision) != original_revision or mirror_has_dirty_state(mirror):
                service._set_workspace_mirror_api(mirror)
            cwd_source = mirror
        cwd_node = cwd_source.nodes.get(int(cwd_source.cwd_node_id)) or must_get_node(db, int(cwd_source.cwd_node_id))
        return {
            "mirror": mirror,
            "session": session,
            "result": result,
            "cwd_path": node_path(db, cwd_node),
            "started_at": started_at,
            "ended_at": datetime.utcnow(),
        }

    return service._execute_workspace_mirror_transaction(
        int(workspace.id),
        tenant_key=tenant_key,
        scope_key=scope_key,
        execute=execute,
    )


def _build_workspace_bootstrap(
    service,
    db: Session,
    workspace: Any,
    *,
    workspace_outline: str,
) -> tuple[str, dict[str, str]]:
    workspace_root = service._get_or_create_root(db, int(workspace.id))
    sampled_file_paths = _sample_bootstrap_file_paths(
        service,
        db,
        int(workspace.id),
        workspace_root.id,
    )
    file_paths = _rank_bootstrap_paths(sampled_file_paths)[:BOOTSTRAP_MAX_SUGGESTED_TARGETS]
    basename_map: dict[str, list[str]] = {}
    for path in file_paths:
        basename = path.rstrip("/").split("/")[-1]
        basename_map.setdefault(basename, []).append(path)
    unique_filename_index = {
        name: paths[0]
        for name, paths in sorted(basename_map.items())
        if len(paths) == 1
    }
    lines = [
        "Workspace bootstrap:",
        workspace_outline,
    ]
    unique_name_paths: list[tuple[str, str]] = []
    if file_paths:
        lines.append("Suggested targets:")
        lines.extend(f"- {path}" for path in file_paths)
        unique_name_paths = list(unique_filename_index.items())[:BOOTSTRAP_MAX_UNIQUE_HINTS]
        if unique_name_paths:
            lines.append("Unique filename hints:")
            lines.extend(f"- {name} -> {path}" for name, path in unique_name_paths)
    lines.append("Path workflow:")
    lines.append("- Reuse an exact suggested path above when it matches the filename you need.")
    lines.append("- If a filename hint is unique, use that exact path directly instead of guessing a root-level path.")
    lines.append("- Otherwise use: find /workspace -name <file>")
    lines.append("- Then read with cat before edit/patch or >| overwrite.")
    return "\n".join(lines), dict(unique_name_paths)


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


def _build_path_shortcuts(unique_filename_index: dict[str, str]) -> list[str]:
    shortcuts: list[str] = []
    for name, path in list(unique_filename_index.items())[:BOOTSTRAP_MAX_UNIQUE_HINTS]:
        shortcuts.append(f"{name}: cat {path}")
    return shortcuts


def _rank_bootstrap_paths(paths: list[str]) -> list[str]:
    def score(path: str) -> tuple[int, int, int, str]:
        pure = PurePosixPath(path)
        depth = max(0, len(pure.parts) - 2)
        suffix = pure.suffix.lower()
        suffix_score = 0 if suffix in {".md", ".txt", ".py"} else 1
        basename_score = 0 if pure.name.lower() in {"readme.md", "changelog.md"} else 1
        return (depth, suffix_score, basename_score, path)

    return sorted(set(paths), key=score)


__all__ = ["run_virtual_bash"]
