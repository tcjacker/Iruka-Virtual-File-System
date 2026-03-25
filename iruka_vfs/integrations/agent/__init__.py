from __future__ import annotations

from importlib import import_module

_EXPORTS = {
    "assert_workspace_access_mode": ("iruka_vfs.integrations.agent.access_mode", "assert_workspace_access_mode"),
    "assert_workspace_readable": ("iruka_vfs.integrations.agent.access_mode", "assert_workspace_readable"),
    "get_workspace_access_mode": ("iruka_vfs.integrations.agent.access_mode", "get_workspace_access_mode"),
    "run_virtual_bash": ("iruka_vfs.integrations.agent.shell", "run_virtual_bash"),
    "set_workspace_access_mode": ("iruka_vfs.integrations.agent.access_mode", "set_workspace_access_mode"),
    "workspace_access_mode_for_runtime": ("iruka_vfs.integrations.agent.access_mode", "workspace_access_mode_for_runtime"),
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
