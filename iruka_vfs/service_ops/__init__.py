from __future__ import annotations

from iruka_vfs.integrations.agent.access_mode import (
    assert_workspace_access_mode,
    get_workspace_access_mode,
    set_workspace_access_mode,
    workspace_access_mode_for_runtime,
)
from iruka_vfs.integrations.agent.shell import run_virtual_bash
from iruka_vfs.service_ops.bootstrap import (
    ensure_virtual_dir_path,
    ensure_virtual_workspace,
    normalize_workspace_path,
    refresh_virtual_workspace,
    seed_workspace_file,
    workspace_access_mode_from_metadata,
)
from iruka_vfs.service_ops.file_api import (
    allow_write_path,
    flush_workspace,
    normalize_virtual_path,
    read_workspace_directory,
    read_workspace_file,
    resolve_target_path_for_write,
    write_workspace_file,
)
from iruka_vfs.service_ops.state import (
    clear_cached_workspace_state,
    ensure_async_log_worker,
    enqueue_virtual_command_log,
    get_cached_workspace_state,
    get_redis_client,
    get_registered_runtime_seed,
    next_ephemeral_command_id,
    next_ephemeral_patch_id,
    register_runtime_seed,
    set_cached_workspace_state,
)

__all__ = [
    "allow_write_path",
    "assert_workspace_access_mode",
    "clear_cached_workspace_state",
    "ensure_async_log_worker",
    "ensure_virtual_dir_path",
    "ensure_virtual_workspace",
    "enqueue_virtual_command_log",
    "flush_workspace",
    "get_cached_workspace_state",
    "get_redis_client",
    "get_registered_runtime_seed",
    "get_workspace_access_mode",
    "next_ephemeral_command_id",
    "next_ephemeral_patch_id",
    "normalize_virtual_path",
    "normalize_workspace_path",
    "read_workspace_directory",
    "read_workspace_file",
    "refresh_virtual_workspace",
    "register_runtime_seed",
    "resolve_target_path_for_write",
    "run_virtual_bash",
    "seed_workspace_file",
    "set_cached_workspace_state",
    "set_workspace_access_mode",
    "workspace_access_mode_for_runtime",
    "workspace_access_mode_from_metadata",
    "write_workspace_file",
]
