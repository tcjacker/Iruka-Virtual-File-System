from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Protocol

from iruka_vfs.models import WorkspaceMirror
from iruka_vfs.workspace_state_serialization import (
    deserialize_workspace_mirror,
    serialize_workspace_mirror_meta,
    serialize_workspace_nodes,
)


class WorkspaceLock(Protocol):
    def acquire(self, blocking: bool = True) -> bool: ...

    def release(self) -> None: ...


@dataclass(frozen=True)
class WorkspaceStateRef:
    tenant_key: str
    workspace_id: int
    scope_key: str


class WorkspaceStateStore(Protocol):
    def workspace_ref(
        self,
        *,
        mirror: WorkspaceMirror | None = None,
        workspace_id: int | None = None,
        tenant_key: str | None = None,
        scope_key: str | None = None,
    ) -> WorkspaceStateRef: ...

    def get_workspace_mirror(
        self,
        workspace_id: int,
        *,
        tenant_key: str | None = None,
        scope_key: str | None = None,
    ) -> WorkspaceMirror | None: ...

    def load_workspace_mirror(self, workspace_ref: WorkspaceStateRef) -> WorkspaceMirror | None: ...

    def set_workspace_mirror(self, mirror: WorkspaceMirror) -> WorkspaceStateRef: ...

    def delete_workspace_mirror(
        self,
        workspace_id: int,
        *,
        tenant_key: str | None = None,
        scope_key: str | None = None,
    ) -> None: ...

    def workspace_lock(
        self,
        mirror: WorkspaceMirror | None = None,
        *,
        workspace_ref: WorkspaceStateRef | None = None,
        workspace_id: int | None = None,
        tenant_key: str | None = None,
        scope_key: str | None = None,
    ) -> WorkspaceLock: ...

    def enqueue_workspace_checkpoint(
        self,
        workspace_ref: WorkspaceStateRef,
        *,
        due_at: float | None = None,
        force: bool = False,
    ) -> None: ...

    def pop_checkpoint(self, timeout_seconds: int) -> WorkspaceStateRef | None: ...

    def requeue_checkpoint(self, workspace_ref: WorkspaceStateRef) -> None: ...

    def clear_checkpoint_schedule(self, workspace_ref: WorkspaceStateRef) -> None: ...

    def get_checkpoint_due_at(self, workspace_ref: WorkspaceStateRef) -> float | None: ...

    def get_checkpoint_metrics(self) -> dict[str, int]: ...

    def get_dirty_workspace_count(self) -> int: ...

    def mark_workspace_dirty(self, workspace_ref: WorkspaceStateRef) -> None: ...

    def clear_workspace_dirty(self, workspace_ref: WorkspaceStateRef) -> None: ...

    def get_error_payload(self, workspace_ref: WorkspaceStateRef) -> dict[str, object] | None: ...

    def set_error_payload(self, workspace_ref: WorkspaceStateRef, payload: dict[str, object]) -> None: ...

    def clear_error_payload(self, workspace_ref: WorkspaceStateRef) -> None: ...

    def get_dead_letter_payload(self, workspace_ref: WorkspaceStateRef) -> dict[str, object] | None: ...

    def set_dead_letter_payload(self, workspace_ref: WorkspaceStateRef, payload: dict[str, object]) -> None: ...

    def clear_dead_letter_payload(self, workspace_ref: WorkspaceStateRef) -> None: ...

    def add_dead_letter(self, workspace_ref: WorkspaceStateRef) -> None: ...

    def remove_dead_letter(self, workspace_ref: WorkspaceStateRef) -> None: ...

    def get_dead_letter_count(self) -> int: ...

    def increment_retry_count(self, workspace_ref: WorkspaceStateRef) -> int: ...

    def clear_retry_count(self, workspace_ref: WorkspaceStateRef) -> None: ...


def _workspace_base_key(tenant_key: str, workspace_id: int, scope_key: str | None = None) -> str:
    from iruka_vfs.mirror.keys import workspace_base_key

    return workspace_base_key(tenant_key, workspace_id, scope_key)


