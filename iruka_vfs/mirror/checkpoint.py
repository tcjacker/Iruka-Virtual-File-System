from __future__ import annotations

from datetime import datetime
import json
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
from iruka_vfs.file_sources import WritableFileSource
from iruka_vfs.models import WorkspaceMirror
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


def _clear_checkpoint_failure_state(client, base_key: str) -> None:
    from iruka_vfs import workspace_mirror as mirror_api

    client.delete(mirror_api.workspace_retry_count_key(base_key))
    client.delete(mirror_api.workspace_dead_letter_payload_key(base_key))
    client.srem(mirror_api.workspace_dead_letter_set_key(), base_key)


def _record_checkpoint_failure(
    client,
    base_key: str,
    *,
    reason: str,
    error_payload: dict[str, object],
) -> tuple[bool, int]:
    from iruka_vfs import workspace_mirror as mirror_api

    failure_count = int(client.get(mirror_api.workspace_retry_count_key(base_key)) or 0) + 1
    client.set(mirror_api.workspace_retry_count_key(base_key), str(failure_count))
    if failure_count >= max(int(VFS_CHECKPOINT_MAX_FAILURES), 1):
        client.sadd(mirror_api.workspace_dead_letter_set_key(), base_key)
        client.set(
            mirror_api.workspace_dead_letter_payload_key(base_key),
            json.dumps(
                {
                    "base_key": base_key,
                    "failure_count": failure_count,
                    "reason": reason,
                    "last_failure": error_payload,
                    "ts": datetime.utcnow().isoformat(),
                },
                ensure_ascii=False,
            ),
        )
        return True, failure_count
    return False, failure_count


def snapshot_workspace_checkpoint_metrics() -> dict[str, int]:
    from iruka_vfs import service
    from iruka_vfs import workspace_mirror as mirror_api

    client = service._get_redis_client()
    queue_depth = 0
    enqueued = 0
    dirty = 0
    dead_letter = 0
    try:
        if hasattr(client, "llen"):
            queue_depth = int(client.llen(mirror_api.workspace_queue_key()) or 0)
        elif hasattr(client, "queues"):
            queue_depth = len(client.queues.get(mirror_api.workspace_queue_key(), []))
        if hasattr(client, "scard"):
            enqueued = int(client.scard(mirror_api.workspace_enqueued_key()) or 0)
            dirty = int(client.scard(mirror_api.workspace_dirty_set_key()) or 0)
            dead_letter = int(client.scard(mirror_api.workspace_dead_letter_set_key()) or 0)
        elif hasattr(client, "sets"):
            enqueued = len(client.sets.get(mirror_api.workspace_enqueued_key(), set()))
            dirty = len(client.sets.get(mirror_api.workspace_dirty_set_key(), set()))
            dead_letter = len(client.sets.get(mirror_api.workspace_dead_letter_set_key(), set()))
    except Exception:
        pass
    return {
        "checkpoint_queue_depth": int(queue_depth),
        "checkpoint_enqueued": int(enqueued),
        "checkpoint_dirty_workspaces": int(dirty),
        "checkpoint_dead_letter": int(dead_letter),
    }


