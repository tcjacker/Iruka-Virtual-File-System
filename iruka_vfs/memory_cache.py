from __future__ import annotations

from datetime import datetime
import time

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from iruka_vfs.constants import (
    MEMORY_CACHE_ENABLED,
    MEMORY_CACHE_FLUSH_BATCH,
    MEMORY_CACHE_FLUSH_INTERVAL_SECONDS,
    MEMORY_CACHE_MAX_BYTES,
    MEMORY_CACHE_MAX_FILES,
)
from iruka_vfs.models import FileCacheEntry
from iruka_vfs import runtime_state as state


def estimate_text_bytes(text_value: str) -> int:
    return len((text_value or "").encode("utf-8"))


def cache_metric_inc(name: str, delta: int = 1) -> None:
    if name not in state.mem_cache_metrics:
        return
    state.mem_cache_metrics[name] = int(state.mem_cache_metrics.get(name) or 0) + delta


def snapshot_virtual_fs_cache_metrics() -> dict[str, int]:
    from iruka_vfs import service

    with state.mem_cache_lock:
        payload = dict(state.mem_cache_metrics)
        payload["entries"] = len(state.mem_cache_entries)
        payload["dirty_entries"] = len(state.mem_cache_dirty_ids)
        payload["cache_bytes"] = state.mem_cache_current_bytes
    try:
        client = service._get_redis_client()
        payload["workspace_dirty_nodes"] = int(client.scard(service._workspace_dirty_set_key()) or 0)
    except Exception:
        payload["workspace_dirty_nodes"] = 0
    return payload


def ensure_mem_cache_worker(engine: Engine) -> None:
    if not MEMORY_CACHE_ENABLED:
        return
    with state.mem_cache_lock:
        if state.mem_cache_worker_started:
            return
        state.mem_cache_session_maker = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
        import threading

        worker = threading.Thread(target=mem_cache_flush_worker, name="vfs-mem-cache-flush-worker", daemon=True)
        worker.start()
        state.mem_cache_worker_started = True


def touch_cache_lru(file_id: int) -> None:
    if file_id in state.mem_cache_lru:
        state.mem_cache_lru.move_to_end(file_id)
        return
    state.mem_cache_lru[file_id] = None


def evict_cache_if_needed_locked() -> None:
    while state.mem_cache_entries and (
        state.mem_cache_current_bytes > MEMORY_CACHE_MAX_BYTES or len(state.mem_cache_entries) > MEMORY_CACHE_MAX_FILES
    ):
        oldest_file_id = next(iter(state.mem_cache_lru.keys()))
        entry = state.mem_cache_entries.get(oldest_file_id)
        if not entry:
            state.mem_cache_lru.pop(oldest_file_id, None)
            continue
        if entry.dirty:
            break
        state.mem_cache_current_bytes -= entry.size_bytes
        state.mem_cache_entries.pop(oldest_file_id, None)
        state.mem_cache_lru.pop(oldest_file_id, None)
        cache_metric_inc("evicted")


def load_cache_entry_from_node_locked(node) -> FileCacheEntry:
    now_ts = time.time()
    existing = state.mem_cache_entries.get(node.id)
    if existing:
        cache_metric_inc("cache_hit")
        existing.last_access_ts = now_ts
        touch_cache_lru(node.id)
        return existing

    cache_metric_inc("cache_miss")
    content_value = node.content_text or ""
    entry = FileCacheEntry(
        file_id=int(node.id),
        content=content_value,
        version_no=int(node.version_no or 1),
        flushed_version_no=int(node.version_no or 1),
        pending_versions=[],
        dirty=False,
        size_bytes=estimate_text_bytes(content_value),
        last_access_ts=now_ts,
    )
    state.mem_cache_entries[node.id] = entry
    state.mem_cache_current_bytes += entry.size_bytes
    touch_cache_lru(node.id)
    evict_cache_if_needed_locked()
    return entry


def get_node_content(db: Session, node) -> str:
    from iruka_vfs import service

    mirror = service._get_workspace_mirror(int(node.workspace_id), tenant_key=getattr(node, "tenant_id", None))
    if mirror:
        with mirror.lock:
            mirror_node = mirror.nodes.get(int(node.id), node)
            return mirror_node.content_text or ""
    if not MEMORY_CACHE_ENABLED or node.node_type != "file":
        return node.content_text or ""
    with state.mem_cache_lock:
        entry = load_cache_entry_from_node_locked(node)
        return entry.content


