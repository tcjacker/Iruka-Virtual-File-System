from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from types import SimpleNamespace
import threading
import time

from iruka_vfs.dependencies import VFSDependencies, configure_vfs_dependencies


@dataclass
class DummyWorkspace:
    id: int = 1
    tenant_id: str = "test-tenant"
    metadata_json: dict | None = None


@dataclass
class DummyVirtualFileNode:
    id: int
    tenant_id: str = "test-tenant"
    workspace_id: int = 1
    parent_id: int | None = None
    name: str = ""
    node_type: str = "file"
    content_text: str = ""
    version_no: int = 1


@dataclass
class DummyVirtualShellCommand:
    id: int = 1


@dataclass
class DummyVirtualShellSession:
    id: int = 1
    tenant_id: str = "test-tenant"
    workspace_id: int = 1
    cwd_node_id: int = 1
    env_json: dict | None = None
    status: str = "active"


@dataclass
class InMemoryLock:
    _lock: threading.Lock

    def acquire(self, blocking: bool = True, blocking_timeout: float | None = None) -> bool:
        timeout = -1 if blocking_timeout is None else blocking_timeout
        return self._lock.acquire(blocking=blocking, timeout=timeout)

    def release(self) -> None:
        if self._lock.locked():
            self._lock.release()


@dataclass
class InMemoryRedis:
    store: dict[str, object] | None = None
    sets: dict[str, set[str]] | None = None
    queues: dict[str, deque[str]] | None = None
    locks: dict[str, threading.Lock] | None = None
    condition: threading.Condition | None = None

    def __post_init__(self) -> None:
        if self.store is None:
            self.store = {}
        if self.sets is None:
            self.sets = {}
        if self.queues is None:
            self.queues = {}
        if self.locks is None:
            self.locks = {}
        if self.condition is None:
            self.condition = threading.Condition()

    def get(self, key: str):
        return self.store.get(key)

    def set(self, key: str, value) -> None:
        self.store[key] = value

    def delete(self, key: str) -> None:
        self.store.pop(key, None)
        self.sets.pop(key, None)

    def sadd(self, key: str, value: str) -> int:
        bucket = self.sets.setdefault(key, set())
        before = len(bucket)
        bucket.add(value)
        return 1 if len(bucket) != before else 0

    def srem(self, key: str, value: str) -> int:
        bucket = self.sets.setdefault(key, set())
        if value in bucket:
            bucket.remove(value)
            return 1
        return 0

    def scard(self, key: str) -> int:
        return len(self.sets.get(key, set()))

    def rpush(self, key: str, value: str) -> None:
        with self.condition:
            self.queues.setdefault(key, deque()).append(value)
            self.condition.notify_all()

    def blpop(self, key: str, timeout: int = 1) -> tuple[str, str] | None:
        deadline = time.time() + timeout
        with self.condition:
            while True:
                queue = self.queues.setdefault(key, deque())
                if queue:
                    return key, queue.popleft()
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                self.condition.wait(timeout=remaining)

    def llen(self, key: str) -> int:
        return len(self.queues.get(key, deque()))

    def incr(self, key: str) -> int:
        value = int(self.store.get(key) or 0) + 1
        self.store[key] = value
        return value

    def lock(self, key: str, timeout: int = 30, blocking_timeout: int = 5) -> InMemoryLock:
        return InMemoryLock(self.locks.setdefault(key, threading.Lock()))


def configure_test_dependencies(*, runtime_profile: str = "persistent") -> None:
    configure_vfs_dependencies(
        VFSDependencies(
            settings=SimpleNamespace(
                default_tenant_id="test-tenant",
                redis_key_namespace="test",
                redis_url="redis://localhost:6379/0",
                database_url="sqlite://",
            ),
            AgentWorkspace=DummyWorkspace,
            VirtualFileNode=DummyVirtualFileNode,
            VirtualShellCommand=DummyVirtualShellCommand,
            VirtualShellSession=DummyVirtualShellSession,
            load_project_state_payload=lambda *args, **kwargs: {},
            repositories=None,
            runtime_profile=runtime_profile,
        )
    )
