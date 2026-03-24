from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from iruka_vfs.constants import (
    VFS_ACCESS_MODE_HOST,
    VFS_NOTES_ROOT,
    VFS_ROOT,
)
from iruka_vfs.dependency_resolution import resolve_vfs_repositories
from iruka_vfs.dependencies import get_vfs_dependencies
from iruka_vfs.memory_cache import get_node_content, get_node_version
from iruka_vfs.pathing import path_is_under, resolve_path
from iruka_vfs.runtime.filesystem import (
    get_or_create_child_dir,
    get_or_create_child_file,
    get_or_create_root,
    get_or_create_session,
    write_file,
)
from iruka_vfs.runtime_seed import RuntimeSeed
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

_dependencies = get_vfs_dependencies()
_repositories = resolve_vfs_repositories()
VFSDependenciesWorkspace = _dependencies.AgentWorkspace


def ensure_virtual_workspace(
    db: Session,
    workspace: VFSDependenciesWorkspace,
    runtime_seed: RuntimeSeed,
    *,
    include_tree: bool = True,
    available_skills: list[dict[str, Any]] | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    tenant_key = assert_workspace_tenant(workspace, tenant_id)
    scope_key = workspace_scope_for_db(db)
    seed = runtime_seed
    register_runtime_seed(int(workspace.id), tenant_key, seed)
    set_active_workspace_tenant(tenant_key)
    set_active_workspace_scope(scope_key)
    try:
        mirror = get_workspace_mirror(
            workspace.id,
            tenant_key=tenant_key,
            scope_key=scope_key,
        )
        cached = get_cached_workspace_state(scope_key, workspace.id)
        if cached and mirror and available_skills is None:
            if include_tree:
                cached["tree"] = render_virtual_tree(db, workspace.id)
            return cached

        root = get_or_create_root(db, workspace.id)
        workspace_dir = get_or_create_child_dir(db, workspace.id, root.id, "workspace")
        documents_dir = get_or_create_child_dir(db, workspace.id, workspace_dir.id, "files")
        get_or_create_child_dir(db, workspace.id, workspace_dir.id, "notes")
        context_dir = get_or_create_child_dir(db, workspace.id, workspace_dir.id, "context")
        skills_dir = get_or_create_child_dir(db, workspace.id, workspace_dir.id, "skills")

        primary_file_path = None
        if seed.primary_file is not None:
            primary_file_path = sync_external_file_source(
                db,
                workspace_id=workspace.id,
                parent_id=documents_dir.id,
                source=seed.primary_file,
                sync_op="sync_from_primary_file",
            )

        for file_name, content in seed.context_files.items():
            context_node = get_or_create_child_file(db, workspace.id, context_dir.id, file_name, content)
            if get_node_content(db, context_node) != content:
                write_file(db, context_node, content, op="sync_from_project_state")

        for path, content in seed.workspace_files.items():
            seed_workspace_file(
                db,
                workspace.id,
                path,
                content,
                op="sync_from_workspace_files",
            )

        for file_name, content in seed.skill_files.items():
            skill_node = get_or_create_child_file(db, workspace.id, skills_dir.id, file_name, content)
            if get_node_content(db, skill_node) != content:
                write_file(db, skill_node, content, op="sync_from_skills")

        metadata = dict(workspace.metadata_json or {})
        metadata["tenant_id"] = tenant_key
        metadata["virtual_primary_file"] = (
            primary_file_path
            or str(seed.metadata.get("virtual_primary_file") or seed.metadata.get("virtual_chapter_file") or "")
        )
        metadata["virtual_writable_roots"] = [VFS_ROOT]
        metadata["virtual_readonly_roots"] = []
        metadata["virtual_notes_dir"] = VFS_NOTES_ROOT
        metadata["virtual_access_mode"] = workspace_access_mode_from_metadata(metadata)
        metadata["virtual_workspace_files"] = sorted(normalize_workspace_path(path, require_file=True) for path in seed.workspace_files)
        metadata["virtual_context_files"] = sorted(seed.context_files.keys())
        metadata["virtual_skill_files"] = sorted(seed.skill_files.keys())
        metadata["virtual_workspace_ready"] = True
        metadata.update(seed.metadata)
        if workspace.metadata_json != metadata or str(getattr(workspace, "tenant_id", "") or "") != tenant_key:
            _repositories.workspace.update_workspace_metadata(
                db,
                workspace_id=int(workspace.id),
                tenant_key=tenant_key,
                metadata_json=metadata,
            )

        session = get_or_create_session(db, workspace.id)
        mirror = build_workspace_mirror(db, workspace, session=session)
        set_workspace_mirror(mirror)
        snapshot = {
            "workspace_id": workspace.id,
            "session_id": session.id,
            "primary_file": metadata["virtual_primary_file"],
        }
        set_cached_workspace_state(scope_key, workspace.id, snapshot)
        if include_tree:
            snapshot["tree"] = render_virtual_tree(db, workspace.id)
        return snapshot
    finally:
        set_active_workspace_tenant(None)


def refresh_virtual_workspace(
    db: Session,
    workspace: VFSDependenciesWorkspace,
    runtime_seed: RuntimeSeed,
    *,
    include_tree: bool = True,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    tenant_key = assert_workspace_tenant(workspace, tenant_id)
    scope_key = workspace_scope_for_db(db)
    register_runtime_seed(int(workspace.id), tenant_key, runtime_seed)
    set_active_workspace_tenant(tenant_key)
    set_active_workspace_scope(scope_key)
    try:
        delete_workspace_mirror(
            int(workspace.id),
            tenant_id=tenant_key,
            scope_key=scope_key,
        )
        clear_cached_workspace_state(scope_key, int(workspace.id))

        session = get_or_create_session(db, int(workspace.id))
        mirror = build_workspace_mirror(db, workspace, session=session)
        set_workspace_mirror(mirror)

        metadata = dict(mirror.workspace_metadata or {})
        snapshot = {
            "workspace_id": int(workspace.id),
            "session_id": int(session.id),
            "primary_file": str(metadata.get("virtual_primary_file") or metadata.get("virtual_chapter_file") or ""),
        }
        set_cached_workspace_state(scope_key, int(workspace.id), snapshot)
        if include_tree:
            snapshot["tree"] = render_virtual_tree(db, int(workspace.id))
        return snapshot
    finally:
        set_active_workspace_tenant(None)


def sync_external_file_source(
    db: Session,
    *,
    workspace_id: int,
    parent_id: int,
    source: Any,
    sync_op: str,
) -> str:
    file_name = source.virtual_path.rsplit("/", 1)[-1]
    content = source.read_text()
    file_node = get_or_create_child_file(db, workspace_id, parent_id, file_name, content)
    if get_node_content(db, file_node) != content and get_node_version(db, file_node) <= 1:
        write_file(db, file_node, content, op=sync_op)
    return source.virtual_path


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


def seed_workspace_file(db: Session, workspace_id: int, path: str, content: str, *, op: str) -> dict[str, Any]:
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
    if not created and get_node_content(db, node) != content:
        version_no = write_file(db, node, content, op=op)
    return {
        "path": normalized,
        "version": int(version_no),
        "created": created,
    }


def workspace_access_mode_from_metadata(metadata: dict[str, Any] | None) -> str:
    raw_mode = str(dict(metadata or {}).get("virtual_access_mode") or VFS_ACCESS_MODE_HOST).strip().lower()
    if raw_mode not in {"host", "agent"}:
        return VFS_ACCESS_MODE_HOST
    return raw_mode
