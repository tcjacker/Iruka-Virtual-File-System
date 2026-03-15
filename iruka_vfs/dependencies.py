from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class VFSDependencies:
    settings: Any
    AgentWorkspace: type[Any]
    Chapter: type[Any]
    VirtualFileNode: type[Any]
    VirtualShellCommand: type[Any]
    VirtualShellSession: type[Any]
    load_project_state_payload: Callable[..., dict[str, Any]]
    repositories: Any | None = None


_dependencies: VFSDependencies | None = None


def configure_vfs_dependencies(dependencies: VFSDependencies) -> None:
    global _dependencies
    _dependencies = dependencies


def get_vfs_dependencies() -> VFSDependencies:
    if _dependencies is None:
        raise RuntimeError("iruka_vfs dependencies are not configured")
    return _dependencies
