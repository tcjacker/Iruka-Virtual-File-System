from __future__ import annotations

from iruka_vfs.dependencies import get_vfs_dependencies
from iruka_vfs.mirror.checkpoint import (
    ensure_workspace_checkpoint_worker,
    enqueue_workspace_checkpoint,
    flush_workspace_mirror,
    mirror_has_dirty_state,
    snapshot_workspace_checkpoint_metrics,
    workspace_checkpoint_worker,
)
from iruka_vfs.mirror.context import (
    active_workspace_mirror,
    active_workspace_scope,
    active_workspace_tenant,
    assert_workspace_tenant,
    effective_tenant_key,
    effective_workspace_scope,
    normalize_tenant_id,
    set_active_workspace_mirror,
    set_active_workspace_scope,
    set_active_workspace_tenant,
    workspace_scope_for_db,
    workspace_tenant_key,
)
from iruka_vfs.mirror.keys import (
    workspace_base_key,
    workspace_dead_letter_payload_key,
    workspace_dead_letter_set_key,
    workspace_dirty_set_key,
    workspace_due_key,
    workspace_enqueued_key,
    workspace_error_key,
    workspace_index_key,
    workspace_lock_key,
    workspace_mirror_dirty_nodes_key,
    workspace_mirror_key,
    workspace_mirror_nodes_key,
    workspace_queue_key,
    workspace_retry_count_key,
)
from iruka_vfs.mirror.indexing import (
    build_workspace_mirror,
    ensure_children_sorted_locked,
    mirror_node_path_locked,
    rebuild_workspace_mirror_indexes_locked,
)
from iruka_vfs.mirror.serialization import (
    clone_node,
    delete_workspace_mirror,
    deserialize_workspace_mirror,
    get_workspace_mirror,
    load_workspace_mirror_by_base_key,
    serialize_workspace_mirror,
    serialize_workspace_mirror_meta,
    serialize_workspace_nodes,
    set_workspace_mirror,
    workspace_lock,
)
from iruka_vfs.sqlalchemy_repositories import build_sqlalchemy_repositories

_dependencies = get_vfs_dependencies()
_repositories = _dependencies.repositories or build_sqlalchemy_repositories(_dependencies)
settings = _dependencies.settings
AgentWorkspace = _dependencies.AgentWorkspace
VirtualFileNode = _dependencies.VirtualFileNode
