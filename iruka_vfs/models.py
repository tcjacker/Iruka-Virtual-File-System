from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from typing import Any

from iruka_vfs.dependencies import get_vfs_dependencies

_dependencies = get_vfs_dependencies()
VirtualFileNode = _dependencies.VirtualFileNode


@dataclass
class VirtualCommandResult:
    stdout: str
    stderr: str
    exit_code: int
    artifacts: dict[str, Any]


@dataclass
class FileCacheEntry:
    file_id: int
    content: str
    version_no: int
    flushed_version_no: int
    pending_versions: list[dict[str, Any]]
    dirty: bool
    size_bytes: int
    last_access_ts: float


@dataclass
class WorkspaceMirror:
    tenant_key: str
    scope_key: str
    workspace_id: int
    chapter_id: int
    root_id: int
    session_id: int
    cwd_node_id: int
    nodes: dict[int, VirtualFileNode]
    path_to_id: dict[str, int]
    children_by_parent: dict[int | None, list[int]]
    workspace_metadata: dict[str, Any]
    revision: int = 1
    checkpoint_revision: int = 0
    dirty_content_node_ids: set[int] = field(default_factory=set)
    dirty_structure_node_ids: set[int] = field(default_factory=set)
    dirty_session: bool = False
    dirty_workspace_metadata: bool = False
    next_temp_id: int = -1
    lock: RLock = field(default_factory=RLock)
