from __future__ import annotations

from datetime import datetime
import json
import os
import queue
import re
import shlex
import threading
import time
from typing import Any

import redis
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from iruka_vfs.command_parser import parse_options as _parse_options
from iruka_vfs.command_parser import shell_tokens as _shell_tokens
from iruka_vfs.command_parser import split_chain as _split_chain
from iruka_vfs.command_parser import parse_pipeline_and_redirect as _parse_pipeline_and_redirect
from iruka_vfs.command_runtime import apply_redirect as _apply_redirect
from iruka_vfs.command_runtime import exec_argv as _exec_argv
from iruka_vfs.command_runtime import run_command_chain as _run_command_chain
from iruka_vfs.command_runtime import run_single_command as _run_single_command
from iruka_vfs.constants import (
    ASYNC_COMMAND_LOGGING,
    MEMORY_CACHE_ENABLED,
    MEMORY_CACHE_MAX_BYTES,
    MEMORY_CACHE_MAX_FILES,
    REGEX_META_CHARS,
    VFS_CHAPTERS_ROOT,
    VFS_COMMAND_LOG_MAX_STDERR_CHARS,
    VFS_COMMAND_LOG_MAX_STDOUT_CHARS,
    VFS_CONTEXT_ROOT,
    VFS_NOTES_ROOT,
    VFS_ROOT,
    VFS_SKILLS_ROOT,
)
from iruka_vfs.dependencies import get_vfs_dependencies
from iruka_vfs.memory_cache import cache_metric_inc as _cache_metric_inc
from iruka_vfs.memory_cache import ensure_mem_cache_worker as _ensure_mem_cache_worker_api
from iruka_vfs.memory_cache import estimate_text_bytes as _estimate_text_bytes
from iruka_vfs.memory_cache import get_node_content as _get_node_content
from iruka_vfs.memory_cache import get_node_version as _get_node_version
from iruka_vfs.memory_cache import snapshot_virtual_fs_cache_metrics
from iruka_vfs.memory_cache import update_cache_after_write as _update_cache_after_write
from iruka_vfs.models import VirtualCommandResult, WorkspaceMirror
from iruka_vfs.paths import list_children as _list_children
from iruka_vfs.paths import node_path as _node_path
from iruka_vfs.paths import path_is_under as _path_is_under
from iruka_vfs.paths import resolve_parent_for_create as _resolve_parent_for_create
from iruka_vfs.paths import resolve_path as _resolve_path
from iruka_vfs.runtime_seed import RuntimeSeed
from iruka_vfs.sqlalchemy_repositories import build_sqlalchemy_repositories
from iruka_vfs.tree_view import render_tree_lines as _render_tree_lines
from iruka_vfs.tree_view import render_virtual_tree
from iruka_vfs.workspace_mirror import active_workspace_mirror as _active_workspace_mirror
from iruka_vfs.workspace_mirror import assert_workspace_tenant as _assert_workspace_tenant
from iruka_vfs.workspace_mirror import build_workspace_mirror as _build_workspace_mirror
from iruka_vfs.workspace_mirror import clone_node as _clone_node
from iruka_vfs.workspace_mirror import delete_workspace_mirror as _delete_workspace_mirror_api
from iruka_vfs.workspace_mirror import deserialize_workspace_mirror as _deserialize_workspace_mirror
from iruka_vfs.workspace_mirror import effective_tenant_key as _effective_tenant_key
from iruka_vfs.workspace_mirror import enqueue_workspace_checkpoint as _enqueue_workspace_checkpoint
from iruka_vfs.workspace_mirror import ensure_children_sorted_locked as _ensure_children_sorted_locked
from iruka_vfs.workspace_mirror import ensure_workspace_checkpoint_worker as _ensure_workspace_checkpoint_worker_api
from iruka_vfs.workspace_mirror import flush_workspace_mirror as _flush_workspace_mirror_api
from iruka_vfs.workspace_mirror import get_workspace_mirror as _get_workspace_mirror
from iruka_vfs.workspace_mirror import get_workspace_mirror as _get_workspace_mirror_api
from iruka_vfs.workspace_mirror import mirror_node_path_locked as _mirror_node_path_locked
from iruka_vfs.workspace_mirror import rebuild_workspace_mirror_indexes_locked as _rebuild_workspace_mirror_indexes_locked
from iruka_vfs.workspace_mirror import set_workspace_mirror as _set_workspace_mirror_api
from iruka_vfs.workspace_mirror import set_active_workspace_mirror as _set_active_workspace_mirror
from iruka_vfs.workspace_mirror import set_active_workspace_scope as _set_active_workspace_scope
from iruka_vfs.workspace_mirror import set_active_workspace_tenant as _set_active_workspace_tenant
from iruka_vfs.workspace_mirror import workspace_enqueued_key as _workspace_enqueued_key
from iruka_vfs.workspace_mirror import workspace_lock as _workspace_lock_api
from iruka_vfs.workspace_mirror import workspace_mirror_key as _workspace_mirror_key
from iruka_vfs.workspace_mirror import workspace_scope_for_db as _workspace_scope_for_db
from iruka_vfs import runtime_state

