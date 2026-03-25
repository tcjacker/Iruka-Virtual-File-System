from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Callable
from typing import TypeVar

from iruka_vfs.models import WorkspaceMirror
from iruka_vfs.service_ops.state import get_workspace_state_store

T = TypeVar("T")
_LOCKED_WORKSPACE_REFS: ContextVar[frozenset[tuple[str, int, str | None]]] = ContextVar(
    "locked_workspace_refs",
    default=frozenset(),
)


@contextmanager
def mark_workspace_lock_held(workspace_ref) -> object:
    ref_key = (workspace_ref.tenant_key, int(workspace_ref.workspace_id), workspace_ref.scope_key)
    current = _LOCKED_WORKSPACE_REFS.get()
    token = _LOCKED_WORKSPACE_REFS.set(current | {ref_key})
    try:
        yield
    finally:
        _LOCKED_WORKSPACE_REFS.reset(token)


def mutate_workspace_mirror(
    workspace_id: int,
    *,
    tenant_key: str | None = None,
    scope_key: str | None = None,
    mutate: Callable[[WorkspaceMirror], tuple[T, bool]],
) -> T | None:
    store = get_workspace_state_store()
    current = store.get_workspace_mirror(
        workspace_id,
        tenant_key=tenant_key,
        scope_key=scope_key,
    )
    if current is None:
        return None
    workspace_ref = store.workspace_ref(mirror=current)
    ref_key = (workspace_ref.tenant_key, int(workspace_ref.workspace_id), workspace_ref.scope_key)
    lock = None
    needs_lock = ref_key not in _LOCKED_WORKSPACE_REFS.get()
    if needs_lock:
        lock = store.workspace_lock(workspace_ref=workspace_ref)
        if not lock.acquire(blocking=True):
            raise TimeoutError(f"failed to acquire workspace lock: {workspace_id}")
    try:
        current = store.load_workspace_mirror(workspace_ref)
        if current is None:
            return None
        with current.lock:
            result, changed = mutate(current)
            if changed:
                store.set_workspace_mirror(current)
            return result
    finally:
        if lock is not None:
            try:
                lock.release()
            except Exception:
                pass


def execute_workspace_mirror_transaction(
    workspace_id: int,
    *,
    tenant_key: str | None = None,
    scope_key: str | None = None,
    execute: Callable[[WorkspaceMirror, object], T],
) -> T | None:
    store = get_workspace_state_store()
    current = store.get_workspace_mirror(
        workspace_id,
        tenant_key=tenant_key,
        scope_key=scope_key,
    )
    if current is None:
        return None
    workspace_ref = store.workspace_ref(mirror=current)
    ref_key = (workspace_ref.tenant_key, int(workspace_ref.workspace_id), workspace_ref.scope_key)
    lock = None
    needs_lock = ref_key not in _LOCKED_WORKSPACE_REFS.get()
    if needs_lock:
        lock = store.workspace_lock(workspace_ref=workspace_ref)
        if not lock.acquire(blocking=True):
            raise TimeoutError(f"failed to acquire workspace lock: {workspace_id}")
    try:
        current = store.load_workspace_mirror(workspace_ref)
        if current is None:
            return None
        with mark_workspace_lock_held(workspace_ref):
            return execute(current, workspace_ref)
    finally:
        if lock is not None:
            try:
                lock.release()
            except Exception:
                pass
