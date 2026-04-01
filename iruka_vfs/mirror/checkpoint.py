from __future__ import annotations

from datetime import datetime
import time
import traceback

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from iruka_vfs.constants import (
    MEMORY_CACHE_FLUSH_BATCH,
    MEMORY_CACHE_FLUSH_INTERVAL_SECONDS,
    VFS_CHECKPOINT_DEBOUNCE_SECONDS,
    VFS_CHECKPOINT_MAX_FAILURES,
    VFS_CHECKPOINT_RETRY_BASE_SECONDS,
    VFS_CHECKPOINT_RETRY_MAX_SECONDS,
)
from iruka_vfs.dependency_resolution import resolve_vfs_repositories
from iruka_vfs.models import WorkspaceMirror
from iruka_vfs.service_ops.state import get_workspace_state_store
from iruka_vfs import runtime_state


def mirror_has_dirty_state(mirror: WorkspaceMirror) -> bool:
    return bool(
        mirror.dirty_content_node_ids
        or mirror.dirty_structure_node_ids
        or mirror.dirty_session
        or mirror.dirty_workspace_metadata
    )


def _checkpoint_retry_delay_seconds(failure_count: int) -> float:
    exponent = max(int(failure_count) - 1, 0)
    return min(max(VFS_CHECKPOINT_RETRY_BASE_SECONDS, 0.01) * (2 ** exponent), max(VFS_CHECKPOINT_RETRY_MAX_SECONDS, 0.01))


def _clear_checkpoint_failure_state(workspace_ref) -> None:
    store = get_workspace_state_store()
    store.clear_retry_count(workspace_ref)
    store.clear_dead_letter_payload(workspace_ref)
    store.remove_dead_letter(workspace_ref)


def _record_checkpoint_failure(workspace_ref, *, reason: str, error_payload: dict[str, object]) -> tuple[bool, int]:
    store = get_workspace_state_store()
    failure_count = store.increment_retry_count(workspace_ref)
    if failure_count >= max(int(VFS_CHECKPOINT_MAX_FAILURES), 1):
        store.add_dead_letter(workspace_ref)
        store.set_dead_letter_payload(
            workspace_ref,
            {
                "workspace_id": int(workspace_ref.workspace_id),
                "tenant_key": str(workspace_ref.tenant_key),
                "scope_key": str(workspace_ref.scope_key),
                "failure_count": failure_count,
                "reason": reason,
                "last_failure": error_payload,
                "ts": datetime.utcnow().isoformat(),
            },
        )
        return True, failure_count
    return False, failure_count


def snapshot_workspace_checkpoint_metrics() -> dict[str, int]:
    return get_workspace_state_store().get_checkpoint_metrics()


def enqueue_workspace_checkpoint(workspace_ref, *, due_at: float | None = None, force: bool = False) -> None:
    scheduled_at = due_at if due_at is not None else time.time() + max(VFS_CHECKPOINT_DEBOUNCE_SECONDS, 0.0)
    get_workspace_state_store().enqueue_workspace_checkpoint(workspace_ref, due_at=scheduled_at, force=force)


def resolve_workspace_ref_for_flush(
    workspace_id: int,
    *,
    tenant_key: str,
    scope_key: str | None = None,
):
    store = get_workspace_state_store()
    mirror = None
    if scope_key:
        mirror = store.get_workspace_mirror(
            workspace_id,
            tenant_key=tenant_key,
            scope_key=scope_key,
        )
    if mirror is None:
        mirror = store.get_workspace_mirror(workspace_id, tenant_key=tenant_key)
    if mirror is None:
        return None
    return store.workspace_ref(mirror=mirror)


def _node_matches_payload(node, payload: dict[str, object]) -> bool:
    if node is None:
        return False
    return (
        int(node.parent_id) if node.parent_id is not None else None
    ) == payload["parent_id"] and str(node.name or "") == payload["name"] and str(
        node.node_type or "file"
    ) == str(payload["node_type"] or "file") and str(node.content_text or "") == str(
        payload["content_text"] or ""
    ) and int(node.version_no or 1) == int(payload["version_no"] or 1)