def _workspace_index_key(tenant_key: str, workspace_id: int, scope_key: str | None = None) -> str:
    from iruka_vfs.mirror.keys import workspace_index_key

    return workspace_index_key(tenant_key, workspace_id, scope_key)


def _mirror_has_dirty_state(mirror: WorkspaceMirror) -> bool:
    from iruka_vfs import workspace_mirror as mirror_api

    return mirror_api.mirror_has_dirty_state(mirror)


def _serialize_nodes_payload(mirror: WorkspaceMirror, dirty_node_ids: set[int]) -> str:
    dirty_nodes = {
        int(node_id): mirror.nodes[int(node_id)]
        for node_id in dirty_node_ids
        if int(node_id) in mirror.nodes
    }
    return serialize_workspace_nodes(dirty_nodes)


def _queue_token(workspace_ref: WorkspaceStateRef) -> str:
    return f"{workspace_ref.tenant_key}|{workspace_ref.workspace_id}|{workspace_ref.scope_key}"


def _workspace_ref_from_token(token: str) -> WorkspaceStateRef | None:
    raw_value = str(token or "")
    if not raw_value:
        return None
    parts = raw_value.split("|", 2)
    if len(parts) != 3:
        return None
    try:
        return WorkspaceStateRef(tenant_key=parts[0], workspace_id=int(parts[1]), scope_key=parts[2])
    except ValueError:
        return None


