from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from iruka_vfs.constants import (
    VFS_ACCESS_MODE_HOST,
    VFS_ROOT,
)
from iruka_vfs.memory_cache import get_node_content, get_node_version
from iruka_vfs.pathing import path_is_under, resolve_path
from iruka_vfs.runtime.filesystem import (
    get_or_create_child_dir,
    get_or_create_child_file,
    get_or_create_root,
    get_or_create_session,
    write_file,
)
from iruka_vfs.runtime_seed import WorkspaceSeed
from iruka_vfs.service_ops.state import (
    clear_cached_workspace_state,
    get_cached_workspace_state,
    register_runtime_seed,
    set_cached_workspace_state,
)
from iruka_vfs.tree_view import render_virtual_tree
from iruka_vfs.workspace_mirror import (
    assert_workspace_tenant,
    build_workspace_mirror,
    delete_workspace_mirror,
    get_workspace_mirror,
    set_active_workspace_scope,
    set_active_workspace_tenant,
    set_workspace_mirror,
    workspace_scope_for_db,
)
from iruka_vfs.workspace_state_serialization import serialize_workspace_mirror


VIRTUAL_PERSISTENCE_BINDING_KEY = "virtual_persistence_binding"


def _repositories():
    from iruka_vfs.dependency_resolution import resolve_vfs_repositories

    return resolve_vfs_repositories()


def persistence_binding_for_db(db: Session | None) -> str:
    if db is None:
        return ""
    bind = db.get_bind()
    engine = getattr(bind, "engine", bind)
    url = getattr(engine, "url", None)
    if url is None:
        return f"{type(engine).__name__}:{id(engine)}"
    return str(url.render_as_string(hide_password=False))


def _assert_workspace_persistence_binding(db: Session | None, workspace: Any) -> str:
    current_binding = persistence_binding_for_db(db)
    if not current_binding:
        return ""
    metadata = dict(getattr(workspace, "metadata_json", {}) or {})
    existing_binding = str(metadata.get(VIRTUAL_PERSISTENCE_BINDING_KEY) or "").strip()
    if existing_binding and existing_binding != current_binding:
        raise ValueError(
            f"workspace is bound to persistence target '{existing_binding}', got '{current_binding}'"
        )
    return current_binding


def _ensure_checkpoint_worker_for_db(db: Session | None) -> None:
    from iruka_vfs.mirror.checkpoint import ensure_workspace_checkpoint_worker

    if db is None:
        return
    bind = db.get_bind()
    if bind is not None:
        ensure_workspace_checkpoint_worker(bind)


