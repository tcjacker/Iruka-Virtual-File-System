from __future__ import annotations

from .dependencies import VFSDependencies, configure_vfs_dependencies
from .file_sources import ExternalFileSource, WritableFileSource
from .profile_setup import build_profile_dependencies, build_profile_persistent_dependencies
from .workspace import VirtualWorkspace, VirtualWorkspaceHandle, create_workspace, create_workspace_handle

__all__ = [
    "ExternalFileSource",
    "VFSDependencies",
    "VirtualWorkspace",
    "VirtualWorkspaceHandle",
    "WritableFileSource",
    "build_profile_dependencies",
    "build_profile_persistent_dependencies",
    "configure_vfs_dependencies",
    "create_workspace",
    "create_workspace_handle",
]
