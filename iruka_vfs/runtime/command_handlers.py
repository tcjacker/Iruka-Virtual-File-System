from __future__ import annotations

from iruka_vfs.runtime.editing import apply_unified_patch, build_simple_patch, exec_edit, exec_patch
from iruka_vfs.runtime.fs_commands import (
    count_lines,
    exec_basename,
    exec_cp,
    exec_dirname,
    exec_head,
    exec_mkdir,
    exec_mv,
    exec_rm,
    exec_sort,
    exec_touch,
    exec_wc,
)
from iruka_vfs.runtime.search import (
    collect_files,
    collect_files_for_search,
    count_text_matches,
    exec_find,
    find_paths,
    format_missing_path_error,
    safe_compile,
    search_match_counts,
    search_matching_file_paths,
    search_display_path,
    search_nodes,
    search_text_lines,
)
