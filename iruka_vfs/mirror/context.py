from __future__ import annotations

import hashlib

from sqlalchemy.orm import Session

from iruka_vfs.dependencies import get_vfs_dependencies
from iruka_vfs.models import WorkspaceMirror
from iruka_vfs import runtime_state

_dependencies = get_vfs_dependencies()
settings = _dependencies.settings
AgentWorkspace = _dependencies.AgentWorkspace


def workspace_tenant_key(workspace: AgentWorkspace) -> str:
    metadata = dict(workspace.metadata_json or {})
    tenant_key = str(
        getattr(workspace, "tenant_id", "")
        or metadata.get("tenant_id")
        or metadata.get("tenant")
        or settings.default_tenant_id
    ).strip()
    return tenant_key or settings.default_tenant_id


def normalize_tenant_id(tenant_id: str | None) -> str:
    normalized = str(tenant_id or "").strip()
    return normalized or settings.default_tenant_id


def assert_workspace_tenant(workspace: AgentWorkspace, tenant_id: str | None) -> str:
    expected = workspace_tenant_key(workspace)
    requested = normalize_tenant_id(tenant_id)
    if expected != requested:
        raise PermissionError(f"tenant mismatch: workspace tenant is '{expected}', requested '{requested}'")
    return expected


def set_active_workspace_mirror(mirror: WorkspaceMirror | None) -> None:
    runtime_state.active_workspace_context.mirror = mirror


def set_active_workspace_tenant(tenant_key: str | None) -> None:
    runtime_state.active_workspace_context.tenant_key = tenant_key


def set_active_workspace_scope(scope_key: str | None) -> None:
    runtime_state.active_workspace_context.scope_key = scope_key


def active_workspace_mirror(workspace_id: int | None = None) -> WorkspaceMirror | None:
    mirror = getattr(runtime_state.active_workspace_context, "mirror", None)
    if not mirror:
        return None
    if workspace_id is not None and int(mirror.workspace_id) != int(workspace_id):
        return None
    return mirror


def active_workspace_tenant() -> str | None:
    tenant_key = getattr(runtime_state.active_workspace_context, "tenant_key", None)
    if tenant_key is None:
        mirror = active_workspace_mirror()
        if mirror:
            return str(mirror.tenant_key)
    return str(tenant_key) if tenant_key else None


def active_workspace_scope() -> str | None:
    scope_key = getattr(runtime_state.active_workspace_context, "scope_key", None)
    if scope_key is None:
        mirror = active_workspace_mirror()
        if mirror:
            return str(mirror.scope_key)
    return str(scope_key) if scope_key else None


def effective_tenant_key(explicit_tenant_key: str | None = None) -> str:
    tenant_key = str(explicit_tenant_key or active_workspace_tenant() or "").strip()
    return tenant_key or settings.default_tenant_id


def workspace_scope_for_db(db: Session) -> str:
    bind = db.get_bind()
    if bind is None:
        base = str(getattr(settings, "database_url", "") or "default-db")
    else:
        url = str(bind.url.render_as_string(hide_password=False))
        if ":memory:" in url:
            return f"sqlite-memory-{id(bind)}"
        base = url
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]


def effective_workspace_scope(explicit_scope_key: str | None = None) -> str:
    scope_key = str(explicit_scope_key or active_workspace_scope() or "").strip()
    if scope_key:
        return scope_key
    base = str(getattr(settings, "database_url", "") or "default-db")
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]
