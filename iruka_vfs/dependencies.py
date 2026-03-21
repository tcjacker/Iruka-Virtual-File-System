from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from iruka_vfs.configuration import RepositoryBackend, RuntimeProfile, WorkspaceStateBackend
from iruka_vfs.sqlalchemy_models import (
    AgentWorkspace as DefaultAgentWorkspace,
    VFSFileNode,
    VFSShellCommand,
    VFSShellSession,
)


def _default_project_state_payload(*args, **kwargs) -> dict[str, Any]:
    return {}


@dataclass(frozen=True)
class VFSDependencies:
    settings: Any
    AgentWorkspace: type[Any] = field(default_factory=lambda: DefaultAgentWorkspace)
    VirtualFileNode: type[Any] = field(default_factory=lambda: VFSFileNode)
    VirtualShellCommand: type[Any] = field(default_factory=lambda: VFSShellCommand)
    VirtualShellSession: type[Any] = field(default_factory=lambda: VFSShellSession)
    load_project_state_payload: Callable[..., dict[str, Any]] = field(default_factory=lambda: _default_project_state_payload)
    repositories: Any | None = None
    workspace_state_store: Any | None = None
    runtime_profile: RuntimeProfile = "persistent"
    repository_backend: RepositoryBackend | None = None
    workspace_state_backend: WorkspaceStateBackend | None = None


_dependencies: VFSDependencies | None = None


def configure_vfs_dependencies(dependencies: VFSDependencies) -> None:
    global _dependencies
    _dependencies = dependencies
    try:
        from iruka_vfs import runtime_state

        runtime_state.workspace_state_store = None
        runtime_state.vfs_repositories = None
    except Exception:
        pass


def get_vfs_dependencies() -> VFSDependencies:
    if _dependencies is None:
        raise RuntimeError("iruka_vfs dependencies are not configured")
    return _dependencies