def _snapshot_dirty_batch_locked(mirror: WorkspaceMirror) -> dict[str, object]:
    dirty_content_ids = sorted(int(node_id) for node_id in mirror.dirty_content_node_ids)[:MEMORY_CACHE_FLUSH_BATCH]
    dirty_structure_ids = sorted(int(node_id) for node_id in mirror.dirty_structure_node_ids)[:MEMORY_CACHE_FLUSH_BATCH]
    dirty_ids = list(dict.fromkeys(dirty_structure_ids + dirty_content_ids))[:MEMORY_CACHE_FLUSH_BATCH]
    dirty_payloads: list[dict[str, object]] = []
    for node_id in dirty_ids:
        node = mirror.nodes.get(node_id)
        if not node:
            continue
        dirty_payloads.append(
            {
                "node_id": int(node.id),
                "parent_id": int(node.parent_id) if node.parent_id is not None else None,
                "name": str(node.name or ""),
                "node_type": str(node.node_type or "file"),
                "content_text": str(node.content_text or ""),
                "version_no": int(node.version_no or 1),
            }
        )
    return {
        "snapshot_revision": int(mirror.revision),
        "dirty_content_ids": set(dirty_content_ids),
        "dirty_structure_ids": set(dirty_structure_ids),
        "dirty_ids": dirty_ids,
        "dirty_payloads": dirty_payloads,
        "session_dirty": bool(mirror.dirty_session),
        "metadata_dirty": bool(mirror.dirty_workspace_metadata),
        "cwd_node_id": int(mirror.cwd_node_id),
        "workspace_metadata": dict(mirror.workspace_metadata),
        "tenant_key": str(mirror.tenant_key),
        "workspace_id": int(mirror.workspace_id),
    }


def _remap_payload_node_ids(
    payloads: list[dict[str, object]],
    remapped_ids: list[tuple[int, int]],
) -> list[dict[str, object]]:
    if not remapped_ids:
        return payloads
    remap = {int(temp_id): int(real_id) for temp_id, real_id in remapped_ids}
    remapped_payloads: list[dict[str, object]] = []
    for payload in payloads:
        item = dict(payload)
        node_id = int(item["node_id"])
        parent_id = item["parent_id"]
        item["node_id"] = remap.get(node_id, node_id)
        if parent_id is not None:
            item["parent_id"] = remap.get(int(parent_id), int(parent_id))
        remapped_payloads.append(item)
    return remapped_payloads


def _confirm_flushed_snapshot_locked(
    current: WorkspaceMirror,
    *,
    snapshot: dict[str, object],
    remapped_ids: list[tuple[int, int]],
) -> None:
    from iruka_vfs import workspace_mirror as mirror_api

    snapshot_revision = int(snapshot["snapshot_revision"])
    flushed_payloads = _remap_payload_node_ids(list(snapshot["dirty_payloads"]), remapped_ids)
    remap = {int(temp_id): int(real_id) for temp_id, real_id in remapped_ids}
    content_ids = {remap.get(int(node_id), int(node_id)) for node_id in set(snapshot["dirty_content_ids"])}
    structure_ids = {remap.get(int(node_id), int(node_id)) for node_id in set(snapshot["dirty_structure_ids"])}

    for temp_id, real_id in remapped_ids:
        node = current.nodes.pop(temp_id, None)
        if not node:
            continue
        node.id = real_id
        if node.parent_id is not None:
            node.parent_id = remap.get(int(node.parent_id), int(node.parent_id))
        current.nodes[real_id] = node
        if current.cwd_node_id == temp_id:
            current.cwd_node_id = real_id

    for node in current.nodes.values():
        if node.parent_id is not None:
            node.parent_id = remap.get(int(node.parent_id), int(node.parent_id))

    mirror_api.rebuild_workspace_mirror_indexes_locked(current)

    for payload in flushed_payloads:
        node_id = int(payload["node_id"])
        current_node = current.nodes.get(node_id)
        if node_id in content_ids and _node_matches_payload(current_node, payload):
            current.dirty_content_node_ids.discard(node_id)
        if node_id in structure_ids and _node_matches_payload(current_node, payload):
            current.dirty_structure_node_ids.discard(node_id)

    if bool(snapshot["session_dirty"]) and int(current.cwd_node_id) == int(snapshot["cwd_node_id"]):
        current.dirty_session = False
    if bool(snapshot["metadata_dirty"]) and dict(current.workspace_metadata) == dict(snapshot["workspace_metadata"]):
        current.dirty_workspace_metadata = False

    current.checkpoint_revision = max(int(current.checkpoint_revision), snapshot_revision)


