from __future__ import annotations

from iruka_vfs.runtime.editing import apply_unified_patch, build_simple_patch, exec_edit, exec_patch
from iruka_vfs.runtime.fs_commands import count_lines, exec_mkdir, exec_touch, exec_wc
from iruka_vfs.runtime.search import (
    collect_files,
    collect_files_for_search,
    safe_compile,
    search_display_path,
    search_nodes,
    search_text_lines,
)
