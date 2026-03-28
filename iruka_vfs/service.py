from __future__ import annotations

from iruka_vfs.constants import MEMORY_CACHE_ENABLED, VFS_ROOT
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
    exec_find as _exec_find,
    exec_mkdir as _exec_mkdir,
    exec_patch as _exec_patch,
    exec_touch as _exec_touch,
    exec_wc as _exec_wc,
    find_paths as _find_paths,
    format_missing_path_error as _format_missing_path_error,
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
from iruka_vfs.runtime_seed import RuntimeSeed, WorkspaceSeed
from iruka_vfs.integrations.agent.access_mode import (
    assert_workspace_access_mode as _assert_workspace_access_mode,
    get_workspace_access_mode,
    set_workspace_access_mode,
    workspace_access_mode_for_runtime as _workspace_access_mode_for_runtime,
)
from iruka_vfs.integrations.agent.shell import run_virtual_bash
from iruka_vfs.service_ops.bootstrap import (
    ensure_virtual_dir_path as _ensure_virtual_dir_path,
    refresh_virtual_workspace,
    ensure_virtual_workspace,
    normalize_workspace_path as _normalize_workspace_path,
    seed_workspace_file as _seed_workspace_file,
    workspace_access_mode_from_metadata as _workspace_access_mode_from_metadata,
)
from iruka_vfs.service_ops.file_api import (
    allow_write_path as _allow_write_path,
    flush_workspace,
    normalize_virtual_path as _normalize_virtual_path,
    read_workspace_directory,
    read_workspace_file,
    resolve_target_path_for_write as _resolve_target_path_for_write,
    write_workspace_file,
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
from iruka_vfs.tree_view import render_tree_lines as _render_tree_lines
from iruka_vfs.tree_view import render_virtual_tree
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
    execute_workspace_mirror_transaction as _execute_workspace_mirror_transaction,
    flush_workspace_mirror as _flush_workspace_mirror_api,
    get_workspace_mirror as _get_workspace_mirror,
    get_workspace_mirror as _get_workspace_mirror_api,
    mirror_has_dirty_state as _mirror_has_dirty_state,
    mirror_node_path_locked as _mirror_node_path_locked,
    mark_workspace_lock_held as _mark_workspace_lock_held,
    mutate_workspace_mirror as _mutate_workspace_mirror,
    rebuild_workspace_mirror_indexes_locked as _rebuild_workspace_mirror_indexes_locked,
    set_active_workspace_mirror as _set_active_workspace_mirror,
    set_active_workspace_scope as _set_active_workspace_scope,
    set_active_workspace_tenant as _set_active_workspace_tenant,
    set_workspace_mirror as _set_workspace_mirror_api,
    workspace_lock as _workspace_lock_api,
    workspace_scope_for_db as _workspace_scope_for_db,
)

__all__ = [
    "AgentWorkspace",
    "VirtualCommandResult",
    "VirtualFileNode",
    "VirtualShellCommand",
    "VirtualShellSession",
    "WorkspaceSeed",
    "WorkspaceMirror",
    "ensure_virtual_workspace",
    "flush_workspace",
    "get_workspace_access_mode",
    "read_workspace_directory",
    "read_workspace_file",
    "refresh_virtual_workspace",
    "render_virtual_tree",
    "run_virtual_bash",
    "set_workspace_access_mode",
    "snapshot_virtual_fs_cache_metrics",
    "write_workspace_file",
    "RuntimeSeed",
]


def __getattr__(name: str):
    if name == "_dependencies":
        from iruka_vfs.dependencies import get_vfs_dependencies

        return get_vfs_dependencies()
    if name == "_repositories":
        from iruka_vfs.dependency_resolution import resolve_vfs_repositories

        return resolve_vfs_repositories()
    if name == "settings":
        from iruka_vfs.dependencies import get_vfs_dependencies

        return get_vfs_dependencies().settings
    if name in {"AgentWorkspace", "VirtualFileNode", "VirtualShellCommand", "VirtualShellSession"}:
        from iruka_vfs.dependencies import get_vfs_dependencies

        return getattr(get_vfs_dependencies(), name)
    raise AttributeError(name)