_dependencies = get_vfs_dependencies()
_repositories = _dependencies.repositories or build_sqlalchemy_repositories(_dependencies)
settings = _dependencies.settings
AgentWorkspace = _dependencies.AgentWorkspace
Chapter = _dependencies.Chapter
VirtualFileNode = _dependencies.VirtualFileNode
VirtualShellCommand = _dependencies.VirtualShellCommand
VirtualShellSession = _dependencies.VirtualShellSession

_workspace_cache_lock = threading.Lock()
_workspace_cache: dict[tuple[str, int], dict[str, Any]] = {}

_log_lock = threading.Lock()
_log_engine: Engine | None = None
_log_session_maker: sessionmaker | None = None
_log_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=5000)
_log_worker_started = False
_ephemeral_command_lock = threading.Lock()
_ephemeral_command_id = 1_000_000_000
_ephemeral_patch_lock = threading.Lock()
_ephemeral_patch_id = 2_000_000_000
_redis_client_lock = threading.Lock()
_redis_client: redis.Redis | None = None


def _truncate_for_log(text_value: str, limit: int) -> tuple[str, dict[str, Any]]:
    normalized = text_value or ""
    safe_limit = max(int(limit), 0)
    original_len = len(normalized)
    if original_len <= safe_limit or safe_limit <= 0:
        if safe_limit <= 0 and original_len > 0:
            return "", {"truncated": True, "original_length": original_len, "stored_length": 0}
        return normalized, {"truncated": False, "original_length": original_len, "stored_length": original_len}

    # Keep head to preserve quick diagnostic value and mark truncation explicitly.
    suffix = f"\n...[truncated {original_len - safe_limit} chars]"
    if safe_limit <= len(suffix):
        clipped = normalized[:safe_limit]
    else:
        clipped = normalized[: safe_limit - len(suffix)] + suffix
    return clipped, {"truncated": True, "original_length": original_len, "stored_length": len(clipped)}


def _get_cached_workspace_state(scope_key: str, workspace_id: int, chapter_id: int) -> dict[str, Any] | None:
    with _workspace_cache_lock:
        item = _workspace_cache.get((scope_key, workspace_id))
        if not item or int(item.get("chapter_id") or 0) != int(chapter_id):
            return None
        return dict(item)


def _set_cached_workspace_state(scope_key: str, workspace_id: int, payload: dict[str, Any]) -> None:
    with _workspace_cache_lock:
        _workspace_cache[(scope_key, workspace_id)] = dict(payload)


def _register_runtime_seed(workspace_id: int, tenant_key: str, runtime_seed: RuntimeSeed) -> None:
    with runtime_state.runtime_seed_lock:
        runtime_state.runtime_seeds[(tenant_key, int(workspace_id))] = runtime_seed


def _get_registered_runtime_seed(workspace_id: int, tenant_key: str) -> RuntimeSeed | None:
    with runtime_state.runtime_seed_lock:
        item = runtime_state.runtime_seeds.get((tenant_key, int(workspace_id)))
    return item


def _ensure_async_log_worker(engine: Engine) -> None:
    global _log_engine, _log_session_maker, _log_worker_started
    if not ASYNC_COMMAND_LOGGING:
        return
    with _log_lock:
        if _log_worker_started:
            return
        _log_engine = engine
        _log_session_maker = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
        worker = threading.Thread(target=_virtual_command_log_worker, name="vfs-command-log-worker", daemon=True)
        worker.start()
        _log_worker_started = True


def _enqueue_virtual_command_log(payload: dict[str, Any]) -> None:
    if not ASYNC_COMMAND_LOGGING or not _log_worker_started:
        return
    try:
        _log_queue.put_nowait(payload)
    except queue.Full:
        # Drop logs on backpressure to protect command latency.
        return


def _virtual_command_log_worker() -> None:
    if _log_session_maker is None:
        return
    while True:
        first = _log_queue.get()
        batch = [first]
        while len(batch) < 100:
            try:
                batch.append(_log_queue.get_nowait())
            except queue.Empty:
                break
        db = _log_session_maker()
        try:
            _repositories.command_log.bulk_insert_command_logs(db, batch)
        except Exception:
            db.rollback()
        finally:
            db.close()


def _next_ephemeral_command_id() -> int:
    global _ephemeral_command_id
    with _ephemeral_command_lock:
        _ephemeral_command_id += 1
        return _ephemeral_command_id


def _next_ephemeral_patch_id() -> int:
    global _ephemeral_patch_id
    with _ephemeral_patch_lock:
        _ephemeral_patch_id += 1
        return _ephemeral_patch_id


def _get_redis_client() -> redis.Redis:
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    with _redis_client_lock:
        if _redis_client is None:
            _redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        return _redis_client