def enqueue_workspace_checkpoint(base_key: str, *, due_at: float | None = None, force: bool = False) -> None:
    from iruka_vfs import service
    from iruka_vfs import workspace_mirror as mirror_api

    client = service._get_redis_client()
    if not force and client.get(mirror_api.workspace_dead_letter_payload_key(base_key)):
        return
    scheduled_at = due_at if due_at is not None else time.time() + max(VFS_CHECKPOINT_DEBOUNCE_SECONDS, 0.0)
    client.set(mirror_api.workspace_due_key(base_key), str(scheduled_at))
    if int(client.sadd(mirror_api.workspace_enqueued_key(), base_key) or 0) == 1:
        client.rpush(mirror_api.workspace_queue_key(), base_key)


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
        if node.parent_id is not None and int(node.parent_id) == temp_id:
            node.parent_id = real_id
        current.nodes[real_id] = node
        for child_ids in current.children_by_parent.values():
            for idx, child_id in enumerate(child_ids):
                if child_id == temp_id:
                    child_ids[idx] = real_id
        current.path_to_id = {
            path: (real_id if node_id == temp_id else node_id)
            for path, node_id in current.path_to_id.items()
        }
        if current.cwd_node_id == temp_id:
            current.cwd_node_id = real_id

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

    client = service._get_redis_client()
    while True:
        try:
            item = client.blpop(mirror_api.workspace_queue_key(), timeout=max(int(MEMORY_CACHE_FLUSH_INTERVAL_SECONDS), 1))
        except Exception:
            continue
        if not item:
            continue
        _, base_key = item
        try:
            if client.get(mirror_api.workspace_dead_letter_payload_key(str(base_key))):
                client.srem(mirror_api.workspace_enqueued_key(), str(base_key))
                client.delete(mirror_api.workspace_due_key(str(base_key)))
                continue
            due_raw = client.get(mirror_api.workspace_due_key(str(base_key)))
            if due_raw:
                try:
                    due_at = float(due_raw)
                except (TypeError, ValueError):
                    due_at = 0.0
                remaining = due_at - time.time()
                if remaining > 0:
                    time.sleep(min(remaining, max(VFS_CHECKPOINT_DEBOUNCE_SECONDS, 0.01)))
                    client.rpush(mirror_api.workspace_queue_key(), str(base_key))
                    service._cache_metric_inc("checkpoint_requeue")
                    continue
            ok = flush_workspace_mirror(None, base_key=str(base_key))
            client.srem(mirror_api.workspace_enqueued_key(), str(base_key))
            client.delete(mirror_api.workspace_due_key(str(base_key)))
            current = mirror_api.load_workspace_mirror_by_base_key(client, str(base_key))
            if current:
                if mirror_has_dirty_state(current):
                    enqueue_workspace_checkpoint(str(base_key))
                    service._cache_metric_inc("checkpoint_requeue")
                    continue
            if not ok:
                error_raw = client.get(mirror_api.workspace_error_key(str(base_key)))
                error_payload = json.loads(error_raw) if error_raw else {"error_message": "flush returned false"}
                dead_lettered, failure_count = _record_checkpoint_failure(
                    client,
                    str(base_key),
                    reason="flush-returned-false",
                    error_payload=dict(error_payload),
                )
                if dead_lettered:
                    service._cache_metric_inc("checkpoint_dead_letter")
                    client.srem(mirror_api.workspace_enqueued_key(), str(base_key))
                    client.delete(mirror_api.workspace_due_key(str(base_key)))
                    continue
                enqueue_workspace_checkpoint(
                    str(base_key),
                    due_at=time.time() + _checkpoint_retry_delay_seconds(failure_count),
                    force=True,
                )
                service._cache_metric_inc("checkpoint_retry")
                continue
            _clear_checkpoint_failure_state(client, str(base_key))
        except Exception as exc:
            service._cache_metric_inc("flush_error")
            error_payload = {
                "ts": datetime.utcnow().isoformat(),
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "traceback": traceback.format_exc(limit=20),
            }
            client.set(
                mirror_api.workspace_error_key(str(base_key)),
                json.dumps(error_payload, ensure_ascii=False),
            )
            client.srem(mirror_api.workspace_enqueued_key(), str(base_key))
            client.delete(mirror_api.workspace_due_key(str(base_key)))
            dead_lettered, failure_count = _record_checkpoint_failure(
                client,
                str(base_key),
                reason="worker-exception",
                error_payload=error_payload,
            )
            if dead_lettered:
                service._cache_metric_inc("checkpoint_dead_letter")
                continue
            enqueue_workspace_checkpoint(
                str(base_key),
                due_at=time.time() + _checkpoint_retry_delay_seconds(failure_count),
                force=True,
            )
            service._cache_metric_inc("checkpoint_retry")


