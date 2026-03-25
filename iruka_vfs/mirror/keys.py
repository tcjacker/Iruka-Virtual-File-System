from __future__ import annotations

from iruka_vfs.mirror.context import effective_workspace_scope


def _settings():
    from iruka_vfs.dependencies import get_vfs_dependencies

    return get_vfs_dependencies().settings


def workspace_base_key(tenant_key: str, workspace_id: int, scope_key: str | None = None) -> str:
    settings = _settings()
    resolved_scope_key = effective_workspace_scope(scope_key)
    return f"{settings.redis_key_namespace}:vfs:scope:{resolved_scope_key}:tenant:{tenant_key}:workspace:{workspace_id}"


def workspace_index_key(tenant_key: str, workspace_id: int, scope_key: str | None = None) -> str:
    settings = _settings()
    resolved_scope_key = effective_workspace_scope(scope_key)
    return f"{settings.redis_key_namespace}:vfs:scope:{resolved_scope_key}:tenant:{tenant_key}:workspace-index:{workspace_id}"


def workspace_latest_index_key(tenant_key: str, workspace_id: int) -> str:
    settings = _settings()
    return f"{settings.redis_key_namespace}:vfs:tenant:{tenant_key}:workspace-index-latest:{workspace_id}"


def workspace_dirty_set_key() -> str:
    settings = _settings()
    return f"{settings.redis_key_namespace}:vfs:dirty-workspaces"


def workspace_lock_key(base_key: str) -> str:
    return f"{base_key}:lock"


def workspace_mirror_key(base_key: str) -> str:
    return f"{base_key}:mirror"


def workspace_mirror_nodes_key(base_key: str) -> str:
    return f"{base_key}:mirror-nodes"


def workspace_mirror_dirty_nodes_key(base_key: str) -> str:
    return f"{base_key}:mirror-dirty-nodes"


def workspace_error_key(base_key: str) -> str:
    return f"{base_key}:checkpoint-error"


def workspace_queue_key() -> str:
    settings = _settings()
    return f"{settings.redis_key_namespace}:vfs:checkpoint-queue"


def workspace_enqueued_key() -> str:
    settings = _settings()
    return f"{settings.redis_key_namespace}:vfs:checkpoint-enqueued"


def workspace_due_key(base_key: str) -> str:
    return f"{base_key}:checkpoint-due-at"


def workspace_retry_count_key(base_key: str) -> str:
    return f"{base_key}:checkpoint-retry-count"


def workspace_dead_letter_set_key() -> str:
    settings = _settings()
    return f"{settings.redis_key_namespace}:vfs:checkpoint-dead-letter"


def workspace_dead_letter_payload_key(base_key: str) -> str:
    return f"{base_key}:checkpoint-dead-letter"
