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
        import queue
        import threading
        from collections import OrderedDict, deque

        from iruka_vfs import runtime_state

        runtime_state.workspace_state_store = None
        runtime_state.vfs_repositories = None
        runtime_state.runtime_seeds.clear()
        runtime_state.active_workspace_context = threading.local()

        runtime_state.local_workspace_mirrors.clear()
        runtime_state.local_workspace_indexes.clear()
        runtime_state.local_workspace_locks.clear()
        runtime_state.local_checkpoint_condition = threading.Condition()
        runtime_state.local_checkpoint_queue = deque()
        runtime_state.local_checkpoint_enqueued.clear()
        runtime_state.local_dirty_workspaces.clear()
        runtime_state.local_checkpoint_due_at.clear()
        runtime_state.local_workspace_errors.clear()
        runtime_state.local_dead_letter_workspaces.clear()
        runtime_state.local_dead_letter_payloads.clear()
        runtime_state.local_retry_counts.clear()

        runtime_state.mem_cache_entries.clear()
        runtime_state.mem_cache_lru = OrderedDict()
        runtime_state.mem_cache_dirty_ids.clear()
        runtime_state.mem_cache_current_bytes = 0
        runtime_state.mem_cache_worker_started = False
        runtime_state.mem_cache_session_maker = None
        runtime_state.mem_cache_metrics = {
            "cache_hit": 0,
            "cache_miss": 0,
            "write_ops": 0,
            "flush_ok": 0,
            "flush_conflict": 0,
            "flush_error": 0,
            "checkpoint_retry": 0,
            "checkpoint_dead_letter": 0,
            "checkpoint_requeue": 0,
            "evicted": 0,
        }

        runtime_state.workspace_checkpoint_worker_started = False
        runtime_state.workspace_checkpoint_session_maker = None

        runtime_state.log_queue = queue.Queue(maxsize=5000)
        runtime_state.log_worker_started = False
        runtime_state.log_engine = None
        runtime_state.log_session_maker = None

        runtime_state.redis_client = None
    except Exception:
        pass


def get_vfs_dependencies() -> VFSDependencies:
    if _dependencies is None:
        raise RuntimeError("iruka_vfs dependencies are not configured")
    return _dependencies
