from __future__ import annotations

import queue
import threading
from typing import Any

import redis
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from iruka_vfs.constants import ASYNC_COMMAND_LOGGING
from iruka_vfs.dependencies import get_vfs_dependencies
from iruka_vfs.runtime_seed import RuntimeSeed
from iruka_vfs import runtime_state

_dependencies = get_vfs_dependencies()
settings = _dependencies.settings

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


def get_cached_workspace_state(scope_key: str, workspace_id: int) -> dict[str, Any] | None:
    with _workspace_cache_lock:
        item = _workspace_cache.get((scope_key, workspace_id))
        if not item:
            return None
        return dict(item)


def set_cached_workspace_state(scope_key: str, workspace_id: int, payload: dict[str, Any]) -> None:
    with _workspace_cache_lock:
        _workspace_cache[(scope_key, workspace_id)] = dict(payload)


def register_runtime_seed(workspace_id: int, tenant_key: str, runtime_seed: RuntimeSeed) -> None:
    with runtime_state.runtime_seed_lock:
        runtime_state.runtime_seeds[(tenant_key, int(workspace_id))] = runtime_seed


def get_registered_runtime_seed(workspace_id: int, tenant_key: str) -> RuntimeSeed | None:
    with runtime_state.runtime_seed_lock:
        return runtime_state.runtime_seeds.get((tenant_key, int(workspace_id)))


def ensure_async_log_worker(engine: Engine, repositories) -> None:
    global _log_engine, _log_session_maker, _log_worker_started
    if not ASYNC_COMMAND_LOGGING:
        return
    with _log_lock:
        if _log_worker_started:
            return
        _log_engine = engine
        _log_session_maker = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
        worker = threading.Thread(
            target=_virtual_command_log_worker,
            args=(repositories,),
            name="vfs-command-log-worker",
            daemon=True,
        )
        worker.start()
        _log_worker_started = True


def enqueue_virtual_command_log(payload: dict[str, Any]) -> None:
    if not ASYNC_COMMAND_LOGGING or not _log_worker_started:
        return
    try:
        _log_queue.put_nowait(payload)
    except queue.Full:
        return


def _virtual_command_log_worker(repositories) -> None:
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
            repositories.command_log.bulk_insert_command_logs(db, batch)
        except Exception:
            db.rollback()
        finally:
            db.close()


def next_ephemeral_command_id() -> int:
    global _ephemeral_command_id
    with _ephemeral_command_lock:
        _ephemeral_command_id += 1
        return _ephemeral_command_id


def next_ephemeral_patch_id() -> int:
    global _ephemeral_patch_id
    with _ephemeral_patch_lock:
        _ephemeral_patch_id += 1
        return _ephemeral_patch_id


def get_redis_client() -> redis.Redis:
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    with _redis_client_lock:
        if _redis_client is None:
            _redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        return _redis_client