class RedisWorkspaceStateStore:
    def __init__(self, *, redis_client_factory) -> None:
        self._redis_client_factory = redis_client_factory

    def _client(self):
        return self._redis_client_factory()

    def workspace_ref(
        self,
        *,
        mirror: WorkspaceMirror | None = None,
        workspace_id: int | None = None,
        tenant_key: str | None = None,
        scope_key: str | None = None,
    ) -> WorkspaceStateRef:
        from iruka_vfs import workspace_mirror as mirror_api

        if mirror is not None:
            return WorkspaceStateRef(
                tenant_key=str(mirror.tenant_key),
                workspace_id=int(mirror.workspace_id),
                scope_key=str(mirror.scope_key),
            )
        if workspace_id is None:
            raise ValueError("workspace_id is required when mirror is not provided")
        return WorkspaceStateRef(
            tenant_key=mirror_api.effective_tenant_key(tenant_key),
            workspace_id=int(workspace_id),
            scope_key=mirror_api.effective_workspace_scope(scope_key),
        )

    def _base_key(self, workspace_ref: WorkspaceStateRef) -> str:
        return _workspace_base_key(workspace_ref.tenant_key, workspace_ref.workspace_id, workspace_ref.scope_key)

    def get_workspace_mirror(
        self,
        workspace_id: int,
        *,
        tenant_key: str | None = None,
        scope_key: str | None = None,
    ) -> WorkspaceMirror | None:
        from iruka_vfs import workspace_mirror as mirror_api

        resolved_tenant_key = mirror_api.effective_tenant_key(tenant_key)
        resolved_scope_key = mirror_api.effective_workspace_scope(scope_key)
        client = self._client()
        base_key = client.get(_workspace_index_key(resolved_tenant_key, workspace_id, resolved_scope_key))
        if not base_key and scope_key is None:
            from iruka_vfs.mirror.keys import workspace_latest_index_key

            base_key = client.get(workspace_latest_index_key(resolved_tenant_key, workspace_id))
        if not base_key:
            return None
        return self.load_workspace_mirror(
            WorkspaceStateRef(
                tenant_key=resolved_tenant_key,
                workspace_id=int(workspace_id),
                scope_key=resolved_scope_key,
            )
        )

    def load_workspace_mirror(self, workspace_ref: WorkspaceStateRef) -> WorkspaceMirror | None:
        from iruka_vfs.mirror.keys import workspace_mirror_dirty_nodes_key, workspace_mirror_key, workspace_mirror_nodes_key

        client = self._client()
        base_key = self._base_key(workspace_ref)
        raw_value = client.get(workspace_mirror_key(base_key))
        if not raw_value:
            return None
        return deserialize_workspace_mirror(
            raw_value,
            raw_nodes_value=client.get(workspace_mirror_nodes_key(base_key)),
            raw_dirty_nodes_value=client.get(workspace_mirror_dirty_nodes_key(base_key)),
        )

    def set_workspace_mirror(self, mirror: WorkspaceMirror) -> WorkspaceStateRef:
        from iruka_vfs.mirror.keys import workspace_mirror_dirty_nodes_key, workspace_mirror_key, workspace_mirror_nodes_key
        from iruka_vfs.mirror.keys import workspace_latest_index_key

        client = self._client()
        workspace_ref = self.workspace_ref(mirror=mirror)
        base_key = self._base_key(workspace_ref)
        client.set(_workspace_index_key(workspace_ref.tenant_key, workspace_ref.workspace_id, workspace_ref.scope_key), base_key)
        client.set(workspace_latest_index_key(workspace_ref.tenant_key, workspace_ref.workspace_id), base_key)
        dirty_node_ids = mirror.dirty_content_node_ids | mirror.dirty_structure_node_ids
        use_full_snapshot = (
            not dirty_node_ids
            or bool(mirror.dirty_structure_node_ids)
            or any(int(node_id) <= 0 for node_id in dirty_node_ids)
            or not client.get(workspace_mirror_nodes_key(base_key))
        )
        client.set(workspace_mirror_key(base_key), serialize_workspace_mirror_meta(mirror))
        if use_full_snapshot:
            client.set(workspace_mirror_nodes_key(base_key), serialize_workspace_nodes(mirror.nodes))
            client.delete(workspace_mirror_dirty_nodes_key(base_key))
        elif dirty_node_ids:
            client.set(
                workspace_mirror_dirty_nodes_key(base_key),
                _serialize_nodes_payload(mirror, dirty_node_ids),
            )
        if _mirror_has_dirty_state(mirror):
            self.mark_workspace_dirty(workspace_ref)
            self.enqueue_workspace_checkpoint(workspace_ref)
        else:
            self.clear_workspace_dirty(workspace_ref)
        return workspace_ref

    def delete_workspace_mirror(
        self,
        workspace_id: int,
        *,
        tenant_key: str | None = None,
        scope_key: str | None = None,
    ) -> None:
        from iruka_vfs.mirror.keys import (
            workspace_latest_index_key,
            workspace_mirror_dirty_nodes_key,
            workspace_mirror_key,
            workspace_mirror_nodes_key,
        )

        from iruka_vfs import workspace_mirror as mirror_api
        resolved_tenant_key = mirror_api.effective_tenant_key(tenant_key)
        resolved_scope_key = mirror_api.effective_workspace_scope(scope_key)
        client = self._client()
        workspace_ref = WorkspaceStateRef(
            tenant_key=resolved_tenant_key,
            workspace_id=int(workspace_id),
            scope_key=resolved_scope_key,
        )
        base_key = client.get(_workspace_index_key(resolved_tenant_key, workspace_id, resolved_scope_key))
        if not base_key:
            return
        base_key = str(base_key)
        client.delete(workspace_mirror_key(base_key))
        client.delete(workspace_mirror_nodes_key(base_key))
        client.delete(workspace_mirror_dirty_nodes_key(base_key))
        self.clear_error_payload(workspace_ref)
        self.clear_checkpoint_schedule(workspace_ref)
        client.delete(_workspace_index_key(resolved_tenant_key, workspace_id, resolved_scope_key))
        latest_key = workspace_latest_index_key(resolved_tenant_key, workspace_id)
        if str(client.get(latest_key) or "") == base_key:
            client.delete(latest_key)
        self.clear_workspace_dirty(workspace_ref)

    def workspace_lock(
        self,
        mirror: WorkspaceMirror | None = None,
        *,
        workspace_ref: WorkspaceStateRef | None = None,
        workspace_id: int | None = None,
        tenant_key: str | None = None,
        scope_key: str | None = None,
    ) -> WorkspaceLock:
        from iruka_vfs.mirror.keys import workspace_lock_key
        from iruka_vfs import workspace_mirror as mirror_api

        client = self._client()
        resolved_ref = workspace_ref or self.workspace_ref(
            mirror=mirror,
            workspace_id=workspace_id,
            tenant_key=tenant_key,
            scope_key=scope_key,
        )
        base_key = str(client.get(_workspace_index_key(resolved_ref.tenant_key, resolved_ref.workspace_id, resolved_ref.scope_key)) or "")
        if not base_key:
            base_key = self._base_key(resolved_ref)
        return client.lock(workspace_lock_key(base_key), timeout=30, blocking_timeout=5)

    def enqueue_workspace_checkpoint(
        self,
        workspace_ref: WorkspaceStateRef,
        *,
        due_at: float | None = None,
        force: bool = False,
    ) -> None:
        from iruka_vfs.mirror.keys import workspace_due_key, workspace_enqueued_key, workspace_queue_key

        client = self._client()
        base_key = self._base_key(workspace_ref)
        if not force and self.get_dead_letter_payload(workspace_ref):
            return
        scheduled_at = due_at if due_at is not None else time.time()
        client.set(workspace_due_key(base_key), str(scheduled_at))
        if int(client.sadd(workspace_enqueued_key(), base_key) or 0) == 1:
            client.rpush(workspace_queue_key(), _queue_token(workspace_ref))

    def pop_checkpoint(self, timeout_seconds: int) -> WorkspaceStateRef | None:
        from iruka_vfs.mirror.keys import workspace_queue_key

        item = self._client().blpop(workspace_queue_key(), timeout=max(int(timeout_seconds), 1))
        if not item:
            return None
        _, token = item
        return _workspace_ref_from_token(str(token))

    def requeue_checkpoint(self, workspace_ref: WorkspaceStateRef) -> None:
        from iruka_vfs.mirror.keys import workspace_queue_key

        self._client().rpush(workspace_queue_key(), _queue_token(workspace_ref))

    def clear_checkpoint_schedule(self, workspace_ref: WorkspaceStateRef) -> None:
        from iruka_vfs.mirror.keys import workspace_due_key, workspace_enqueued_key

        base_key = self._base_key(workspace_ref)
        client = self._client()
        client.srem(workspace_enqueued_key(), base_key)
        client.delete(workspace_due_key(base_key))

    def get_checkpoint_due_at(self, workspace_ref: WorkspaceStateRef) -> float | None:
        from iruka_vfs.mirror.keys import workspace_due_key

        base_key = self._base_key(workspace_ref)
        raw_value = self._client().get(workspace_due_key(base_key))
        if raw_value is None:
            return None
        try:
            return float(raw_value)
        except (TypeError, ValueError):
            return 0.0

    def get_checkpoint_metrics(self) -> dict[str, int]:
        from iruka_vfs.mirror.keys import (
            workspace_dead_letter_set_key,
            workspace_dirty_set_key,
            workspace_enqueued_key,
            workspace_queue_key,
        )

        client = self._client()
        queue_depth = 0
        enqueued = 0
        dirty = 0
        dead_letter = 0
        try:
            if hasattr(client, "llen"):
                queue_depth = int(client.llen(workspace_queue_key()) or 0)
            elif hasattr(client, "queues"):
                queue_depth = len(client.queues.get(workspace_queue_key(), []))
            if hasattr(client, "scard"):
                enqueued = int(client.scard(workspace_enqueued_key()) or 0)
                dirty = int(client.scard(workspace_dirty_set_key()) or 0)
                dead_letter = int(client.scard(workspace_dead_letter_set_key()) or 0)
            elif hasattr(client, "sets"):
                enqueued = len(client.sets.get(workspace_enqueued_key(), set()))
                dirty = len(client.sets.get(workspace_dirty_set_key(), set()))
                dead_letter = len(client.sets.get(workspace_dead_letter_set_key(), set()))
        except Exception:
            pass
        return {
            "checkpoint_queue_depth": int(queue_depth),
            "checkpoint_enqueued": int(enqueued),
            "checkpoint_dirty_workspaces": int(dirty),
            "checkpoint_dead_letter": int(dead_letter),
        }

    def get_dirty_workspace_count(self) -> int:
        from iruka_vfs.mirror.keys import workspace_dirty_set_key

        try:
            return int(self._client().scard(workspace_dirty_set_key()) or 0)
        except Exception:
            return 0

    def mark_workspace_dirty(self, workspace_ref: WorkspaceStateRef) -> None:
        from iruka_vfs.mirror.keys import workspace_dirty_set_key

        base_key = self._base_key(workspace_ref)
        self._client().sadd(workspace_dirty_set_key(), base_key)

    def clear_workspace_dirty(self, workspace_ref: WorkspaceStateRef) -> None:
        from iruka_vfs.mirror.keys import workspace_dirty_set_key

        base_key = self._base_key(workspace_ref)
        self._client().srem(workspace_dirty_set_key(), base_key)

    def get_error_payload(self, workspace_ref: WorkspaceStateRef) -> dict[str, object] | None:
        from iruka_vfs.mirror.keys import workspace_error_key

        base_key = self._base_key(workspace_ref)
        raw_value = self._client().get(workspace_error_key(base_key))
        if not raw_value:
            return None
        return dict(json.loads(raw_value))

    def set_error_payload(self, workspace_ref: WorkspaceStateRef, payload: dict[str, object]) -> None:
        from iruka_vfs.mirror.keys import workspace_error_key

        base_key = self._base_key(workspace_ref)
        self._client().set(workspace_error_key(base_key), json.dumps(payload, ensure_ascii=False))

    def clear_error_payload(self, workspace_ref: WorkspaceStateRef) -> None:
        from iruka_vfs.mirror.keys import workspace_error_key

        base_key = self._base_key(workspace_ref)
        self._client().delete(workspace_error_key(base_key))

    def get_dead_letter_payload(self, workspace_ref: WorkspaceStateRef) -> dict[str, object] | None:
        from iruka_vfs.mirror.keys import workspace_dead_letter_payload_key

        base_key = self._base_key(workspace_ref)
        raw_value = self._client().get(workspace_dead_letter_payload_key(base_key))
        if not raw_value:
            return None
        return dict(json.loads(raw_value))

    def set_dead_letter_payload(self, workspace_ref: WorkspaceStateRef, payload: dict[str, object]) -> None:
        from iruka_vfs.mirror.keys import workspace_dead_letter_payload_key

        base_key = self._base_key(workspace_ref)
        self._client().set(workspace_dead_letter_payload_key(base_key), json.dumps(payload, ensure_ascii=False))

    def clear_dead_letter_payload(self, workspace_ref: WorkspaceStateRef) -> None:
        from iruka_vfs.mirror.keys import workspace_dead_letter_payload_key

        base_key = self._base_key(workspace_ref)
        self._client().delete(workspace_dead_letter_payload_key(base_key))

    def add_dead_letter(self, workspace_ref: WorkspaceStateRef) -> None:
        from iruka_vfs.mirror.keys import workspace_dead_letter_set_key

        base_key = self._base_key(workspace_ref)
        self._client().sadd(workspace_dead_letter_set_key(), base_key)

    def remove_dead_letter(self, workspace_ref: WorkspaceStateRef) -> None:
        from iruka_vfs.mirror.keys import workspace_dead_letter_set_key

        base_key = self._base_key(workspace_ref)
        self._client().srem(workspace_dead_letter_set_key(), base_key)

    def get_dead_letter_count(self) -> int:
        from iruka_vfs.mirror.keys import workspace_dead_letter_set_key

        try:
            return int(self._client().scard(workspace_dead_letter_set_key()) or 0)
        except Exception:
            return 0

    def increment_retry_count(self, workspace_ref: WorkspaceStateRef) -> int:
        from iruka_vfs.mirror.keys import workspace_retry_count_key

        client = self._client()
        base_key = self._base_key(workspace_ref)
        retry_key = workspace_retry_count_key(base_key)
        return int(client.incr(retry_key) or 0) if hasattr(client, "incr") else int(client.get(retry_key) or 0) + 1

    def clear_retry_count(self, workspace_ref: WorkspaceStateRef) -> None:
        from iruka_vfs.mirror.keys import workspace_retry_count_key

        base_key = self._base_key(workspace_ref)
        self._client().delete(workspace_retry_count_key(base_key))

