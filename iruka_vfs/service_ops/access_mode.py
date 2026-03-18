from __future__ import annotations

from sqlalchemy.orm import Session

from iruka_vfs.constants import VFS_ACCESS_MODE_AGENT, VFS_ACCESS_MODE_HOST
from iruka_vfs.runtime_seed import RuntimeSeed
from iruka_vfs.service_ops.bootstrap import ensure_virtual_workspace, workspace_access_mode_from_metadata
from iruka_vfs.workspace_mirror import (
    assert_workspace_tenant,
    get_workspace_mirror,
    set_workspace_mirror,
    workspace_lock,
    workspace_scope_for_db,
)
from iruka_vfs.dependencies import get_vfs_dependencies
from iruka_vfs.sqlalchemy_repositories import build_sqlalchemy_repositories

_dependencies = get_vfs_dependencies()
_repositories = _dependencies.repositories or build_sqlalchemy_repositories(_dependencies)
AgentWorkspace = _dependencies.AgentWorkspace


def workspace_access_mode_for_runtime(workspace: AgentWorkspace | None, workspace_id: int, tenant_key: str, scope_key: str | None = None) -> str:
    mirror = get_workspace_mirror(workspace_id, tenant_key=tenant_key, scope_key=scope_key)
    if mirror:
        with mirror.lock:
            return workspace_access_mode_from_metadata(dict(mirror.workspace_metadata or {}))
    return workspace_access_mode_from_metadata(dict(getattr(workspace, "metadata_json", {}) or {}))


def assert_workspace_access_mode(
    workspace: AgentWorkspace,
    *,
    tenant_key: str,
    required_mode: str,
    scope_key: str | None = None,
) -> str:
    actual_mode = workspace_access_mode_for_runtime(workspace, int(workspace.id), tenant_key, scope_key=scope_key)
    if actual_mode != required_mode:
        raise PermissionError(f"workspace access mode is '{actual_mode}', required '{required_mode}'")
    return actual_mode


def get_workspace_access_mode(
    db: Session,
    workspace: AgentWorkspace,
    *,
    runtime_seed: RuntimeSeed,
    tenant_id: str | None = None,
) -> str:
    tenant_key = assert_workspace_tenant(workspace, tenant_id)
    scope_key = workspace_scope_for_db(db)
    ensure_virtual_workspace(
        db,
        workspace,
        runtime_seed,
        include_tree=False,
        tenant_id=tenant_key,
    )
    return workspace_access_mode_for_runtime(workspace, int(workspace.id), tenant_key, scope_key=scope_key)


def set_workspace_access_mode(
    db: Session,
    workspace: AgentWorkspace,
    *,
    runtime_seed: RuntimeSeed,
    mode: str,
    tenant_id: str | None = None,
    flush: bool = True,
) -> str:
    tenant_key = assert_workspace_tenant(workspace, tenant_id)
    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode not in {VFS_ACCESS_MODE_HOST, VFS_ACCESS_MODE_AGENT}:
        raise ValueError(f"unsupported workspace access mode: {mode}")
    scope_key = workspace_scope_for_db(db)
    ensure_virtual_workspace(
        db,
        workspace,
        runtime_seed,
        include_tree=False,
        tenant_id=tenant_key,
    )
    if flush:
        from iruka_vfs.service_ops.file_api import flush_workspace

        flush_workspace(int(workspace.id), tenant_id=tenant_key)
    mirror = get_workspace_mirror(int(workspace.id), tenant_key=tenant_key, scope_key=scope_key)
    if not mirror:
        raise ValueError(f"workspace mirror missing for workspace {workspace.id}")
    lock, _ = workspace_lock(mirror)
    if not lock.acquire(blocking=True):
        raise TimeoutError(f"failed to acquire workspace lock: {workspace.id}")
    try:
        current = get_workspace_mirror(int(workspace.id), tenant_key=tenant_key, scope_key=scope_key)
        if not current:
            raise ValueError(f"workspace mirror missing for workspace {workspace.id}")
        with current.lock:
            metadata = dict(current.workspace_metadata or {})
            current_mode = workspace_access_mode_from_metadata(metadata)
            if current_mode == normalized_mode:
                return current_mode
            metadata["virtual_access_mode"] = normalized_mode
            current.workspace_metadata = metadata
            current.dirty_workspace_metadata = True
            current.revision += 1
            workspace.metadata_json = metadata
            _repositories.workspace.update_workspace_metadata(
                db,
                workspace_id=int(workspace.id),
                tenant_key=tenant_key,
                metadata_json=metadata,
            )
            set_workspace_mirror(current)
            return normalized_mode
    finally:
        try:
            lock.release()
        except Exception:
            pass
