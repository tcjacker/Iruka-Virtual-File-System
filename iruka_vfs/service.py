"""Deprecated compatibility facade for legacy integrations.

Recommended host integrations should use ``from iruka_vfs import create_workspace`` and
the high-level ``VirtualWorkspace`` methods instead.
"""

from __future__ import annotations

import inspect
import warnings

from iruka_vfs.dependencies import get_vfs_dependencies
from iruka_vfs.memory_cache import (
    cache_metric_inc as _cache_metric_inc,
    snapshot_virtual_fs_cache_metrics,
)
from iruka_vfs.models import VirtualCommandResult, WorkspaceMirror
from iruka_vfs.pathing import (
    list_children as _list_children,
    resolve_path as _resolve_path,
)
from iruka_vfs.runtime_seed import RuntimeSeed
from iruka_vfs.service_ops.access_mode import (
    get_workspace_access_mode as _get_workspace_access_mode,
    set_workspace_access_mode as _set_workspace_access_mode,
)
from iruka_vfs.service_ops.bootstrap import (
    bootstrap_workspace_snapshot as _bootstrap_workspace_snapshot,
    ensure_virtual_workspace as _ensure_virtual_workspace,
)
from iruka_vfs.service_ops.file_api import (
    flush_workspace as _flush_workspace,
    read_workspace_directory as _read_workspace_directory,
    read_workspace_file as _read_workspace_file,
    run_virtual_bash as _run_virtual_bash,
    tool_edit_workspace_file as _tool_edit_workspace_file,
    tool_write_workspace_file as _tool_write_workspace_file,
    write_workspace_file as _write_workspace_file,
)
from iruka_vfs.service_ops.state import (
    get_redis_client as _get_redis_client,
    get_registered_runtime_seed as _get_registered_runtime_seed,
)
from iruka_vfs.sqlalchemy_repositories import build_sqlalchemy_repositories
from iruka_vfs.tree_view import render_virtual_tree as _render_virtual_tree
from iruka_vfs.workspace_mirror import (
    active_workspace_mirror as _active_workspace_mirror,
    effective_tenant_key as _effective_tenant_key,
    get_workspace_mirror as _get_workspace_mirror,
    mirror_node_path_locked as _mirror_node_path_locked,
    workspace_dirty_set_key as _workspace_dirty_set_key,
)

_dependencies = get_vfs_dependencies()
_repositories = _dependencies.repositories or build_sqlalchemy_repositories(_dependencies)
settings = _dependencies.settings
AgentWorkspace = _dependencies.AgentWorkspace
Chapter = _dependencies.Chapter
VirtualFileNode = _dependencies.VirtualFileNode
VirtualShellCommand = _dependencies.VirtualShellCommand
VirtualShellSession = _dependencies.VirtualShellSession


def _warn_deprecated_facade(name: str, replacement: str) -> None:
    frame = inspect.currentframe()
    helper_caller = frame.f_back if frame is not None else None
    caller = helper_caller.f_back if helper_caller is not None else None
    caller_module = str((caller.f_globals if caller is not None else {}).get("__name__", ""))
    del frame
    del helper_caller
    del caller
    if caller_module == "iruka_vfs" or caller_module.startswith("iruka_vfs."):
        return
    warnings.warn(
        f"iruka_vfs.service.{name}() is deprecated and will be removed in 0.3; use {replacement} instead",
        DeprecationWarning,
        stacklevel=2,
    )


def ensure_virtual_workspace(*args, **kwargs):
    _warn_deprecated_facade("ensure_virtual_workspace", "iruka_vfs.create_workspace()")
    return _ensure_virtual_workspace(*args, **kwargs)


def bootstrap_workspace_snapshot(*args, **kwargs):
    _warn_deprecated_facade("bootstrap_workspace_snapshot", "VirtualWorkspace.bootstrap_snapshot()")
    return _bootstrap_workspace_snapshot(*args, **kwargs)


def flush_workspace(*args, **kwargs):
    _warn_deprecated_facade("flush_workspace", "VirtualWorkspace.flush()")
    return _flush_workspace(*args, **kwargs)


def get_workspace_access_mode(*args, **kwargs):
    _warn_deprecated_facade("get_workspace_access_mode", "high-level VirtualWorkspace methods")
    return _get_workspace_access_mode(*args, **kwargs)


def set_workspace_access_mode(*args, **kwargs):
    _warn_deprecated_facade("set_workspace_access_mode", "high-level VirtualWorkspace methods")
    return _set_workspace_access_mode(*args, **kwargs)


def read_workspace_directory(*args, **kwargs):
    _warn_deprecated_facade("read_workspace_directory", "VirtualWorkspace.read_directory()")
    return _read_workspace_directory(*args, **kwargs)


def read_workspace_file(*args, **kwargs):
    _warn_deprecated_facade("read_workspace_file", "VirtualWorkspace.read_file()")
    return _read_workspace_file(*args, **kwargs)


def render_virtual_tree(*args, **kwargs):
    _warn_deprecated_facade(
        "render_virtual_tree",
        "VirtualWorkspace.ensure(..., include_tree=True) or VirtualWorkspace.file_tree()",
    )
    return _render_virtual_tree(*args, **kwargs)


def run_virtual_bash(*args, **kwargs):
    _warn_deprecated_facade("run_virtual_bash", "VirtualWorkspace.run()")
    return _run_virtual_bash(*args, **kwargs)


def tool_write_workspace_file(*args, **kwargs):
    _warn_deprecated_facade("tool_write_workspace_file", "VirtualWorkspace.write()")
    return _tool_write_workspace_file(*args, **kwargs)


def tool_edit_workspace_file(*args, **kwargs):
    _warn_deprecated_facade("tool_edit_workspace_file", "VirtualWorkspace.edit()")
    return _tool_edit_workspace_file(*args, **kwargs)


def write_workspace_file(*args, **kwargs):
    _warn_deprecated_facade("write_workspace_file", "VirtualWorkspace.write()")
    return _write_workspace_file(*args, **kwargs)

__all__ = [
    "AgentWorkspace",
    "Chapter",
    "RuntimeSeed",
    "VirtualCommandResult",
    "VirtualFileNode",
    "VirtualShellCommand",
    "VirtualShellSession",
    "WorkspaceMirror",
    "ensure_virtual_workspace",
    "flush_workspace",
    "get_workspace_access_mode",
    "read_workspace_directory",
    "read_workspace_file",
    "render_virtual_tree",
    "run_virtual_bash",
    "set_workspace_access_mode",
    "snapshot_virtual_fs_cache_metrics",
    "tool_edit_workspace_file",
    "tool_write_workspace_file",
    "write_workspace_file",
]
