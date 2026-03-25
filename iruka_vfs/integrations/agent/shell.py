from __future__ import annotations

from datetime import datetime
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
    get_workspace_mirror,
    mirror_has_dirty_state,
    set_active_workspace_mirror,
    set_active_workspace_scope,
    set_active_workspace_tenant,
    set_workspace_mirror,
    workspace_lock,
    workspace_scope_for_db,
    ensure_workspace_checkpoint_worker,
)
from iruka_vfs.integrations.agent.access_mode import assert_workspace_access_mode


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
        initial_mirror = get_workspace_mirror(workspace.id, tenant_key=tenant_key, scope_key=scope_key)
        if not initial_mirror:
            raise ValueError(f"workspace mirror missing for workspace {workspace.id}")
        lock = workspace_lock(initial_mirror)
        if not lock.acquire(blocking=True):
            raise TimeoutError(f"failed to acquire workspace lock: {workspace.id}")
        try:
            mirror = get_workspace_mirror(workspace.id, tenant_key=tenant_key, scope_key=scope_key)
            if not mirror:
                raise ValueError(f"workspace mirror missing for workspace {workspace.id}")
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
            set_active_workspace_mirror(mirror)
            original_cwd_node_id = int(mirror.cwd_node_id)
            original_revision = int(mirror.revision)
            result = run_command_chain(db, session, raw_cmd)
            next_cwd_node_id = int(session.cwd_node_id or mirror.cwd_node_id)
            if next_cwd_node_id != original_cwd_node_id:
                mirror.cwd_node_id = next_cwd_node_id
                mirror.dirty_session = True
                mirror.revision += 1
            if int(mirror.revision) != original_revision or mirror_has_dirty_state(mirror):
                set_workspace_mirror(mirror)
            cwd_node = mirror.nodes.get(int(mirror.cwd_node_id)) or must_get_node(db, int(mirror.cwd_node_id))
            cwd_path = node_path(db, cwd_node)
            ended_at = datetime.utcnow()
        finally:
            set_active_workspace_mirror(None)
            set_active_workspace_tenant(None)
            set_active_workspace_scope(None)
            try:
                lock.release()
            except Exception:
                pass
        log_stdout, stdout_meta = truncate_for_log(result.stdout, VFS_COMMAND_LOG_MAX_STDOUT_CHARS)
        log_stderr, stderr_meta = truncate_for_log(result.stderr, VFS_COMMAND_LOG_MAX_STDERR_CHARS)
        log_artifacts = prepare_log_artifacts(
            dict(result.artifacts or {}),
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
        set_active_workspace_tenant(None)
        set_active_workspace_scope(None)

    return {
        "session_id": int(mirror.session_id),
        "command_id": command_id,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.exit_code,
        "artifacts": result.artifacts,
        "cwd": cwd_path,
    }


__all__ = ["run_virtual_bash"]
