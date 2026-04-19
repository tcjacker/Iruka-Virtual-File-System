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
    ensure_mem_cache_worker as _ensure_mem_cache_worker_api,
    estimate_text_bytes as _estimate_text_bytes,
    get_node_content as _get_node_content,
    get_node_version as _get_node_version,
    snapshot_virtual_fs_cache_metrics,
    update_cache_after_write as _update_cache_after_write,
)
from iruka_vfs.models import VirtualCommandResult, WorkspaceMirror
from iruka_vfs.pathing import (
    list_children as _list_children,
    node_path as _node_path,
    path_is_under as _path_is_under,
    resolve_parent_for_create as _resolve_parent_for_create,
    resolve_path as _resolve_path,
)
from iruka_vfs.runtime import (
    apply_redirect as _apply_redirect,
    apply_unified_patch as _apply_unified_patch,
    build_simple_patch as _build_simple_patch,
    collect_files as _collect_files,
    collect_files_for_search as _collect_files_for_search,
    count_lines as _count_lines,
    exec_argv as _exec_argv,
    exec_edit as _exec_edit,
    exec_mkdir as _exec_mkdir,
    exec_patch as _exec_patch,
    exec_touch as _exec_touch,
    exec_wc as _exec_wc,
    get_or_create_child_dir as _get_or_create_child_dir,
    get_or_create_child_file as _get_or_create_child_file,
    get_or_create_root as _get_or_create_root,
    get_or_create_session as _get_or_create_session,
    mkdir_parents as _mkdir_parents,
    must_get_node as _must_get_node,
    prepare_artifacts_for_log as _prepare_artifacts_for_log,
    run_command_chain as _run_command_chain,
    run_single_command as _run_single_command,
    safe_compile as _safe_compile,
    search_display_path as _search_display_path,
    search_nodes as _search_nodes,
    search_text_lines as _search_text_lines,
    summarize_artifacts_for_log as _summarize_artifacts_for_log,
    truncate_for_log as _truncate_for_log,
    write_file as _write_file,
)
from iruka_vfs.runtime_seed import RuntimeSeed
from iruka_vfs.service_ops.access_mode import (
    assert_workspace_access_mode as _assert_workspace_access_mode,
    get_workspace_access_mode as _get_workspace_access_mode,
    set_workspace_access_mode as _set_workspace_access_mode,
    workspace_access_mode_for_runtime as _workspace_access_mode_for_runtime,
)
from iruka_vfs.service_ops.bootstrap import (
    bootstrap_workspace_snapshot as _bootstrap_workspace_snapshot,
    ensure_virtual_dir_path as _ensure_virtual_dir_path,
    ensure_virtual_workspace as _ensure_virtual_workspace,
    normalize_workspace_path as _normalize_workspace_path,
    seed_workspace_file as _seed_workspace_file,
    sync_external_file_source as _sync_external_file_source,
    workspace_access_mode_from_metadata as _workspace_access_mode_from_metadata,
)
from iruka_vfs.service_ops.file_api import (
    allow_write_path as _allow_write_path,
    flush_workspace as _flush_workspace,
    normalize_virtual_path as _normalize_virtual_path,
    read_workspace_directory as _read_workspace_directory,
    read_workspace_file as _read_workspace_file,
    resolve_target_path_for_write as _resolve_target_path_for_write,
    run_virtual_bash as _run_virtual_bash,
    tool_edit_workspace_file as _tool_edit_workspace_file,
    tool_write_workspace_file as _tool_write_workspace_file,
    write_workspace_file as _write_workspace_file,
)
from iruka_vfs.service_ops.state import (
    ensure_async_log_worker as _ensure_async_log_worker,
    enqueue_virtual_command_log as _enqueue_virtual_command_log,
    get_cached_workspace_state as _get_cached_workspace_state,
    get_redis_client as _get_redis_client,
    get_registered_runtime_seed as _get_registered_runtime_seed,
    next_ephemeral_command_id as _next_ephemeral_command_id,
    next_ephemeral_patch_id as _next_ephemeral_patch_id,
    register_runtime_seed as _register_runtime_seed,
    set_cached_workspace_state as _set_cached_workspace_state,
)
from iruka_vfs.sqlalchemy_repositories import build_sqlalchemy_repositories
from iruka_vfs.tree_view import render_tree_lines as _render_tree_lines
from iruka_vfs.tree_view import render_virtual_tree as _render_virtual_tree
from iruka_vfs.workspace_mirror import (
    active_workspace_mirror as _active_workspace_mirror,
    assert_workspace_tenant as _assert_workspace_tenant,
    build_workspace_mirror as _build_workspace_mirror,
    clone_node as _clone_node,
    delete_workspace_mirror as _delete_workspace_mirror_api,
    deserialize_workspace_mirror as _deserialize_workspace_mirror,
    effective_tenant_key as _effective_tenant_key,
    enqueue_workspace_checkpoint as _enqueue_workspace_checkpoint,
    ensure_children_sorted_locked as _ensure_children_sorted_locked,
    ensure_workspace_checkpoint_worker as _ensure_workspace_checkpoint_worker_api,
    flush_workspace_mirror as _flush_workspace_mirror_api,
    get_workspace_mirror as _get_workspace_mirror,
    get_workspace_mirror as _get_workspace_mirror_api,
    load_workspace_mirror_by_base_key as _load_workspace_mirror_by_base_key,
    mirror_has_dirty_state as _mirror_has_dirty_state,
    mirror_node_path_locked as _mirror_node_path_locked,
    rebuild_workspace_mirror_indexes_locked as _rebuild_workspace_mirror_indexes_locked,
    set_active_workspace_mirror as _set_active_workspace_mirror,
    set_active_workspace_scope as _set_active_workspace_scope,
    set_active_workspace_tenant as _set_active_workspace_tenant,
    set_workspace_mirror as _set_workspace_mirror_api,
    workspace_due_key as _workspace_due_key,
    workspace_dirty_set_key as _workspace_dirty_set_key,
    workspace_enqueued_key as _workspace_enqueued_key,
    workspace_lock as _workspace_lock_api,
    workspace_mirror_key as _workspace_mirror_key,
    workspace_scope_for_db as _workspace_scope_for_db,
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
