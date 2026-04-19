from __future__ import annotations

import warnings
from typing import Any

from iruka_vfs.file_sources import ExternalFileSource
from iruka_vfs.sdk.workspace_factory import create_workspace_handle as _create_workspace_handle
from iruka_vfs.sdk.workspace_handle import VirtualWorkspace


def _warn_deprecated(name: str, replacement: str) -> None:
    warnings.warn(
        f"{name} is deprecated and will be removed in 0.3; use {replacement} instead",
        DeprecationWarning,
        stacklevel=2,
    )


def create_workspace(
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
    return _create_workspace_handle(
        workspace=workspace,
        tenant_id=tenant_id,
        runtime_key=runtime_key,
        primary_file=primary_file,
        workspace_files=workspace_files,
        context_files=context_files,
        skill_files=skill_files,
        metadata=metadata,
    )


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
    _warn_deprecated("iruka_vfs.sdk.create_workspace_handle()", "iruka_vfs.sdk.create_workspace()")
    return create_workspace(
        workspace=workspace,
        tenant_id=tenant_id,
        runtime_key=runtime_key,
        primary_file=primary_file,
        workspace_files=workspace_files,
        context_files=context_files,
        skill_files=skill_files,
        metadata=metadata,
    )

__all__ = [
    "VirtualWorkspace",
    "create_workspace",
]


def __getattr__(name: str):
    if name == "VirtualWorkspaceHandle":
        warnings.warn(
            "iruka_vfs.sdk.VirtualWorkspaceHandle is deprecated and will be removed in 0.3; "
            "use iruka_vfs.sdk.VirtualWorkspace instead",
            DeprecationWarning,
            stacklevel=2,
        )
        return VirtualWorkspace
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
