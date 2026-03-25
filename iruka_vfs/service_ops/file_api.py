from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from iruka_vfs.memory_cache import get_node_content
from iruka_vfs.constants import (
    VFS_ACCESS_MODE_HOST,
    VFS_ROOT,
)
from iruka_vfs.pathing import list_children, node_path, path_is_under, resolve_parent_for_create, resolve_path
from iruka_vfs.runtime import collect_files, must_get_node
from iruka_vfs.runtime.filesystem import get_or_create_session
from iruka_vfs.runtime_seed import WorkspaceSeed
from iruka_vfs.integrations.agent.access_mode import assert_workspace_access_mode, assert_workspace_readable
from iruka_vfs.integrations.agent.shell import run_virtual_bash
from iruka_vfs.service_ops.bootstrap import ensure_virtual_workspace, normalize_workspace_path, seed_workspace_file
from iruka_vfs.workspace_mirror import (
    active_workspace_scope,
    assert_workspace_tenant,
    effective_tenant_key,
    flush_workspace_mirror,
    set_active_workspace_scope,
    set_active_workspace_tenant,
    workspace_scope_for_db,
)
from iruka_vfs.mirror.checkpoint import resolve_workspace_ref_for_flush, run_checkpoint_cycle


def flush_workspace(workspace_id: int, tenant_id: str | None = None) -> bool:
    tenant_key = effective_tenant_key(tenant_id)
    scope_key = active_workspace_scope()
    workspace_ref = resolve_workspace_ref_for_flush(
        workspace_id,
        tenant_key=tenant_key,
        scope_key=scope_key,
    )
    if workspace_ref is None:
        return True
    ok, _ = run_checkpoint_cycle(workspace_ref)
    return ok


def write_workspace_file(
    db: Session,
    workspace: Any,
    path: str,
    content: str,
    *,
    workspace_seed: WorkspaceSeed,
    tenant_id: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    tenant_key = assert_workspace_tenant(workspace, tenant_id)
    scope_key = workspace_scope_for_db(db)
    try:
        ensure_virtual_workspace(db, workspace, workspace_seed, include_tree=False, tenant_id=tenant_key)
        assert_workspace_access_mode(
            workspace,
            tenant_key=tenant_key,
            required_mode=VFS_ACCESS_MODE_HOST,
            scope_key=scope_key,
        )
        set_active_workspace_tenant(tenant_key)
        set_active_workspace_scope(scope_key)
        normalized = normalize_workspace_path(path, require_file=True)
        session = get_or_create_session(db, int(workspace.id))
        allowed, deny_reason = allow_write_path(db, session, normalized)
        if not allowed:
            raise PermissionError(f"write_file: {deny_reason}")
        return seed_workspace_file(
            db,
            int(workspace.id),
            normalized,
            content,
            op="python_write_file",
            overwrite_existing=overwrite,
            conflict_if_exists=True,
        )
    finally:
        set_active_workspace_tenant(None)
        set_active_workspace_scope(None)


def read_workspace_file(
    db: Session,
    workspace: Any,
    path: str,
    *,
    workspace_seed: WorkspaceSeed,
    tenant_id: str | None = None,
) -> str:
    tenant_key = assert_workspace_tenant(workspace, tenant_id)
    scope_key = workspace_scope_for_db(db)
    try:
        ensure_virtual_workspace(db, workspace, workspace_seed, include_tree=False, tenant_id=tenant_key)
        assert_workspace_readable(
            workspace,
            tenant_key=tenant_key,
            scope_key=scope_key,
        )
        set_active_workspace_tenant(tenant_key)
        set_active_workspace_scope(scope_key)
        normalized = normalize_workspace_path(path, require_file=True)
        session = get_or_create_session(db, int(workspace.id))
        node = resolve_path(db, int(workspace.id), int(session.cwd_node_id), normalized)
        if not node or node.node_type != "file":
            raise FileNotFoundError(f"workspace file not found: {normalized}")
        return get_node_content(db, node)
    finally:
        set_active_workspace_tenant(None)
        set_active_workspace_scope(None)


def read_workspace_directory(
    db: Session,
    workspace: Any,
    path: str,
    *,
    workspace_seed: WorkspaceSeed,
    tenant_id: str | None = None,
    recursive: bool = True,
) -> dict[str, str]:
    tenant_key = assert_workspace_tenant(workspace, tenant_id)
    scope_key = workspace_scope_for_db(db)
    try:
        ensure_virtual_workspace(db, workspace, workspace_seed, include_tree=False, tenant_id=tenant_key)
        assert_workspace_readable(
            workspace,
            tenant_key=tenant_key,
            scope_key=scope_key,
        )
        set_active_workspace_tenant(tenant_key)
        set_active_workspace_scope(scope_key)
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
    finally:
        set_active_workspace_tenant(None)
        set_active_workspace_scope(None)


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
