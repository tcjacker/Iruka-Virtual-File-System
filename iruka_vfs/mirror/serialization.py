from __future__ import annotations

from iruka_vfs.models import WorkspaceMirror
from iruka_vfs.service_ops.state import get_workspace_state_store
from iruka_vfs.workspace_state_serialization import (
    clone_node,
    deserialize_workspace_mirror,
    serialize_workspace_mirror,
    serialize_workspace_mirror_meta,
    serialize_workspace_nodes,
)

def get_workspace_mirror(
    workspace_id: int,
    tenant_key: str | None = None,
    scope_key: str | None = None,
) -> WorkspaceMirror | None:
    return get_workspace_state_store().get_workspace_mirror(
        workspace_id,
        tenant_key=tenant_key,
        scope_key=scope_key,
    )


def set_workspace_mirror(mirror: WorkspaceMirror) -> None:
    get_workspace_state_store().set_workspace_mirror(mirror)


def delete_workspace_mirror(workspace_id: int, tenant_id: str | None = None, scope_key: str | None = None) -> None:
    get_workspace_state_store().delete_workspace_mirror(
        workspace_id,
        tenant_key=tenant_id,
        scope_key=scope_key,
    )


def workspace_lock(
    mirror: WorkspaceMirror | None = None,
    *,
    workspace_id: int | None = None,
) -> object:
    return get_workspace_state_store().workspace_lock(mirror=mirror, workspace_id=workspace_id)