def ensure_workspace_checkpoint_worker(engine: Engine) -> None:
    if runtime_state.workspace_checkpoint_worker_started:
        return
    with runtime_state.redis_client_lock:
        if runtime_state.workspace_checkpoint_worker_started:
            return
        runtime_state.workspace_checkpoint_session_maker = sessionmaker(
            bind=engine,
            autoflush=False,
            autocommit=False,
            class_=Session,
        )
        import threading

        worker = threading.Thread(
            target=workspace_checkpoint_worker,
            name="vfs-workspace-checkpoint-worker",
            daemon=True,
        )
        worker.start()
        runtime_state.workspace_checkpoint_worker_started = True


def workspace_checkpoint_worker() -> None:
    from iruka_vfs import service
    from iruka_vfs import workspace_mirror as mirror_api

    if runtime_state.workspace_checkpoint_session_maker is None:
        return

    store = get_workspace_state_store()
    while True:
        try:
            workspace_ref = store.pop_checkpoint(timeout_seconds=max(int(MEMORY_CACHE_FLUSH_INTERVAL_SECONDS), 1))
        except Exception:
            continue
        if not workspace_ref:
            continue
        try:
            if store.get_dead_letter_payload(workspace_ref):
                store.clear_checkpoint_schedule(workspace_ref)
                continue
            due_at = store.get_checkpoint_due_at(workspace_ref)
            if due_at is not None:
                remaining = due_at - time.time()
                if remaining > 0:
                    time.sleep(min(remaining, max(VFS_CHECKPOINT_DEBOUNCE_SECONDS, 0.01)))
                    store.requeue_checkpoint(workspace_ref)
                    service._cache_metric_inc("checkpoint_requeue")
                    continue
            ok, has_more_dirty = run_checkpoint_cycle(workspace_ref)
            if has_more_dirty:
                service._cache_metric_inc("checkpoint_requeue")
                continue
            if not ok:
                error_payload = store.get_error_payload(workspace_ref) or {"error_message": "flush returned false"}
                dead_lettered, failure_count = _record_checkpoint_failure(
                    workspace_ref,
                    reason="flush-returned-false",
                    error_payload=dict(error_payload),
                )
                if dead_lettered:
                    service._cache_metric_inc("checkpoint_dead_letter")
                    store.clear_checkpoint_schedule(workspace_ref)
                    continue
                enqueue_workspace_checkpoint(
                    workspace_ref,
                    due_at=time.time() + _checkpoint_retry_delay_seconds(failure_count),
                    force=True,
                )
                service._cache_metric_inc("checkpoint_retry")
                continue
            _clear_checkpoint_failure_state(workspace_ref)
        except Exception as exc:
            service._cache_metric_inc("flush_error")
            error_payload = {
                "ts": datetime.utcnow().isoformat(),
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "traceback": traceback.format_exc(limit=20),
            }
            store.set_error_payload(workspace_ref, error_payload)
            store.clear_checkpoint_schedule(workspace_ref)
            dead_lettered, failure_count = _record_checkpoint_failure(
                workspace_ref,
                reason="worker-exception",
                error_payload=error_payload,
            )
            if dead_lettered:
                service._cache_metric_inc("checkpoint_dead_letter")
                continue
            enqueue_workspace_checkpoint(
                workspace_ref,
                due_at=time.time() + _checkpoint_retry_delay_seconds(failure_count),
                force=True,
            )
            service._cache_metric_inc("checkpoint_retry")


def run_checkpoint_cycle(workspace_ref) -> tuple[bool, bool]:
    store = get_workspace_state_store()
    ok = flush_workspace_mirror(None, workspace_ref=workspace_ref)
    store.clear_checkpoint_schedule(workspace_ref)
    current = store.load_workspace_mirror(workspace_ref)
    has_more_dirty = bool(current and mirror_has_dirty_state(current))
    if has_more_dirty:
        enqueue_workspace_checkpoint(workspace_ref)
    return ok, has_more_dirty