def ensure_virtual_workspace(
    db: Session,
    workspace: Any,
    workspace_seed: WorkspaceSeed,
    *,
    include_tree: bool = True,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    tenant_key = assert_workspace_tenant(workspace, tenant_id)
    scope_key = workspace_scope_for_db(db)
    persistence_binding = _assert_workspace_persistence_binding(db, workspace)
    _ensure_checkpoint_worker_for_db(db)
    register_runtime_seed(int(workspace.id), tenant_key, workspace_seed)
    set_active_workspace_tenant(tenant_key)
    set_active_workspace_scope(scope_key)
    try:
        mirror = get_workspace_mirror(
            workspace.id,
            tenant_key=tenant_key,
            scope_key=scope_key,
        )
        cached = get_cached_workspace_state(scope_key, workspace.id)
        if cached and mirror:
            if include_tree:
                cached["tree"] = render_virtual_tree(db, workspace.id)
            return cached

        root = get_or_create_root(db, workspace.id)
        get_or_create_child_dir(db, workspace.id, root.id, "workspace")

        for path, content in workspace_seed.workspace_files.items():
            seed_workspace_file(
                db,
                workspace.id,
                path,
                content,
                op="sync_from_workspace_files",
                overwrite_existing=False,
            )

        metadata = dict(workspace.metadata_json or {})
        metadata["tenant_id"] = tenant_key
        metadata["virtual_writable_roots"] = [VFS_ROOT]
        metadata["virtual_readonly_roots"] = []
        metadata["virtual_access_mode"] = workspace_access_mode_from_metadata(metadata)
        metadata["virtual_workspace_files"] = sorted(
            normalize_workspace_path(path, require_file=True) for path in workspace_seed.workspace_files
        )
        if persistence_binding:
            metadata[VIRTUAL_PERSISTENCE_BINDING_KEY] = persistence_binding
        metadata["virtual_workspace_ready"] = True
        metadata.update(workspace_seed.metadata)
        if workspace.metadata_json != metadata or str(getattr(workspace, "tenant_id", "") or "") != tenant_key:
            _repositories().workspace.update_workspace_metadata(
                db,
                workspace_id=int(workspace.id),
                tenant_key=tenant_key,
                metadata_json=metadata,
            )
            workspace.metadata_json = dict(metadata)
            if hasattr(workspace, "tenant_id"):
                workspace.tenant_id = tenant_key

        session = get_or_create_session(db, workspace.id)
        mirror = build_workspace_mirror(db, workspace, session=session)
        set_workspace_mirror(mirror)
        snapshot = {
            "workspace_id": workspace.id,
            "session_id": session.id,
        }
        set_cached_workspace_state(scope_key, workspace.id, snapshot)
        if include_tree:
            snapshot["tree"] = render_virtual_tree(db, workspace.id)
        return snapshot
    finally:
        set_active_workspace_tenant(None)
        set_active_workspace_scope(None)


def refresh_virtual_workspace(
    db: Session,
    workspace: Any,
    workspace_seed: WorkspaceSeed,
    *,
    include_tree: bool = True,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    tenant_key = assert_workspace_tenant(workspace, tenant_id)
    scope_key = workspace_scope_for_db(db)
    persistence_binding = _assert_workspace_persistence_binding(db, workspace)
    _ensure_checkpoint_worker_for_db(db)
    register_runtime_seed(int(workspace.id), tenant_key, workspace_seed)
    set_active_workspace_tenant(tenant_key)
    set_active_workspace_scope(scope_key)
    try:
        current_mirror = get_workspace_mirror(
            int(workspace.id),
            tenant_key=tenant_key,
            scope_key=scope_key,
        )
        session = _repositories().session.get_active_session(db, int(workspace.id), tenant_key)
        if session is None:
            delete_workspace_mirror(
                int(workspace.id),
                tenant_id=tenant_key,
                scope_key=scope_key,
            )
            clear_cached_workspace_state(scope_key, int(workspace.id))
            session = get_or_create_session(db, int(workspace.id))
            mirror = build_workspace_mirror(db, workspace, session=session)
            set_workspace_mirror(mirror)
        else:
            mirror = build_workspace_mirror(db, workspace, session=session)
            if current_mirror is None or _refresh_signature(current_mirror) != _refresh_signature(mirror):
                delete_workspace_mirror(
                    int(workspace.id),
                    tenant_id=tenant_key,
                    scope_key=scope_key,
                )
                clear_cached_workspace_state(scope_key, int(workspace.id))
                set_workspace_mirror(mirror)
            else:
                mirror = current_mirror

        metadata = dict(mirror.workspace_metadata or {})
        if persistence_binding:
            metadata[VIRTUAL_PERSISTENCE_BINDING_KEY] = persistence_binding
        mirror.workspace_metadata = metadata
        snapshot = {
            "workspace_id": int(workspace.id),
            "session_id": int(session.id),
        }
        set_cached_workspace_state(scope_key, int(workspace.id), snapshot)
        if include_tree:
            snapshot["tree"] = render_virtual_tree(db, int(workspace.id))
        return snapshot
    finally:
        set_active_workspace_tenant(None)
        set_active_workspace_scope(None)


def _refresh_signature(mirror) -> dict[str, Any]:
    payload = json.loads(serialize_workspace_mirror(mirror))
    payload.pop("tenant_key", None)
    payload.pop("scope_key", None)
    payload.pop("workspace_id", None)
    payload.pop("revision", None)
    payload.pop("checkpoint_revision", None)
    payload.pop("next_temp_id", None)
    return payload

def normalize_workspace_path(raw_path: str, *, require_file: bool = False) -> str:
    cleaned = str(raw_path or "").strip()
    if not cleaned:
        raise ValueError("workspace path is required")
    if not cleaned.startswith("/"):
        cleaned = f"{VFS_ROOT.rstrip('/')}/{cleaned.lstrip('/')}"
    parts: list[str] = []
    for part in cleaned.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            raise ValueError(f"workspace path cannot contain '..': {raw_path}")
        parts.append(part)
    normalized = "/" + "/".join(parts)
    if not path_is_under(normalized, VFS_ROOT):
        raise ValueError(f"workspace path must stay under {VFS_ROOT}: {raw_path}")
    if require_file and normalized == VFS_ROOT:
        raise ValueError(f"workspace file path must not be the workspace root: {raw_path}")
    return normalized


def ensure_virtual_dir_path(db: Session, workspace_id: int, dir_path: str):
    normalized = normalize_workspace_path(dir_path)
    root = get_or_create_root(db, workspace_id)
    if normalized == "/":
        return root

    current = root
    for part in [item for item in normalized.split("/") if item]:
        child = resolve_path(db, workspace_id, int(current.id), part)
        if child:
            if child.node_type != "dir":
                raise ValueError(f"workspace path exists as file: {normalized}")
            current = child
            continue
        current = get_or_create_child_dir(db, workspace_id, int(current.id), part)
    return current


def seed_workspace_file(
    db: Session,
    workspace_id: int,
    path: str,
    content: str,
    *,
    op: str,
    overwrite_existing: bool = True,
    conflict_if_exists: bool = False,
) -> dict[str, Any]:
    from iruka_vfs.write_conflicts import build_overwrite_conflict

    normalized = normalize_workspace_path(path, require_file=True)
    parent_path, _, name = normalized.rpartition("/")
    parent = ensure_virtual_dir_path(db, workspace_id, parent_path or "/")
    node = resolve_path(db, workspace_id, int(parent.id), name)
    created = False
    if node:
        if node.node_type != "file":
            raise ValueError(f"workspace path exists as directory: {normalized}")
    else:
        node = get_or_create_child_file(db, workspace_id, int(parent.id), name, content)
        created = True

    version_no = get_node_version(db, node)
    if not overwrite_existing and not created:
        if conflict_if_exists:
            conflict = build_overwrite_conflict(normalized, source="host_write")
            conflict["version"] = int(version_no)
            conflict["created"] = False
            return conflict
        return {
            "path": normalized,
            "version": int(version_no),
            "created": False,
        }
    if overwrite_existing and not created and get_node_content(db, node) != content:
        version_no = write_file(db, node, content, op=op)
    return {
        "ok": True,
        "path": normalized,
        "version": int(version_no),
        "created": created,
    }


def workspace_access_mode_from_metadata(metadata: dict[str, Any] | None) -> str:
    raw_mode = str(dict(metadata or {}).get("virtual_access_mode") or VFS_ACCESS_MODE_HOST).strip().lower()
    if raw_mode not in {"host", "agent"}:
        return VFS_ACCESS_MODE_HOST
    return raw_mode
