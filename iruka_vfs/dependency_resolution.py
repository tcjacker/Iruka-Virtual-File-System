from __future__ import annotations

from iruka_vfs.configuration import RepositoryBackend, RuntimeProfile, WorkspaceStateBackend
from iruka_vfs.dependencies import get_vfs_dependencies
from iruka_vfs.in_memory_repositories import build_in_memory_repositories
from iruka_vfs.pgsql_repositories import build_pgsql_repositories
from iruka_vfs import runtime_state
from iruka_vfs.sqlalchemy_repositories import build_sqlalchemy_repositories


def _resolve_runtime_profile(dependencies) -> RuntimeProfile:
    return str(getattr(dependencies, "runtime_profile", "persistent") or "persistent")  # type: ignore[return-value]


def _resolve_repository_backend(dependencies) -> RepositoryBackend:
    explicit = getattr(dependencies, "repository_backend", None)
    if explicit:
        return str(explicit)  # type: ignore[return-value]
    profile = _resolve_runtime_profile(dependencies)
    if profile in {"ephemeral-local", "ephemeral-redis"}:
        return "memory"
    return "pgsql"


def _resolve_workspace_state_backend(dependencies) -> WorkspaceStateBackend:
    explicit = getattr(dependencies, "workspace_state_backend", None)
    if explicit:
        return str(explicit)  # type: ignore[return-value]
    profile = _resolve_runtime_profile(dependencies)
    if profile == "ephemeral-local":
        return "local-memory"
    return "redis"


def resolve_vfs_repositories():
    if runtime_state.vfs_repositories is not None:
        return runtime_state.vfs_repositories
    dependencies = get_vfs_dependencies()
    repositories = dependencies.repositories
    if repositories is None:
        backend = _resolve_repository_backend(dependencies)
        if backend == "memory":
            repositories = build_in_memory_repositories(dependencies)
        elif backend == "pgsql":
            repositories = build_pgsql_repositories(dependencies)
        else:
            repositories = build_sqlalchemy_repositories(dependencies)
    runtime_state.vfs_repositories = repositories
    return repositories


def resolve_workspace_state_backend(dependencies=None) -> WorkspaceStateBackend:
    return _resolve_workspace_state_backend(dependencies or get_vfs_dependencies())


def resolve_repository_backend(dependencies=None) -> RepositoryBackend:
    return _resolve_repository_backend(dependencies or get_vfs_dependencies())
