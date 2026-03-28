from __future__ import annotations

from iruka_vfs.runtime.editing import apply_unified_patch, build_simple_patch, exec_edit, exec_patch
from iruka_vfs.runtime.fs_commands import count_lines, exec_mkdir, exec_touch, exec_wc
from iruka_vfs.runtime.search import (
    collect_files,
    collect_files_for_search,
    exec_find,
    find_paths,
    format_missing_path_error,
    safe_compile,
    search_display_path,
    search_nodes,
    search_text_lines,
)
from iruka_vfs.runtime.executor import apply_redirect, exec_argv, run_command_chain, run_single_command
from iruka_vfs.runtime.filesystem import (
    get_or_create_child_dir,
    get_or_create_child_file,
    get_or_create_root,
    get_or_create_session,
    mkdir_parents,
    must_get_node,
    write_file,
)
from iruka_vfs.runtime.logging_support import prepare_artifacts_for_log, summarize_artifacts_for_log, truncate_for_log

__all__ = [
    "apply_redirect",
    "apply_unified_patch",
    "build_simple_patch",
    "collect_files",
    "collect_files_for_search",
    "count_lines",
    "exec_find",
    "exec_argv",
    "exec_edit",
    "exec_mkdir",
    "exec_patch",
    "exec_touch",
    "exec_wc",
    "find_paths",
    "format_missing_path_error",
    "get_or_create_child_dir",
    "get_or_create_child_file",
    "get_or_create_root",
    "get_or_create_session",
    "mkdir_parents",
    "must_get_node",
    "prepare_artifacts_for_log",
    "run_command_chain",
    "run_single_command",
    "safe_compile",
    "search_display_path",
    "search_nodes",
    "search_text_lines",
    "summarize_artifacts_for_log",
    "truncate_for_log",
    "write_file",
]