def flush_workspace_mirror(mirror: WorkspaceMirror | None, *, base_key: str | None = None) -> bool:
    from iruka_vfs import service
    from iruka_vfs import workspace_mirror as mirror_api

    if runtime_state.workspace_checkpoint_session_maker is None:
        return False

    client = service._get_redis_client()
    resolved_base_key = base_key or mirror_api.workspace_base_key(mirror.tenant_key, mirror.workspace_id)
    lock = client.lock(mirror_api.workspace_lock_key(resolved_base_key), timeout=30, blocking_timeout=1)
    if not lock.acquire(blocking=True):
        return False
    try:
        if mirror is None:
            mirror = mirror_api.load_workspace_mirror_by_base_key(client, resolved_base_key)
            if not mirror:
                client.srem(mirror_api.workspace_dirty_set_key(), resolved_base_key)
                return True
        with mirror.lock:
            snapshot = _snapshot_dirty_batch_locked(mirror)
            dirty_ids = list(snapshot["dirty_ids"])
            if not dirty_ids and not bool(snapshot["session_dirty"]) and not bool(snapshot["metadata_dirty"]):
                client.srem(mirror_api.workspace_dirty_set_key(), resolved_base_key)
                return True

        db = runtime_state.workspace_checkpoint_session_maker()
        try:
            remapped_ids: list[tuple[int, int]] = []
            for payload in list(snapshot["dirty_payloads"]):
                node_id = int(payload["node_id"])
                if node_id > 0:
                    mirror_api._repositories.node.update_node_content(
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

                row = mirror_api._repositories.node.create_node(
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

            if bool(snapshot["session_dirty"]):
                mirror_api._repositories.session.update_session_cwd(
                    db,
                    session_id=int(mirror.session_id),
                    tenant_key=str(snapshot["tenant_key"]),
                    cwd_node_id=int(snapshot["cwd_node_id"]),
                )
            if bool(snapshot["metadata_dirty"]):
                mirror_api._repositories.workspace.update_workspace_metadata(
                    db,
                    workspace_id=int(snapshot["workspace_id"]),
                    tenant_key=str(snapshot["tenant_key"]),
                    metadata_json=dict(snapshot["workspace_metadata"]),
                )
            db.commit()
            runtime_seed = service._get_registered_runtime_seed(int(snapshot["workspace_id"]), str(snapshot["tenant_key"]))
            primary_file = runtime_seed.primary_file if runtime_seed else None
            chapter_path = str(dict(snapshot["workspace_metadata"]).get("virtual_chapter_file") or "")
            if (
                primary_file is not None
                and isinstance(primary_file, WritableFileSource)
                and primary_file.virtual_path == chapter_path
                and primary_file.write_text is not None
            ):
                chapter_node_id = mirror.path_to_id.get(chapter_path)
                chapter_node = mirror.nodes.get(int(chapter_node_id)) if chapter_node_id is not None else None
                if chapter_node is not None:
                    primary_file.write_text(str(chapter_node.content_text or ""))
        except Exception as exc:
            db.rollback()
            service._cache_metric_inc("flush_error")
            client.set(
                mirror_api.workspace_error_key(resolved_base_key),
                json.dumps(
                    {
                        "workspace_id": int(snapshot["workspace_id"]) if "snapshot" in locals() else None,
                        "tenant_key": str(snapshot["tenant_key"]) if "snapshot" in locals() else None,
                        "ts": datetime.utcnow().isoformat(),
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                        "traceback": traceback.format_exc(limit=20),
                    },
                    ensure_ascii=False,
                ),
            )
            return False
        try:
            current = mirror_api.load_workspace_mirror_by_base_key(client, resolved_base_key)
            if not current:
                client.srem(mirror_api.workspace_dirty_set_key(), resolved_base_key)
                client.delete(mirror_api.workspace_error_key(resolved_base_key))
                return True
            with current.lock:
                _confirm_flushed_snapshot_locked(current, snapshot=snapshot, remapped_ids=remapped_ids)
                mirror_api.set_workspace_mirror(current)
                client.delete(mirror_api.workspace_error_key(resolved_base_key))
                _clear_checkpoint_failure_state(client, resolved_base_key)
        finally:
            db.close()
    finally:
        try:
            lock.release()
        except Exception:
            pass
    return True