@dataclass
class _LocalCheckpointState:
    queue: deque[str]
    enqueued: set[str]
    dirty: set[str]
    due_at: dict[str, float]
    errors: dict[str, dict[str, object]]
    dead_letters: set[str]
    dead_letter_payloads: dict[str, dict[str, object]]
    retry_counts: dict[str, int]
    condition: threading.Condition


class _ThreadLockAdapter:
    def __init__(self, lock: threading.Lock | threading.RLock) -> None:
        self._lock = lock

    def acquire(self, blocking: bool = True) -> bool:
        return self._lock.acquire(blocking=blocking)

    def release(self) -> None:
        self._lock.release()


class LocalMemoryStateStore:
    def __init__(self, *, state) -> None:
        self._state = state

    def workspace_ref(
        self,
        *,
        mirror: WorkspaceMirror | None = None,
        workspace_id: int | None = None,
        tenant_key: str | None = None,
        scope_key: str | None = None,
    ) -> WorkspaceStateRef:
        from iruka_vfs import workspace_mirror as mirror_api

        if mirror is not None:
            return WorkspaceStateRef(str(mirror.tenant_key), int(mirror.workspace_id), str(mirror.scope_key))
        if workspace_id is None:
            raise ValueError("workspace_id is required when mirror is not provided")
        return WorkspaceStateRef(
            tenant_key=mirror_api.effective_tenant_key(tenant_key),
            workspace_id=int(workspace_id),
            scope_key=mirror_api.effective_workspace_scope(scope_key),
        )

    def _base_key(self, workspace_ref: WorkspaceStateRef) -> str:
        return _workspace_base_key(workspace_ref.tenant_key, workspace_ref.workspace_id, workspace_ref.scope_key)

    def get_workspace_mirror(
        self,
        workspace_id: int,
        *,
        tenant_key: str | None = None,
        scope_key: str | None = None,
    ) -> WorkspaceMirror | None:
        from iruka_vfs import workspace_mirror as mirror_api

        active = mirror_api.active_workspace_mirror(workspace_id)
        if active:
            return active
        resolved_tenant_key = mirror_api.effective_tenant_key(tenant_key)
        resolved_scope_key = mirror_api.effective_workspace_scope(scope_key)
        base_key = self._state.local_workspace_indexes.get((resolved_tenant_key, int(workspace_id), resolved_scope_key))
        if not base_key and scope_key is None:
            base_key = self._state.local_workspace_indexes.get((resolved_tenant_key, int(workspace_id), None))
        if not base_key:
            return None
        return self._state.local_workspace_mirrors.get(base_key)

    def load_workspace_mirror(self, workspace_ref: WorkspaceStateRef) -> WorkspaceMirror | None:
        return self._state.local_workspace_mirrors.get(self._base_key(workspace_ref))

    def set_workspace_mirror(self, mirror: WorkspaceMirror) -> WorkspaceStateRef:
        workspace_ref = self.workspace_ref(mirror=mirror)
        base_key = self._base_key(workspace_ref)
        self._state.local_workspace_mirrors[base_key] = mirror
        self._state.local_workspace_indexes[(workspace_ref.tenant_key, workspace_ref.workspace_id, workspace_ref.scope_key)] = base_key
        self._state.local_workspace_indexes[(workspace_ref.tenant_key, workspace_ref.workspace_id, None)] = base_key
        if _mirror_has_dirty_state(mirror):
            self.mark_workspace_dirty(workspace_ref)
            self.enqueue_workspace_checkpoint(workspace_ref)
        else:
            self.clear_workspace_dirty(workspace_ref)
        return workspace_ref

    def delete_workspace_mirror(
        self,
        workspace_id: int,
        *,
        tenant_key: str | None = None,
        scope_key: str | None = None,
    ) -> None:
        from iruka_vfs import workspace_mirror as mirror_api

        resolved_tenant_key = mirror_api.effective_tenant_key(tenant_key)
        resolved_scope_key = mirror_api.effective_workspace_scope(scope_key)
        index_key = (resolved_tenant_key, int(workspace_id), resolved_scope_key)
        base_key = self._state.local_workspace_indexes.pop(index_key, None)
        if not base_key:
            return
        self._state.local_workspace_mirrors.pop(base_key, None)
        workspace_ref = WorkspaceStateRef(resolved_tenant_key, int(workspace_id), resolved_scope_key)
        if self._state.local_workspace_indexes.get((resolved_tenant_key, int(workspace_id), None)) == base_key:
            self._state.local_workspace_indexes.pop((resolved_tenant_key, int(workspace_id), None), None)
        self.clear_error_payload(workspace_ref)
        self.clear_dead_letter_payload(workspace_ref)
        self.clear_retry_count(workspace_ref)
        self.clear_checkpoint_schedule(workspace_ref)
        self.clear_workspace_dirty(workspace_ref)
        self._state.local_workspace_locks.pop(base_key, None)

    def workspace_lock(
        self,
        mirror: WorkspaceMirror | None = None,
        *,
        workspace_ref: WorkspaceStateRef | None = None,
        workspace_id: int | None = None,
        tenant_key: str | None = None,
        scope_key: str | None = None,
    ) -> WorkspaceLock:
        resolved_ref = workspace_ref or self.workspace_ref(
            mirror=mirror,
            workspace_id=workspace_id,
            tenant_key=tenant_key,
            scope_key=scope_key,
        )
        base_key = self._state.local_workspace_indexes.get(
            (resolved_ref.tenant_key, resolved_ref.workspace_id, resolved_ref.scope_key)
        ) or self._base_key(resolved_ref)
        lock = self._state.local_workspace_locks.setdefault(base_key, threading.Lock())
        return _ThreadLockAdapter(lock)

    def enqueue_workspace_checkpoint(
        self,
        workspace_ref: WorkspaceStateRef,
        *,
        due_at: float | None = None,
        force: bool = False,
    ) -> None:
        checkpoint = self._state.local_checkpoint_state
        base_key = _queue_token(workspace_ref)
        with checkpoint.condition:
            if not force and base_key in checkpoint.dead_letter_payloads:
                return
            checkpoint.due_at[base_key] = due_at if due_at is not None else time.time()
            if base_key not in checkpoint.enqueued:
                checkpoint.enqueued.add(base_key)
                checkpoint.queue.append(base_key)
                checkpoint.condition.notify_all()

    def pop_checkpoint(self, timeout_seconds: int) -> WorkspaceStateRef | None:
        checkpoint = self._state.local_checkpoint_state
        deadline = time.time() + max(int(timeout_seconds), 1)
        with checkpoint.condition:
            while True:
                if checkpoint.queue:
                    token = checkpoint.queue.popleft()
                    return _workspace_ref_from_token(token)
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                checkpoint.condition.wait(timeout=remaining)

    def requeue_checkpoint(self, workspace_ref: WorkspaceStateRef) -> None:
        checkpoint = self._state.local_checkpoint_state
        with checkpoint.condition:
            checkpoint.queue.append(_queue_token(workspace_ref))
            checkpoint.condition.notify_all()

    def clear_checkpoint_schedule(self, workspace_ref: WorkspaceStateRef) -> None:
        checkpoint = self._state.local_checkpoint_state
        base_key = _queue_token(workspace_ref)
        with checkpoint.condition:
            checkpoint.enqueued.discard(base_key)
            checkpoint.due_at.pop(base_key, None)

    def get_checkpoint_due_at(self, workspace_ref: WorkspaceStateRef) -> float | None:
        return self._state.local_checkpoint_state.due_at.get(_queue_token(workspace_ref))

    def get_checkpoint_metrics(self) -> dict[str, int]:
        checkpoint = self._state.local_checkpoint_state
        with checkpoint.condition:
            return {
                "checkpoint_queue_depth": len(checkpoint.queue),
                "checkpoint_enqueued": len(checkpoint.enqueued),
                "checkpoint_dirty_workspaces": len(checkpoint.dirty),
                "checkpoint_dead_letter": len(checkpoint.dead_letters),
            }

    def get_dirty_workspace_count(self) -> int:
        return len(self._state.local_checkpoint_state.dirty)

    def mark_workspace_dirty(self, workspace_ref: WorkspaceStateRef) -> None:
        self._state.local_checkpoint_state.dirty.add(_queue_token(workspace_ref))

    def clear_workspace_dirty(self, workspace_ref: WorkspaceStateRef) -> None:
        self._state.local_checkpoint_state.dirty.discard(_queue_token(workspace_ref))

    def get_error_payload(self, workspace_ref: WorkspaceStateRef) -> dict[str, object] | None:
        return self._state.local_checkpoint_state.errors.get(_queue_token(workspace_ref))

    def set_error_payload(self, workspace_ref: WorkspaceStateRef, payload: dict[str, object]) -> None:
        self._state.local_checkpoint_state.errors[_queue_token(workspace_ref)] = dict(payload)

    def clear_error_payload(self, workspace_ref: WorkspaceStateRef) -> None:
        self._state.local_checkpoint_state.errors.pop(_queue_token(workspace_ref), None)

    def get_dead_letter_payload(self, workspace_ref: WorkspaceStateRef) -> dict[str, object] | None:
        return self._state.local_checkpoint_state.dead_letter_payloads.get(_queue_token(workspace_ref))

    def set_dead_letter_payload(self, workspace_ref: WorkspaceStateRef, payload: dict[str, object]) -> None:
        self._state.local_checkpoint_state.dead_letter_payloads[_queue_token(workspace_ref)] = dict(payload)

    def clear_dead_letter_payload(self, workspace_ref: WorkspaceStateRef) -> None:
        checkpoint = self._state.local_checkpoint_state
        base_key = _queue_token(workspace_ref)
        checkpoint.dead_letter_payloads.pop(base_key, None)
        checkpoint.dead_letters.discard(base_key)

    def add_dead_letter(self, workspace_ref: WorkspaceStateRef) -> None:
        self._state.local_checkpoint_state.dead_letters.add(_queue_token(workspace_ref))

    def remove_dead_letter(self, workspace_ref: WorkspaceStateRef) -> None:
        self._state.local_checkpoint_state.dead_letters.discard(_queue_token(workspace_ref))

    def get_dead_letter_count(self) -> int:
        return len(self._state.local_checkpoint_state.dead_letters)

    def increment_retry_count(self, workspace_ref: WorkspaceStateRef) -> int:
        checkpoint = self._state.local_checkpoint_state
        base_key = _queue_token(workspace_ref)
        next_value = int(checkpoint.retry_counts.get(base_key) or 0) + 1
        checkpoint.retry_counts[base_key] = next_value
        return next_value

    def clear_retry_count(self, workspace_ref: WorkspaceStateRef) -> None:
        self._state.local_checkpoint_state.retry_counts.pop(_queue_token(workspace_ref), None)
