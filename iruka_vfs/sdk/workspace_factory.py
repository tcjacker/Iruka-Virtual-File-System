from __future__ import annotations

from typing import Any

from iruka_vfs.file_sources import ExternalFileSource
from iruka_vfs.runtime_seed import RuntimeSeed
from iruka_vfs.sdk.workspace_handle import VirtualWorkspace


def create_workspace_handle(
    *,
    workspace: Any,
    tenant_id: str | None = None,
    runtime_key: str | None = None,
    primary_file: ExternalFileSource | None = None,
    workspace_files: dict[str, str] | None = None,
    context_files: dict[str, str] | None = None,
    skill_files: dict[str, str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> VirtualWorkspace:
    resolved_tenant_id = str(tenant_id or getattr(workspace, "tenant_id", "") or "").strip()
    if not resolved_tenant_id:
        raise ValueError("tenant_id is required either explicitly or on workspace.tenant_id")

    resolved_runtime_key = str(runtime_key or getattr(workspace, "runtime_key", "") or "").strip()
    if not resolved_runtime_key:
        raise ValueError("runtime_key is required either explicitly or on workspace.runtime_key")

    seed = RuntimeSeed(
        runtime_key=resolved_runtime_key,
        tenant_id=resolved_tenant_id,
        primary_file=primary_file,
        workspace_files=dict(workspace_files or {}),
        context_files=dict(context_files or {}),
        skill_files=dict(skill_files or {}),
        metadata=dict(metadata or {}),
    )
    return VirtualWorkspace(
        workspace=workspace,
        runtime_seed=seed,
        tenant_id=resolved_tenant_id,
    )

