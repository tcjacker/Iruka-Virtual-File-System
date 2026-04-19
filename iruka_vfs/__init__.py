from __future__ import annotations

import warnings

from .dependencies import VFSDependencies, configure_vfs_dependencies
from .file_sources import ExternalFileSource, WritableFileSource
from .workspace import VirtualWorkspace, create_workspace, create_workspace_handle

__all__ = [
    "ExternalFileSource",
    "VFSDependencies",
    "VirtualWorkspace",
    "WritableFileSource",
    "configure_vfs_dependencies",
    "create_workspace",
]


def __getattr__(name: str):
    if name == "VirtualWorkspaceHandle":
        warnings.warn(
            "iruka_vfs.VirtualWorkspaceHandle is deprecated and will be removed in 0.3; "
            "use iruka_vfs.VirtualWorkspace instead",
            DeprecationWarning,
            stacklevel=2,
        )
        return VirtualWorkspace
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
