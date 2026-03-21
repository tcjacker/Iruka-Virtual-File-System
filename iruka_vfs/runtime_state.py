from __future__ import annotations

from collections import OrderedDict, deque
import queue
import threading
from typing import Any

import redis
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


workspace_cache_lock = threading.Lock()
workspace_cache: dict[tuple[str, int], dict[str, Any]] = {}
runtime_seed_lock = threading.Lock()
runtime_seeds: dict[tuple[str, int], Any] = {}

log_lock = threading.Lock()
log_engine: Engine | None = None
log_session_maker: sessionmaker | None = None
log_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=5000)
log_worker_started = False

ephemeral_command_lock = threading.Lock()
ephemeral_command_id = 1_000_000_000
ephemeral_patch_lock = threading.Lock()
ephemeral_patch_id = 2_000_000_000

workspace_checkpoint_worker_started = False
workspace_checkpoint_session_maker: sessionmaker | None = None
redis_client_lock = threading.Lock()
redis_client: redis.Redis | None = None
workspace_state_store = None
vfs_repositories = None
active_workspace_context = threading.local()

local_workspace_mirrors: dict[str, Any] = {}
local_workspace_indexes: dict[tuple[str, int, str], str] = {}
local_workspace_locks: dict[str, threading.Lock] = {}
local_checkpoint_condition = threading.Condition()
local_checkpoint_queue = deque()
local_checkpoint_enqueued: set[str] = set()
local_dirty_workspaces: set[str] = set()
local_checkpoint_due_at: dict[str, float] = {}
local_workspace_errors: dict[str, dict[str, object]] = {}
local_dead_letter_workspaces: set[str] = set()
local_dead_letter_payloads: dict[str, dict[str, object]] = {}
local_retry_counts: dict[str, int] = {}

mem_cache_lock = threading.Lock()
mem_cache_entries: dict[int, Any] = {}
mem_cache_lru: OrderedDict[int, None] = OrderedDict()
mem_cache_dirty_ids: set[int] = set()
mem_cache_current_bytes = 0
mem_cache_worker_started = False
mem_cache_session_maker: sessionmaker | None = None
mem_cache_metrics: dict[str, int] = {
    "cache_hit": 0,
    "cache_miss": 0,
    "write_ops": 0,
    "flush_ok": 0,
    "flush_conflict": 0,
    "flush_error": 0,
    "checkpoint_retry": 0,
    "checkpoint_dead_letter": 0,
    "checkpoint_requeue": 0,
    "evicted": 0,
}
