from __future__ import annotations

from datetime import datetime
import time

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from iruka_vfs.cache.ops import cache_metric_inc
from iruka_vfs.constants import MEMORY_CACHE_ENABLED, MEMORY_CACHE_FLUSH_BATCH, MEMORY_CACHE_FLUSH_INTERVAL_SECONDS
from iruka_vfs import runtime_state as state


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
