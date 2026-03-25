from __future__ import annotations

from typing import Any

from iruka_vfs.integrations.agent.access_mode import (
    get_workspace_access_mode,
    set_workspace_access_mode,
)
from iruka_vfs.service_ops.bootstrap import workspace_access_mode_from_metadata
from iruka_vfs.workspace_mirror import get_workspace_mirror


def workspace_access_mode_for_runtime(
    workspace: Any | None,
    workspace_id: int,
    tenant_key: str,
    scope_key: str | None = None,
) -> str:
    mirror = get_workspace_mirror(workspace_id, tenant_key=tenant_key, scope_key=scope_key)
    if mirror:
        with mirror.lock:
            return workspace_access_mode_from_metadata(dict(mirror.workspace_metadata or {}))
    return workspace_access_mode_from_metadata(dict(getattr(workspace, "metadata_json", {}) or {}))


def assert_workspace_access_mode(
    workspace: Any,
    *,
    tenant_key: str,
    required_mode: str,
    scope_key: str | None = None,
) -> str:
    actual_mode = workspace_access_mode_for_runtime(workspace, int(workspace.id), tenant_key, scope_key=scope_key)
    if actual_mode != required_mode:
        raise PermissionError(f"workspace access mode is '{actual_mode}', required '{required_mode}'")
    return actual_mode


def assert_workspace_readable(
    workspace: Any,
    *,
    tenant_key: str,
    scope_key: str | None = None,
) -> str:
    actual_mode = workspace_access_mode_for_runtime(workspace, int(workspace.id), tenant_key, scope_key=scope_key)
    if actual_mode not in {"host", "agent"}:
        raise PermissionError(f"workspace access mode is '{actual_mode}', required readable mode")
    return actual_mode

__all__ = [
    "assert_workspace_access_mode",
    "assert_workspace_readable",
    "get_workspace_access_mode",
    "set_workspace_access_mode",
    "workspace_access_mode_for_runtime",
]