def ensure_virtual_workspace(
    db: Session,
    workspace: AgentWorkspace,
    runtime_seed: RuntimeSeed,
    *,
    include_tree: bool = True,
    available_skills: list[dict[str, Any]] | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    tenant_key = _assert_workspace_tenant(workspace, tenant_id)
    scope_key = _workspace_scope_for_db(db)
    seed = runtime_seed
    _register_runtime_seed(int(workspace.id), tenant_key, seed)
    _set_active_workspace_tenant(tenant_key)
    _set_active_workspace_scope(scope_key)
    try:
        mirror = _get_workspace_mirror_api(
            workspace.id,
            seed.chapter_id,
            tenant_key=tenant_key,
            scope_key=scope_key,
        )
        cached = _get_cached_workspace_state(scope_key, workspace.id, seed.chapter_id or 0)
        if cached and mirror and available_skills is None:
            if include_tree:
                cached["tree"] = render_virtual_tree(db, workspace.id)
            return cached

        root = _get_or_create_root(db, workspace.id)
        workspace_dir = _get_or_create_child_dir(db, workspace.id, root.id, "workspace")
        chapters_dir = _get_or_create_child_dir(db, workspace.id, workspace_dir.id, "chapters")
        _get_or_create_child_dir(db, workspace.id, workspace_dir.id, "notes")
        context_dir = _get_or_create_child_dir(db, workspace.id, workspace_dir.id, "context")
        skills_dir = _get_or_create_child_dir(db, workspace.id, workspace_dir.id, "skills")

        chapter_path = None
        if seed.primary_file is not None:
            chapter_path = _sync_external_file_source(
                db,
                workspace_id=workspace.id,
                parent_id=chapters_dir.id,
                source=seed.primary_file,
                sync_op="sync_from_primary_file",
            )

        for file_name, content in seed.context_files.items():
            context_node = _get_or_create_child_file(db, workspace.id, context_dir.id, file_name, content)
            if _get_node_content(db, context_node) != content:
                _write_file(db, context_node, content, op="sync_from_project_state")

        for file_name, content in seed.skill_files.items():
            skill_node = _get_or_create_child_file(db, workspace.id, skills_dir.id, file_name, content)
            if _get_node_content(db, skill_node) != content:
                _write_file(db, skill_node, content, op="sync_from_skills")

        metadata = dict(workspace.metadata_json or {})
        metadata["tenant_id"] = tenant_key
        metadata["virtual_chapter_file"] = chapter_path or str(seed.metadata.get("virtual_chapter_file") or "")
        metadata["virtual_writable_roots"] = [VFS_CHAPTERS_ROOT, VFS_NOTES_ROOT]
        metadata["virtual_readonly_roots"] = [VFS_CONTEXT_ROOT, VFS_SKILLS_ROOT]
        metadata["virtual_notes_dir"] = VFS_NOTES_ROOT
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

        session = _get_or_create_session(db, workspace.id)
        mirror = _build_workspace_mirror(db, workspace, seed.chapter_id or int(workspace.chapter_id), session=session)
        _set_workspace_mirror_api(mirror)
        snapshot = {
            "workspace_id": workspace.id,
            "chapter_id": seed.chapter_id or int(workspace.chapter_id),
            "session_id": session.id,
            "chapter_file": metadata["virtual_chapter_file"],
        }
        _set_cached_workspace_state(scope_key, workspace.id, snapshot)
        if include_tree:
            snapshot["tree"] = render_virtual_tree(db, workspace.id)
        return snapshot
    finally:
        _set_active_workspace_tenant(None)


def _sync_external_file_source(
    db: Session,
    *,
    workspace_id: int,
    parent_id: int,
    source: Any,
    sync_op: str,
) -> str:
    file_name = source.virtual_path.rsplit("/", 1)[-1]
    content = source.read_text()
    file_node = _get_or_create_child_file(db, workspace_id, parent_id, file_name, content)
    if _get_node_content(db, file_node) != content and _get_node_version(db, file_node) <= 1:
        _write_file(db, file_node, content, op=sync_op)
    return source.virtual_path


def flush_workspace(workspace_id: int, tenant_id: str | None = None) -> bool:
    tenant_key = _effective_tenant_key(tenant_id)
    mirror = _get_workspace_mirror_api(workspace_id, tenant_key=tenant_key)
    if not mirror:
        return True
    lock, base_key = _workspace_lock_api(mirror)
    if not lock.acquire(blocking=True):
        return False
    try:
        current = _get_workspace_mirror_api(workspace_id, tenant_key=tenant_key)
        if not current:
            return True
    finally:
        try:
            lock.release()
        except Exception:
            pass
    ok = _flush_workspace_mirror_api(None, base_key=base_key)
    client = _get_redis_client()
    client.srem(_workspace_enqueued_key(), base_key)
    current_raw = client.get(_workspace_mirror_key(base_key))
    if current_raw:
        current = _deserialize_workspace_mirror(current_raw)
        if current.dirty_node_ids or current.dirty_session or current.dirty_workspace_metadata:
            _enqueue_workspace_checkpoint(base_key)
    return ok


def run_virtual_bash(
    db: Session,
    workspace: AgentWorkspace,
    raw_cmd: str,
    *,
    runtime_seed: RuntimeSeed,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    try:
        tenant_key = _assert_workspace_tenant(workspace, tenant_id)
        bind = db.get_bind()
        _ensure_async_log_worker(bind)
        _ensure_workspace_checkpoint_worker_api(bind)
        _ensure_mem_cache_worker_api(bind)
        seed = runtime_seed
        snapshot = ensure_virtual_workspace(
            db,
            workspace,
            seed,
            include_tree=False,
            tenant_id=tenant_key,
        )
        initial_mirror = _get_workspace_mirror_api(workspace.id, seed.chapter_id, tenant_key=tenant_key)
        if not initial_mirror:
            raise ValueError(f"workspace mirror missing for workspace {workspace.id}")
        lock, _ = _workspace_lock_api(initial_mirror)
        if not lock.acquire(blocking=True):
            raise TimeoutError(f"failed to acquire workspace lock: {workspace.id}")
        try:
            mirror = _get_workspace_mirror_api(workspace.id, seed.chapter_id, tenant_key=tenant_key)
            if not mirror:
                raise ValueError(f"workspace mirror missing for workspace {workspace.id}")
            session = VirtualShellSession(
                id=int(mirror.session_id),
                tenant_id=tenant_key,
                workspace_id=workspace.id,
                cwd_node_id=int(mirror.cwd_node_id),
                env_json={"PWD": VFS_ROOT},
                status="active",
            )
            started_at = datetime.utcnow()
            _set_active_workspace_tenant(tenant_key)
            _set_active_workspace_scope(mirror.scope_key)
            _set_active_workspace_mirror(mirror)
            original_cwd_node_id = int(mirror.cwd_node_id)
            original_revision = int(mirror.revision)
            result = _run_command_chain(db, session, raw_cmd)
            next_cwd_node_id = int(session.cwd_node_id or mirror.cwd_node_id)
            if next_cwd_node_id != original_cwd_node_id:
                mirror.cwd_node_id = next_cwd_node_id
                mirror.dirty_session = True
                mirror.revision += 1
            if int(mirror.revision) != original_revision or mirror.dirty_node_ids or mirror.dirty_session or mirror.dirty_workspace_metadata:
                _set_workspace_mirror_api(mirror)
            ended_at = datetime.utcnow()
        finally:
            _set_active_workspace_mirror(None)
            _set_active_workspace_tenant(None)
            _set_active_workspace_scope(None)
            try:
                lock.release()
            except Exception:
                pass
        log_stdout, stdout_meta = _truncate_for_log(result.stdout, VFS_COMMAND_LOG_MAX_STDOUT_CHARS)
        log_stderr, stderr_meta = _truncate_for_log(result.stderr, VFS_COMMAND_LOG_MAX_STDERR_CHARS)
        log_artifacts = dict(result.artifacts or {})
        log_artifacts["logging"] = {
            "stdout": stdout_meta,
            "stderr": stderr_meta,
        }

        log_payload = {
            "tenant_id": tenant_key,
            "session_id": session.id,
            "raw_cmd": raw_cmd,
            "parsed_json": {"segments": _split_chain(raw_cmd)},
            "exit_code": result.exit_code,
            "stdout_text": log_stdout,
            "stderr_text": log_stderr,
            "artifacts_json": log_artifacts,
            "started_at": started_at,
            "ended_at": ended_at,
        }
        if ASYNC_COMMAND_LOGGING:
            _enqueue_virtual_command_log(log_payload)
            command_id = _next_ephemeral_command_id()
        else:
            command_id = _repositories.command_log.create_command_log(db, log_payload)
    except Exception:
        db.rollback()
        raise

    cwd_node = mirror.nodes.get(int(mirror.cwd_node_id)) or _must_get_node(db, int(mirror.cwd_node_id))
    return {
        "session_id": int(mirror.session_id),
        "command_id": command_id,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.exit_code,
        "artifacts": result.artifacts,
        "cwd": _node_path(db, cwd_node),
    }




def _resolve_target_path_for_write(
    db: Session,
    session: VirtualShellSession,
    raw_path: str,
    *,
    node: VirtualFileNode | None = None,
) -> str | None:
    if node:
        return _node_path(db, node)
    parent, leaf = _resolve_parent_for_create(db, session.workspace_id, session.cwd_node_id, raw_path)
    if not parent or not leaf:
        return None
    parent_path = _node_path(db, parent)
    if parent_path == "/":
        return f"/{leaf}"
    return f"{parent_path.rstrip('/')}/{leaf}"


def _allow_write_path(db: Session, session: VirtualShellSession, path: str) -> tuple[bool, str]:
    mirror = _get_workspace_mirror(session.workspace_id, tenant_key=getattr(session, "tenant_id", None))
    if mirror:
        with mirror.lock:
            metadata = dict(mirror.workspace_metadata)
    else:
        tenant_key = _effective_tenant_key(getattr(session, "tenant_id", None))
        workspace = _repositories.workspace.get_workspace(db, session.workspace_id, tenant_key)
        metadata = dict(workspace.metadata_json or {}) if workspace else {}
    chapter_file = str(metadata.get("virtual_chapter_file") or "")

    if _path_is_under(path, VFS_NOTES_ROOT):
        return True, ""
    if path == chapter_file:
        return True, ""
    if _path_is_under(path, VFS_CHAPTERS_ROOT):
        if chapter_file:
            return False, f"write denied: only current chapter file is writable ({chapter_file})"
        return False, "write denied: chapter scope metadata is missing"
    return False, f"write denied: path is read-only ({path})"


def _search_text_lines(text: str, pattern: str) -> list[str]:
    regex = _safe_compile(pattern)
    matches: list[str] = []
    for line in text.splitlines():
        hit = bool(regex.search(line)) if regex else pattern.lower() in line.lower()
        if hit:
            matches.append(line)
    return matches


def _exec_touch(db: Session, session: VirtualShellSession, args: list[str]) -> VirtualCommandResult:
    if not args:
        return VirtualCommandResult("", "touch: missing file operand", 1, {})

    created: list[str] = []
    existing: list[str] = []
    for raw_path in args:
        node = _resolve_path(db, session.workspace_id, session.cwd_node_id, raw_path)
        resolved_target = _resolve_target_path_for_write(db, session, raw_path, node=node)
        if not resolved_target:
            return VirtualCommandResult("", f"touch: cannot touch '{raw_path}': invalid parent path", 1, {})
        allowed, deny_reason = _allow_write_path(db, session, resolved_target)
        if not allowed:
            return VirtualCommandResult("", f"touch: {deny_reason}", 1, {"path": resolved_target})
        if node:
            if node.node_type != "file":
                return VirtualCommandResult("", f"touch: cannot touch '{raw_path}': Not a file", 1, {})
            mirror = _get_workspace_mirror(session.workspace_id, tenant_key=getattr(session, "tenant_id", None))
            if mirror:
                with mirror.lock:
                    mirror_node = mirror.nodes.get(int(node.id), node)
                    mirror_node.updated_at = datetime.utcnow()
                    mirror.dirty_node_ids.add(int(mirror_node.id))
                    mirror.revision += 1
            else:
                node.updated_at = datetime.utcnow()
                _repositories.node.touch_node(db, node=node)
            existing.append(_node_path(db, node))
            continue

        parent, name = _resolve_parent_for_create(db, session.workspace_id, session.cwd_node_id, raw_path)
        if not parent:
            return VirtualCommandResult("", f"touch: cannot touch '{raw_path}': invalid parent path", 1, {})
        file_node = _get_or_create_child_file(db, session.workspace_id, parent.id, name, "")
        created.append(_node_path(db, file_node))

    summary = []
    if created:
        summary.append(f"created={len(created)}")
    if existing:
        summary.append(f"existing={len(existing)}")
    return VirtualCommandResult(
        "touch " + ", ".join(summary or ["ok"]),
        "",
        0,
        {"created": created, "existing": existing},
    )


def _exec_wc(db: Session, session: VirtualShellSession, args: list[str], *, input_text: str) -> VirtualCommandResult:
    if not args:
        return VirtualCommandResult(str(_count_lines(input_text)), "", 0, {"source": "stdin"})

    opts = [arg for arg in args if arg.startswith("-")]
    files = [arg for arg in args if not arg.startswith("-")]
    if any(opt != "-l" for opt in opts):
        return VirtualCommandResult("", "wc: only -l is supported", 1, {})
    if opts and "-l" not in opts:
        return VirtualCommandResult("", "wc: only -l is supported", 1, {})

    if not files:
        return VirtualCommandResult(str(_count_lines(input_text)), "", 0, {"source": "stdin"})

    lines: list[str] = []
    total = 0
    for target in files:
        node = _resolve_path(db, session.workspace_id, session.cwd_node_id, target)
        if not node or node.node_type != "file":
            return VirtualCommandResult("", f"wc: {target}: No such file", 1, {})
        count = _count_lines(_get_node_content(db, node))
        total += count
        lines.append(f"{count} {_node_path(db, node)}")
    if len(files) > 1:
        lines.append(f"{total} total")
    return VirtualCommandResult("\n".join(lines), "", 0, {"files": files, "total": total})


def _count_lines(text: str) -> int:
    return text.count("\n")


def _exec_edit(db: Session, session: VirtualShellSession, args: list[str]) -> VirtualCommandResult:
    if not args:
        return VirtualCommandResult("", "edit: missing path", 1, {})

    path = args[0]
    opts = _parse_options(args[1:])
    find_text = opts.get("--find")
    replace_text = opts.get("--replace")
    replace_all = "--all" in opts["flags"]
    if find_text is None or replace_text is None:
        return VirtualCommandResult("", "edit: require --find and --replace", 1, {})

    node = _resolve_path(db, session.workspace_id, session.cwd_node_id, path)
    if not node or node.node_type != "file":
        return VirtualCommandResult("", f"edit: file not found: {path}", 1, {})
    node_path = _node_path(db, node)
    allowed, deny_reason = _allow_write_path(db, session, node_path)
    if not allowed:
        return VirtualCommandResult("", f"edit: {deny_reason}", 1, {"path": node_path})

    before = _get_node_content(db, node)
    if find_text not in before:
        return VirtualCommandResult("", "edit: target text not found", 1, {"path": node_path})

    if replace_all:
        after = before.replace(find_text, replace_text)
        count = before.count(find_text)
    else:
        after = before.replace(find_text, replace_text, 1)
        count = 1

    version_no = _write_file(db, node, after, op="edit")
    return VirtualCommandResult(
        f"edited {count} occurrence(s) in {node_path} -> version {version_no}",
        "",
        0,
        {
            "path": node_path,
            "version": version_no,
            "replacements": count,
        },
    )


def _exec_patch(db: Session, session: VirtualShellSession, args: list[str]) -> VirtualCommandResult:
    if args and args[0] == "apply":
        args = args[1:]
    opts = _parse_options(args)
    path = opts.get("--path")
    if not path:
        return VirtualCommandResult("", "patch: require --path", 1, {})

    node = _resolve_path(db, session.workspace_id, session.cwd_node_id, path)
    if not node or node.node_type != "file":
        return VirtualCommandResult("", f"patch: file not found: {path}", 1, {})
    node_path = _node_path(db, node)
    allowed, deny_reason = _allow_write_path(db, session, node_path)
    if not allowed:
        return VirtualCommandResult("", f"patch: {deny_reason}", 1, {"path": node_path})

    unified = opts.get("--unified")
    find_text = opts.get("--find")
    replace_text = opts.get("--replace")
    before = _get_node_content(db, node)

    if unified:
        after, conflicts = _apply_unified_patch(before, unified)
        patch_id = _next_ephemeral_patch_id()

        if conflicts:
            return VirtualCommandResult(
                "",
                "patch: rejected hunks: " + json.dumps(conflicts, ensure_ascii=False),
                1,
                {"patch_id": patch_id, "conflicts": conflicts},
            )

        version_no = _write_file(db, node, after, op="patch")
        return VirtualCommandResult(
            f"patch applied to {node_path} -> version {version_no}",
            "",
            0,
            {"patch_id": patch_id, "path": node_path, "version": version_no},
        )

    if find_text is None or replace_text is None:
        return VirtualCommandResult("", "patch: require either --unified or (--find and --replace)", 1, {})

    if find_text not in before:
        return VirtualCommandResult("", "patch: target text not found", 1, {})

    after = before.replace(find_text, replace_text, 1)
    patch_id = _next_ephemeral_patch_id()
    version_no = _write_file(db, node, after, op="patch")

    return VirtualCommandResult(
        f"patch applied to {node_path} -> version {version_no}",
        "",
        0,
        {"patch_id": patch_id, "path": node_path, "version": version_no},
    )


def _apply_unified_patch(before: str, diff_text: str) -> tuple[str, list[dict[str, Any]]]:
    original = before.splitlines()
    lines = diff_text.splitlines()
    output: list[str] = []
    cursor = 0
    idx = 0
    conflicts: list[dict[str, Any]] = []

    while idx < len(lines):
        line = lines[idx]
        if not line.startswith("@@"):
            idx += 1
            continue

        match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
        if not match:
            conflicts.append({"line": idx + 1, "reason": "invalid hunk header"})
            break

        old_start = int(match.group(1))
        output.extend(original[cursor : max(old_start - 1, cursor)])
        pos = old_start - 1
        idx += 1

        while idx < len(lines) and not lines[idx].startswith("@@"):
            patch_line = lines[idx]
            if patch_line.startswith("\\"):
                idx += 1
                continue
            if not patch_line:
                marker = " "
                text = ""
            else:
                marker = patch_line[0]
                text = patch_line[1:]

            if marker == " ":
                if pos >= len(original) or original[pos] != text:
                    conflicts.append({"line": idx + 1, "reason": "context mismatch", "expected": text})
                    return before, conflicts
                output.append(original[pos])
                pos += 1
            elif marker == "-":
                if pos >= len(original) or original[pos] != text:
                    conflicts.append({"line": idx + 1, "reason": "remove mismatch", "expected": text})
                    return before, conflicts
                pos += 1
            elif marker == "+":
                output.append(text)
            else:
                conflicts.append({"line": idx + 1, "reason": f"unsupported marker: {marker}"})
                return before, conflicts
            idx += 1

        cursor = pos

    output.extend(original[cursor:])
    newline = "\n" if before.endswith("\n") else ""
    return "\n".join(output) + newline, conflicts


def _build_simple_patch(path: str, before: str, after: str) -> str:
    return "\n".join(
        [
            f"--- {path}",
            f"+++ {path}",
            "@@ -1,1 +1,1 @@",
            f"-{before}",
            f"+{after}",
        ]
    )


def _search_nodes(db: Session, workspace_id: int, node: VirtualFileNode, pattern: str) -> list[str]:
    regex = _safe_compile(pattern)
    file_nodes = _collect_files_for_search(db, workspace_id, node, pattern=pattern, regex=regex)
    matches: list[str] = []
    for item in file_nodes:
        content_text = _get_node_content(db, item)
        for i, line in enumerate(content_text.splitlines(), start=1):
            hit = bool(regex.search(line)) if regex else pattern.lower() in line.lower()
            if hit:
                matches.append(f"{_search_display_path(db, item)}:{i}:{line}")
    return matches


def _search_display_path(db: Session, node: VirtualFileNode) -> str:
    if node.parent_id is None and node.name.startswith("/"):
        return node.name
    return _node_path(db, node)


def _collect_files_for_search(
    db: Session,
    workspace_id: int,
    node: VirtualFileNode,
    *,
    pattern: str,
    regex: re.Pattern[str] | None,
) -> list[VirtualFileNode]:
    mirror = _get_workspace_mirror(workspace_id, tenant_key=getattr(node, "tenant_id", None))
    if mirror:
        with mirror.lock:
            if node.node_type == "file":
                return [mirror.nodes.get(int(node.id), node)]
            return _collect_files(db, workspace_id, int(node.id))
    bind = db.get_bind()
    dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
    if dialect_name != "postgresql":
        return [node] if node.node_type == "file" else _collect_files(db, workspace_id, node.id)

    # PostgreSQL optimization:
    # - Recursive CTE loads subtree in one round trip.
    # - Optional content pre-filter narrows candidate files for literal patterns.
    base_path = _node_path(db, node)
    root_id = int(node.id)
    tenant_key = _effective_tenant_key(getattr(node, "tenant_id", None))

    rows = _repositories.node.search_subtree_files(
        db,
        tenant_key=tenant_key,
        workspace_id=workspace_id,
        root_id=root_id,
        pattern=pattern,
        use_case_insensitive=regex is None,
        use_literal_case_sensitive=regex is not None and not REGEX_META_CHARS.search(pattern),
    )

    out: list[VirtualFileNode] = []
    for row in rows:
        rel_path = str(row["rel_path"] or "")
        virtual_path = base_path if not rel_path else f"{base_path.rstrip('/')}/{rel_path}" if base_path != "/" else f"/{rel_path}"
        out.append(
            VirtualFileNode(
                id=int(row["id"]),
                tenant_id=tenant_key,
                workspace_id=workspace_id,
                parent_id=None,
                name=virtual_path,
                node_type="file",
                content_text=str(row["content_text"] or ""),
                version_no=1,
            )
        )
    return out


def _safe_compile(pattern: str) -> re.Pattern[str] | None:
    try:
        return re.compile(pattern)
    except re.error:
        return None


def _collect_files(db: Session, workspace_id: int, parent_id: int) -> list[VirtualFileNode]:
    mirror = _get_workspace_mirror(workspace_id)
    if mirror:
        with mirror.lock:
            out: list[VirtualFileNode] = []
            stack = [parent_id]
            while stack:
                node_id = stack.pop()
                for child_id in mirror.children_by_parent.get(node_id, []):
                    child = mirror.nodes[child_id]
                    if child.node_type == "file":
                        out.append(child)
                    else:
                        stack.append(child.id)
            return out
    out: list[VirtualFileNode] = []
    stack = [parent_id]
    while stack:
        node_id = stack.pop()
        children = _list_children(db, workspace_id, node_id)
        for child in children:
            if child.node_type == "file":
                out.append(child)
            else:
                stack.append(child.id)
    return out


def _get_or_create_session(db: Session, workspace_id: int) -> VirtualShellSession:
    tenant_key = _effective_tenant_key()
    mirror = _get_workspace_mirror(workspace_id, tenant_key=tenant_key)
    if mirror:
        with mirror.lock:
            return VirtualShellSession(
                id=int(mirror.session_id),
                tenant_id=tenant_key,
                workspace_id=int(mirror.workspace_id),
                cwd_node_id=int(mirror.cwd_node_id),
                env_json={"PWD": VFS_ROOT},
                status="active",
            )
    session = _repositories.session.get_active_session(db, workspace_id, tenant_key)
    if session:
        return session

    root = _get_or_create_root(db, workspace_id)
    workspace_dir = _get_or_create_child_dir(db, workspace_id, root.id, "workspace")
    return _repositories.session.create_session(
        db,
        tenant_key=tenant_key,
        workspace_id=workspace_id,
        cwd_node_id=workspace_dir.id,
        env_json={"PWD": "/workspace"},
        status="active",
    )


def _get_or_create_root(db: Session, workspace_id: int) -> VirtualFileNode:
    tenant_key = _effective_tenant_key()
    root = _repositories.node.get_root(db, workspace_id, tenant_key)
    if root:
        return root
    return _repositories.node.create_node(
        db,
        tenant_key=tenant_key,
        workspace_id=workspace_id,
        parent_id=None,
        name="",
        node_type="dir",
        content_text="",
        version_no=1,
    )


def _get_or_create_child_dir(db: Session, workspace_id: int, parent_id: int, name: str) -> VirtualFileNode:
    tenant_key = _effective_tenant_key()
    node = _repositories.node.get_child(
        db,
        tenant_key=tenant_key,
        workspace_id=workspace_id,
        parent_id=parent_id,
        name=name,
        node_type="dir",
    )
    if node:
        return node
    return _repositories.node.create_node(
        db,
        tenant_key=tenant_key,
        workspace_id=workspace_id,
        parent_id=parent_id,
        name=name,
        node_type="dir",
        content_text="",
        version_no=1,
    )


def _get_or_create_child_file(db: Session, workspace_id: int, parent_id: int, name: str, content: str) -> VirtualFileNode:
    tenant_key = _effective_tenant_key()
    mirror = _get_workspace_mirror(workspace_id, tenant_key=tenant_key)
    if mirror:
        with mirror.lock:
            parent = mirror.nodes.get(parent_id)
            if not parent or parent.node_type != "dir":
                raise ValueError(f"invalid virtual parent: {parent_id}")
            parent_path = _mirror_node_path_locked(mirror, parent)
            target_path = f"{parent_path.rstrip('/')}/{name}" if parent_path != "/" else f"/{name}"
            existing_id = mirror.path_to_id.get(target_path)
            if existing_id is not None:
                return mirror.nodes[existing_id]
            node = VirtualFileNode(
                id=mirror.next_temp_id,
                tenant_id=tenant_key,
                workspace_id=workspace_id,
                parent_id=parent_id,
                name=name,
                node_type="file",
                content_text=content,
                version_no=1,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            mirror.next_temp_id -= 1
            mirror.nodes[int(node.id)] = node
            mirror.children_by_parent.setdefault(parent_id, []).append(int(node.id))
            _ensure_children_sorted_locked(mirror, parent_id)
            mirror.path_to_id[target_path] = int(node.id)
            mirror.dirty_node_ids.add(int(node.id))
            mirror.revision += 1
            _cache_metric_inc("write_ops")
            return node
    node = _repositories.node.get_child(
        db,
        tenant_key=tenant_key,
        workspace_id=workspace_id,
        parent_id=parent_id,
        name=name,
        node_type="file",
    )
    if node:
        return node
    return _repositories.node.create_node(
        db,
        tenant_key=tenant_key,
        workspace_id=workspace_id,
        parent_id=parent_id,
        name=name,
        node_type="file",
        content_text=content,
        version_no=1,
    )


def _write_file(db: Session, node: VirtualFileNode, content: str, *, op: str) -> int:
    mirror = _get_workspace_mirror(int(node.workspace_id), tenant_key=getattr(node, "tenant_id", None))
    if mirror:
        with mirror.lock:
            mirror_node = mirror.nodes.get(int(node.id))
            if mirror_node is None:
                mirror.nodes[int(node.id)] = node
                mirror_node = node
                _rebuild_workspace_mirror_indexes_locked(mirror)
            base_version = int(mirror_node.version_no or 1)
            next_version = base_version + 1
            mirror_node.content_text = content
            mirror_node.version_no = next_version
            mirror_node.updated_at = datetime.utcnow()
            mirror.dirty_node_ids.add(int(mirror_node.id))
            mirror.revision += 1
            _cache_metric_inc("write_ops")
            return next_version
    if not MEMORY_CACHE_ENABLED:
        base_version = node.version_no
        next_version = base_version + 1
        node.content_text = content
        node.version_no = next_version
        node.updated_at = datetime.utcnow()
        _repositories.node.touch_node(db, node=node)
        return int(next_version)

    next_version = _update_cache_after_write(node, content, op=op)
    return int(next_version)




def _must_get_node(db: Session, node_id: int | None) -> VirtualFileNode:
    if not node_id:
        raise ValueError("missing virtual node id")
    active = _active_workspace_mirror()
    mirrors = [active] if active else []
    for mirror in mirrors:
        with mirror.lock:
            node = mirror.nodes.get(int(node_id))
            if node:
                return node
    tenant_key = _effective_tenant_key()
    node = _repositories.node.get_node(db, node_id, tenant_key)
    if not node:
        raise ValueError(f"virtual node not found: {node_id}")
    return node