def get_node_version(db: Session, node) -> int:
    from iruka_vfs import service

    mirror = service._get_workspace_mirror(int(node.workspace_id), tenant_key=getattr(node, "tenant_id", None))
    if mirror:
        with mirror.lock:
            mirror_node = mirror.nodes.get(int(node.id), node)
            return int(mirror_node.version_no or 1)
    if not MEMORY_CACHE_ENABLED or node.node_type != "file":
        return int(node.version_no or 1)
    with state.mem_cache_lock:
        entry = load_cache_entry_from_node_locked(node)
        return int(entry.version_no or 1)


def update_cache_after_write(node, content: str, *, op: str) -> int:
    now_ts = time.time()
    with state.mem_cache_lock:
        entry = load_cache_entry_from_node_locked(node)
        state.mem_cache_current_bytes -= entry.size_bytes
        base_version = int(entry.version_no)
        next_version = base_version + 1
        entry.content = content
        entry.version_no = next_version
        entry.dirty = True
        entry.last_access_ts = now_ts
        entry.size_bytes = estimate_text_bytes(content)
        entry.pending_versions.append(
            {
                "version_no": next_version,
                "base_version_no": base_version,
                "op": op,
                "content_text": content,
            }
        )
        state.mem_cache_dirty_ids.add(node.id)
        state.mem_cache_current_bytes += entry.size_bytes
        touch_cache_lru(node.id)
        evict_cache_if_needed_locked()
        cache_metric_inc("write_ops")
        return next_version


def mem_cache_flush_worker() -> None:
    if state.mem_cache_session_maker is None:
        return
    while True:
        time.sleep(MEMORY_CACHE_FLUSH_INTERVAL_SECONDS)
        with state.mem_cache_lock:
            dirty_ids = list(state.mem_cache_dirty_ids)[:MEMORY_CACHE_FLUSH_BATCH]
        if not dirty_ids:
            continue

        for file_id in dirty_ids:
            with state.mem_cache_lock:
                entry = state.mem_cache_entries.get(file_id)
                if not entry or not entry.pending_versions:
                    state.mem_cache_dirty_ids.discard(file_id)
                    continue
                pending = [dict(item) for item in entry.pending_versions]
                expected_db_version = int(entry.flushed_version_no)
                final_version = int(pending[-1]["version_no"])
                final_content = str(pending[-1]["content_text"])

            db = state.mem_cache_session_maker()
            try:
                updated = db.execute(
                    text(
                        """
                        UPDATE virtual_file_nodes
                        SET content_text = :content_text,
                            version_no = :new_version_no,
                            updated_at = :updated_at
                        WHERE id = :file_id
                          AND version_no = :expected_version_no
                        """
                    ),
                    {
                        "content_text": final_content,
                        "new_version_no": final_version,
                        "updated_at": datetime.utcnow(),
                        "file_id": file_id,
                        "expected_version_no": expected_db_version,
                    },
                )
                if int(updated.rowcount or 0) != 1:
                    db.rollback()
                    cache_metric_inc("flush_conflict")
                    with state.mem_cache_lock:
                        entry_now = state.mem_cache_entries.get(file_id)
                        if entry_now:
                            entry_now.pending_versions = []
                            entry_now.dirty = False
                            state.mem_cache_dirty_ids.discard(file_id)
                    continue

                db.commit()
                cache_metric_inc("flush_ok")

                with state.mem_cache_lock:
                    entry_now = state.mem_cache_entries.get(file_id)
                    if not entry_now:
                        state.mem_cache_dirty_ids.discard(file_id)
                        continue
                    entry_now.flushed_version_no = max(int(entry_now.flushed_version_no), final_version)
                    entry_now.pending_versions = [
                        item for item in entry_now.pending_versions if int(item.get("version_no") or 0) > final_version
                    ]
                    entry_now.dirty = bool(entry_now.pending_versions)
                    if not entry_now.dirty:
                        state.mem_cache_dirty_ids.discard(file_id)
            except Exception:
                db.rollback()
                cache_metric_inc("flush_error")
            finally:
                db.close()
