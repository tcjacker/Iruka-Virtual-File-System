from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from iruka_vfs.command_parser import split_chain
from iruka_vfs.command_runtime import run_command_chain
from iruka_vfs.memory_cache import ensure_mem_cache_worker, get_node_content
from iruka_vfs.constants import (
    ASYNC_COMMAND_LOGGING,
    VFS_ACCESS_MODE_AGENT,
    VFS_ACCESS_MODE_HOST,
    VFS_COMMAND_LOG_MAX_ARTIFACT_CHARS,
    VFS_COMMAND_LOG_MAX_STDERR_CHARS,
    VFS_COMMAND_LOG_MAX_STDOUT_CHARS,
    VFS_ROOT,
)
from iruka_vfs.dependencies import get_vfs_dependencies
from iruka_vfs.dependency_resolution import resolve_vfs_repositories
from iruka_vfs.pathing import list_children, node_path, path_is_under, resolve_parent_for_create, resolve_path
from iruka_vfs.runtime import collect_files, must_get_node, truncate_for_log
from iruka_vfs.runtime.filesystem import get_or_create_session
from iruka_vfs.runtime_seed import RuntimeSeed
from iruka_vfs.service_ops.access_mode import assert_workspace_access_mode
from iruka_vfs.service_ops.bootstrap import ensure_virtual_workspace, normalize_workspace_path, seed_workspace_file
from iruka_vfs.service_ops.state import (
    enqueue_virtual_command_log,
    ensure_async_log_worker,
    get_workspace_state_store,
    next_ephemeral_command_id,
)
from iruka_vfs.workspace_mirror import (
    active_workspace_scope,
    assert_workspace_tenant,
    effective_tenant_key,
    enqueue_workspace_checkpoint,
    ensure_workspace_checkpoint_worker,
    flush_workspace_mirror,
    get_workspace_mirror,
    mirror_has_dirty_state,
    set_active_workspace_mirror,
    set_active_workspace_scope,
    set_active_workspace_tenant,
    set_workspace_mirror,
    workspace_lock,
    workspace_scope_for_db,
)
from iruka_vfs.runtime.logging_support import prepare_artifacts_for_log as prepare_log_artifacts

_dependencies = get_vfs_dependencies()
_repositories = resolve_vfs_repositories()
VirtualShellSession = _dependencies.VirtualShellSession
AgentWorkspace = _dependencies.AgentWorkspace


def flush_workspace(workspace_id: int, tenant_id: str | None = None) -> bool:
    tenant_key = effective_tenant_key(tenant_id)
    store = get_workspace_state_store()
    scope_key = active_workspace_scope()
    mirror = None
    if scope_key:
        mirror = store.get_workspace_mirror(
            workspace_id,
            tenant_key=tenant_key,
            scope_key=scope_key,
        )
    if mirror is None:
        mirror = store.get_workspace_mirror(workspace_id, tenant_key=tenant_key)
    if not mirror:
        return True
    workspace_ref = store.workspace_ref(mirror=mirror)
    lock = store.workspace_lock(workspace_ref=workspace_ref)
    if not lock.acquire(blocking=True):
        return False
    try:
        current = store.load_workspace_mirror(workspace_ref)
        if not current:
            return True
    finally:
        try:
            lock.release()
        except Exception:
            pass
    ok = flush_workspace_mirror(None, workspace_ref=workspace_ref)
    store.clear_checkpoint_schedule(workspace_ref)
    current = store.load_workspace_mirror(workspace_ref)
    if current and mirror_has_dirty_state(current):
        enqueue_workspace_checkpoint(workspace_ref)
    return ok


