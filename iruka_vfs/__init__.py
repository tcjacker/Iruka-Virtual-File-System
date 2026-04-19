from __future__ import annotations

from .dependencies import VFSDependencies, configure_vfs_dependencies
from .file_sources import ExternalFileSource, WritableFileSource
from .workspace import VirtualWorkspace, VirtualWorkspaceHandle, create_workspace, create_workspace_handle

__all__ = [
    "ExternalFileSource",
    "VFSDependencies",
    "VirtualWorkspace",
    "WritableFileSource",
    "configure_vfs_dependencies",
    "create_workspace",
]
