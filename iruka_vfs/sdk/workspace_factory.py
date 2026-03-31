from __future__ import annotations

from typing import Any

from iruka_vfs.runtime_seed import WorkspaceSeed, build_workspace_seed
from iruka_vfs.sdk.workspace_handle import VirtualWorkspace


def create_workspace_handle(
    *,
    workspace: Any,
    tenant_id: str | None = None,
    runtime_key: str | None = None,
    workspace_seed: WorkspaceSeed | None = None,
) -> VirtualWorkspace:
    resolved_tenant_id = str(tenant_id or getattr(workspace, "tenant_id", "") or "").strip()
    if not resolved_tenant_id:
        raise ValueError("tenant_id is required either explicitly or on workspace.tenant_id")

    seed = workspace_seed
    if seed is None:
        resolved_runtime_key = str(runtime_key or getattr(workspace, "runtime_key", "") or "").strip()
        if not resolved_runtime_key:
            raise ValueError("runtime_key is required either explicitly or on workspace.runtime_key")
        seed = build_workspace_seed(
            runtime_key=resolved_runtime_key,
            tenant_id=resolved_tenant_id,
        )
    return VirtualWorkspace(
        workspace=workspace,
        workspace_seed=seed,
        tenant_id=resolved_tenant_id,
    )
