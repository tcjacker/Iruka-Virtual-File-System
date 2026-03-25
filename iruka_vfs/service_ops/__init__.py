from __future__ import annotations

from importlib import import_module

_EXPORTS = {
    "allow_write_path": ("iruka_vfs.service_ops.file_api", "allow_write_path"),
    "assert_workspace_access_mode": ("iruka_vfs.integrations.agent.access_mode", "assert_workspace_access_mode"),
    "clear_cached_workspace_state": ("iruka_vfs.service_ops.state", "clear_cached_workspace_state"),
    "ensure_async_log_worker": ("iruka_vfs.service_ops.state", "ensure_async_log_worker"),
    "ensure_virtual_dir_path": ("iruka_vfs.service_ops.bootstrap", "ensure_virtual_dir_path"),
    "ensure_virtual_workspace": ("iruka_vfs.service_ops.bootstrap", "ensure_virtual_workspace"),
    "enqueue_virtual_command_log": ("iruka_vfs.service_ops.state", "enqueue_virtual_command_log"),
    "flush_workspace": ("iruka_vfs.service_ops.file_api", "flush_workspace"),
    "get_cached_workspace_state": ("iruka_vfs.service_ops.state", "get_cached_workspace_state"),
    "get_redis_client": ("iruka_vfs.service_ops.state", "get_redis_client"),
    "get_registered_runtime_seed": ("iruka_vfs.service_ops.state", "get_registered_runtime_seed"),
    "get_workspace_access_mode": ("iruka_vfs.integrations.agent.access_mode", "get_workspace_access_mode"),
    "next_ephemeral_command_id": ("iruka_vfs.service_ops.state", "next_ephemeral_command_id"),
    "next_ephemeral_patch_id": ("iruka_vfs.service_ops.state", "next_ephemeral_patch_id"),
    "normalize_virtual_path": ("iruka_vfs.service_ops.file_api", "normalize_virtual_path"),
    "normalize_workspace_path": ("iruka_vfs.service_ops.bootstrap", "normalize_workspace_path"),
    "read_workspace_directory": ("iruka_vfs.service_ops.file_api", "read_workspace_directory"),
    "read_workspace_file": ("iruka_vfs.service_ops.file_api", "read_workspace_file"),
    "refresh_virtual_workspace": ("iruka_vfs.service_ops.bootstrap", "refresh_virtual_workspace"),
    "register_runtime_seed": ("iruka_vfs.service_ops.state", "register_runtime_seed"),
    "resolve_target_path_for_write": ("iruka_vfs.service_ops.file_api", "resolve_target_path_for_write"),
    "run_virtual_bash": ("iruka_vfs.integrations.agent.shell", "run_virtual_bash"),
    "seed_workspace_file": ("iruka_vfs.service_ops.bootstrap", "seed_workspace_file"),
    "set_cached_workspace_state": ("iruka_vfs.service_ops.state", "set_cached_workspace_state"),
    "set_workspace_access_mode": ("iruka_vfs.integrations.agent.access_mode", "set_workspace_access_mode"),
    "workspace_access_mode_for_runtime": ("iruka_vfs.integrations.agent.access_mode", "workspace_access_mode_for_runtime"),
    "workspace_access_mode_from_metadata": ("iruka_vfs.service_ops.bootstrap", "workspace_access_mode_from_metadata"),
    "write_workspace_file": ("iruka_vfs.service_ops.file_api", "write_workspace_file"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str):
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attr_name = target
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
