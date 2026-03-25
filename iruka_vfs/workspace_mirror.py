from __future__ import annotations

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
from iruka_vfs.mirror.indexing import (
    build_workspace_mirror,
    ensure_children_sorted_locked,
    mirror_node_path_locked,
    rebuild_workspace_mirror_indexes_locked,
)
from iruka_vfs.mirror.mutation import (
    execute_workspace_mirror_transaction,
    mark_workspace_lock_held,
    mutate_workspace_mirror,
)
from iruka_vfs.mirror.serialization import (
    clone_node,
    delete_workspace_mirror,
    deserialize_workspace_mirror,
    get_workspace_mirror,
    serialize_workspace_mirror,
    serialize_workspace_mirror_meta,
    serialize_workspace_nodes,
    set_workspace_mirror,
    workspace_lock,
)


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
    if name in {"AgentWorkspace", "VirtualFileNode"}:
        from iruka_vfs.dependencies import get_vfs_dependencies

        return getattr(get_vfs_dependencies(), name)
    raise AttributeError(name)
