from __future__ import annotations

from typing import Any

from iruka_vfs.configuration import RuntimeProfile
from iruka_vfs.dependencies import VFSDependencies
from iruka_vfs.sqlalchemy_models import (
    AgentWorkspace as DefaultAgentWorkspace,
    VFSFileNode,
    VFSShellCommand,
    VFSShellSession,
)


def _default_project_state_payload(*args, **kwargs) -> dict[str, Any]:
    return {}


def build_profile_dependencies(
    *,
    settings: Any,
    VirtualFileNode: type[Any] | None = None,
    VirtualShellCommand: type[Any] | None = None,
    VirtualShellSession: type[Any] | None = None,
    load_project_state_payload=None,
    runtime_profile: RuntimeProfile = "persistent",
    repositories: Any | None = None,
    workspace_state_store: Any | None = None,
    repository_backend: str | None = None,
    workspace_state_backend: str | None = None,
) -> VFSDependencies:
    return VFSDependencies(
        settings=settings,
        AgentWorkspace=DefaultAgentWorkspace,
        VirtualFileNode=VirtualFileNode or VFSFileNode,
        VirtualShellCommand=VirtualShellCommand or VFSShellCommand,
        VirtualShellSession=VirtualShellSession or VFSShellSession,
        load_project_state_payload=load_project_state_payload or _default_project_state_payload,
        repositories=repositories,
        workspace_state_store=workspace_state_store,
        runtime_profile=runtime_profile,
        repository_backend=repository_backend,
        workspace_state_backend=workspace_state_backend,
    )


def build_profile_persistent_dependencies(
    *,
    settings: Any,
    load_project_state_payload=None,
    repositories: Any | None = None,
    workspace_state_store: Any | None = None,
    repository_backend: str | None = None,
    workspace_state_backend: str | None = None,
) -> VFSDependencies:
    return build_profile_dependencies(
        settings=settings,
        load_project_state_payload=load_project_state_payload,
        runtime_profile="persistent",
        repositories=repositories,
        workspace_state_store=workspace_state_store,
        repository_backend=repository_backend,
        workspace_state_backend=workspace_state_backend,
    )