def run_virtual_bash(
    db: Session,
    workspace: AgentWorkspace,
    raw_cmd: str,
    *,
    runtime_seed: RuntimeSeed,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    try:
        tenant_key = assert_workspace_tenant(workspace, tenant_id)
        bind = db.get_bind()
        ensure_async_log_worker(bind, _repositories)
        ensure_workspace_checkpoint_worker(bind)
        ensure_mem_cache_worker(bind)
        ensure_virtual_workspace(db, workspace, runtime_seed, include_tree=False, tenant_id=tenant_key)
        assert_workspace_access_mode(
            workspace,
            tenant_key=tenant_key,
            required_mode=VFS_ACCESS_MODE_AGENT,
            scope_key=workspace_scope_for_db(db),
        )
        initial_mirror = get_workspace_mirror(workspace.id, tenant_key=tenant_key)
        if not initial_mirror:
            raise ValueError(f"workspace mirror missing for workspace {workspace.id}")
        lock = workspace_lock(initial_mirror)
        if not lock.acquire(blocking=True):
            raise TimeoutError(f"failed to acquire workspace lock: {workspace.id}")
        try:
            mirror = get_workspace_mirror(workspace.id, tenant_key=tenant_key)
            if not mirror:
                raise ValueError(f"workspace mirror missing for workspace {workspace.id}")
            session = VirtualShellSession(
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
            command_id = _repositories.command_log.create_command_log(db, log_payload)
    except Exception:
        db.rollback()
        raise

    return {
        "session_id": int(mirror.session_id),
        "command_id": command_id,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.exit_code,
        "artifacts": result.artifacts,
        "cwd": cwd_path,
    }


def write_workspace_file(
    db: Session,
    workspace: AgentWorkspace,
    path: str,
    content: str,
    *,
    runtime_seed: RuntimeSeed,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    tenant_key = assert_workspace_tenant(workspace, tenant_id)
    ensure_virtual_workspace(db, workspace, runtime_seed, include_tree=False, tenant_id=tenant_key)
    assert_workspace_access_mode(
        workspace,
        tenant_key=tenant_key,
        required_mode=VFS_ACCESS_MODE_HOST,
        scope_key=workspace_scope_for_db(db),
    )
    normalized = normalize_workspace_path(path, require_file=True)
    session = get_or_create_session(db, int(workspace.id))
    allowed, deny_reason = allow_write_path(db, session, normalized)
    if not allowed:
        raise PermissionError(f"write_file: {deny_reason}")
    return seed_workspace_file(db, int(workspace.id), normalized, content, op="python_write_file")


def read_workspace_file(
    db: Session,
    workspace: AgentWorkspace,
    path: str,
    *,
    runtime_seed: RuntimeSeed,
    tenant_id: str | None = None,
) -> str:
    tenant_key = assert_workspace_tenant(workspace, tenant_id)
    ensure_virtual_workspace(db, workspace, runtime_seed, include_tree=False, tenant_id=tenant_key)
    assert_workspace_access_mode(
        workspace,
        tenant_key=tenant_key,
        required_mode=VFS_ACCESS_MODE_HOST,
        scope_key=workspace_scope_for_db(db),
    )
    normalized = normalize_workspace_path(path, require_file=True)
    session = get_or_create_session(db, int(workspace.id))
    node = resolve_path(db, int(workspace.id), int(session.cwd_node_id), normalized)
    if not node or node.node_type != "file":
        raise FileNotFoundError(f"workspace file not found: {normalized}")
    return get_node_content(db, node)


def read_workspace_directory(
    db: Session,
    workspace: AgentWorkspace,
    path: str,
    *,
    runtime_seed: RuntimeSeed,
    tenant_id: str | None = None,
    recursive: bool = True,
) -> dict[str, str]:
    tenant_key = assert_workspace_tenant(workspace, tenant_id)
    ensure_virtual_workspace(db, workspace, runtime_seed, include_tree=False, tenant_id=tenant_key)
    assert_workspace_access_mode(
        workspace,
        tenant_key=tenant_key,
        required_mode=VFS_ACCESS_MODE_HOST,
        scope_key=workspace_scope_for_db(db),
    )
    normalized = normalize_workspace_path(path)
    session = get_or_create_session(db, int(workspace.id))
    node = resolve_path(db, int(workspace.id), int(session.cwd_node_id), normalized)
    if not node or node.node_type != "dir":
        raise FileNotFoundError(f"workspace directory not found: {normalized}")
    files = collect_files(db, int(workspace.id), int(node.id)) if recursive else [
        child for child in list_children(db, int(workspace.id), int(node.id)) if child.node_type == "file"
    ]
    rows = [(node_path(db, item), get_node_content(db, item)) for item in files]
    rows.sort(key=lambda item: item[0])
    return {path_key: content_value for path_key, content_value in rows}


def resolve_target_path_for_write(db: Session, session: VirtualShellSession, raw_path: str, *, node=None) -> str | None:
    if node:
        return node_path(db, node)
    parent, leaf = resolve_parent_for_create(db, session.workspace_id, session.cwd_node_id, raw_path)
    if not parent or not leaf:
        return None
    parent_path = node_path(db, parent)
    if parent_path == "/":
        return f"/{leaf}"
    return f"{parent_path.rstrip('/')}/{leaf}"


def normalize_virtual_path(db: Session, session: VirtualShellSession, raw_path: str) -> str | None:
    if not raw_path:
        return None
    if raw_path.startswith("/"):
        return raw_path.rstrip("/") or "/"
    cwd = must_get_node(db, session.cwd_node_id)
    cwd_path = node_path(db, cwd)
    joined = f"{cwd_path.rstrip('/')}/{raw_path}" if cwd_path != "/" else f"/{raw_path}"
    return joined.rstrip("/") or "/"


def allow_write_path(db: Session, session: VirtualShellSession, path: str) -> tuple[bool, str]:
    if path_is_under(path, VFS_ROOT):
        return True, ""
    return False, f"write denied: path is outside workspace ({path})"