def flush_workspace_mirror(mirror: WorkspaceMirror | None, *, workspace_ref=None) -> bool:
    from iruka_vfs import service
    from iruka_vfs import workspace_mirror as mirror_api

    if runtime_state.workspace_checkpoint_session_maker is None:
        return False

    store = get_workspace_state_store()
    resolved_ref = workspace_ref or store.workspace_ref(mirror=mirror)
    transaction = _load_checkpoint_transaction_context(store, mirror=mirror, workspace_ref=resolved_ref)
    if transaction is None:
        return True
    mirror = transaction["mirror"]
    lock = transaction["lock"]
    try:
        with mirror.lock:
            snapshot = _snapshot_dirty_batch_locked(mirror)
            dirty_ids = list(snapshot["dirty_ids"])
            if not dirty_ids and not bool(snapshot["session_dirty"]) and not bool(snapshot["metadata_dirty"]):
                store.clear_workspace_dirty(resolved_ref)
                return True

        db = runtime_state.workspace_checkpoint_session_maker()
        try:
            repositories = resolve_vfs_repositories()
            remapped_ids: list[tuple[int, int]] = []
            pending_payloads = [dict(item) for item in list(snapshot["dirty_payloads"])]
            while pending_payloads:
                next_round: list[dict[str, object]] = []
                progress_made = False
                for payload in pending_payloads:
                    parent_id = payload["parent_id"]
                    if parent_id is not None and int(parent_id) < 0:
                        remapped_parent_id = next(
                            (real_id for temp_id, real_id in remapped_ids if int(temp_id) == int(parent_id)),
                            None,
                        )
                        if remapped_parent_id is None:
                            next_round.append(payload)
                            continue
                        payload["parent_id"] = remapped_parent_id

                    progress_made = True
                    node_id = int(payload["node_id"])
                    if node_id > 0:
                        repositories.node.update_node_content(
                            db,
                            node_id=node_id,
                            tenant_key=str(snapshot["tenant_key"]),
                            parent_id=payload["parent_id"],
                            name=payload["name"],
                            node_type=payload["node_type"],
                            content_text=payload["content_text"],
                            version_no=payload["version_no"],
                        )
                        service._cache_metric_inc("flush_ok")
                        continue

                    row = repositories.node.create_node(
                        db,
                        tenant_key=str(snapshot["tenant_key"]),
                        workspace_id=int(snapshot["workspace_id"]),
                        parent_id=payload["parent_id"],
                        name=payload["name"],
                        node_type=payload["node_type"],
                        content_text=payload["content_text"],
                        version_no=payload["version_no"],
                    )
                    remapped_ids.append((node_id, int(row.id)))
                    service._cache_metric_inc("flush_ok")
                if not progress_made:
                    raise ValueError("flush could not remap temporary parent ids")
                pending_payloads = next_round

            if bool(snapshot["session_dirty"]):
                repositories.session.update_session_cwd(
                    db,
                    session_id=int(mirror.session_id),
                    tenant_key=str(snapshot["tenant_key"]),
                    cwd_node_id=int(snapshot["cwd_node_id"]),
                )
            if bool(snapshot["metadata_dirty"]):
                repositories.workspace.update_workspace_metadata(
                    db,
                    workspace_id=int(snapshot["workspace_id"]),
                    tenant_key=str(snapshot["tenant_key"]),
                    metadata_json=dict(snapshot["workspace_metadata"]),
                )
            db.commit()
        except Exception as exc:
            db.rollback()
            service._cache_metric_inc("flush_error")
            store.set_error_payload(
                resolved_ref,
                {
                    "workspace_id": int(snapshot["workspace_id"]) if "snapshot" in locals() else None,
                    "tenant_key": str(snapshot["tenant_key"]) if "snapshot" in locals() else None,
                    "ts": datetime.utcnow().isoformat(),
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "traceback": traceback.format_exc(limit=20),
                },
            )
            return False
        try:
            current = store.load_workspace_mirror(resolved_ref)
            if not current:
                store.clear_workspace_dirty(resolved_ref)
                store.clear_error_payload(resolved_ref)
                return True
            with current.lock:
                _confirm_flushed_snapshot_locked(current, snapshot=snapshot, remapped_ids=remapped_ids)
                mirror_api.set_workspace_mirror(current)
                store.clear_error_payload(resolved_ref)
                _clear_checkpoint_failure_state(resolved_ref)
        finally:
            db.close()
    finally:
        try:
            lock.release()
        except Exception:
            pass
    return True


def _load_checkpoint_transaction_context(store, *, mirror: WorkspaceMirror | None, workspace_ref):
    current = mirror if mirror is not None else store.load_workspace_mirror(workspace_ref)
    if not current:
        store.clear_workspace_dirty(workspace_ref)
        return None
    lock = store.workspace_lock(mirror=current, workspace_ref=workspace_ref)
    if not lock.acquire(blocking=True):
        return None
    current = store.load_workspace_mirror(workspace_ref)
    if not current:
        try:
            lock.release()
        except Exception:
            pass
        store.clear_workspace_dirty(workspace_ref)
        return None
    return {"mirror": current, "lock": lock}
