from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.orm import Session


class WorkspaceRepository(Protocol):
    def get_workspace(self, db: Session, workspace_id: int, tenant_key: str) -> Any | None: ...

    def update_workspace_metadata(
        self,
        db: Session,
        *,
        workspace_id: int,
        tenant_key: str,
        metadata_json: dict[str, Any],
    ) -> bool: ...


class SessionRepository(Protocol):
    def get_active_session(self, db: Session, workspace_id: int, tenant_key: str) -> Any | None: ...

    def create_session(
        self,
        db: Session,
        *,
        tenant_key: str,
        workspace_id: int,
        cwd_node_id: int,
        env_json: dict[str, Any],
        status: str,
    ) -> Any: ...

    def update_session_cwd(
        self,
        db: Session,
        *,
        session_id: int,
        tenant_key: str,
        cwd_node_id: int,
    ) -> None: ...


class NodeRepository(Protocol):
    def get_root(self, db: Session, workspace_id: int, tenant_key: str) -> Any | None: ...

    def get_child(
        self,
        db: Session,
        *,
        tenant_key: str,
        workspace_id: int,
        parent_id: int,
        name: str,
        node_type: str,
    ) -> Any | None: ...

    def create_node(
        self,
        db: Session,
        *,
        tenant_key: str,
        workspace_id: int,
        parent_id: int | None,
        name: str,
        node_type: str,
        content_text: str,
        version_no: int,
    ) -> Any: ...

    def get_node(self, db: Session, node_id: int, tenant_key: str) -> Any | None: ...

    def list_workspace_nodes(self, db: Session, workspace_id: int, tenant_key: str) -> list[Any]: ...

    def update_node_content(
        self,
        db: Session,
        *,
        node_id: int,
        tenant_key: str,
        parent_id: int | None,
        name: str,
        node_type: str,
        content_text: str,
        version_no: int,
    ) -> None: ...

    def touch_node(self, db: Session, *, node: Any) -> None: ...

    def search_subtree_files(
        self,
        db: Session,
        *,
        tenant_key: str,
        workspace_id: int,
        root_id: int,
        pattern: str,
        use_case_insensitive: bool,
        use_literal_case_sensitive: bool,
    ) -> list[dict[str, Any]]: ...


class CommandLogRepository(Protocol):
    def create_command_log(self, db: Session, payload: dict[str, Any]) -> int: ...

    def bulk_insert_command_logs(self, db: Session, payloads: list[dict[str, Any]]) -> None: ...


@dataclass(frozen=True)
class VFSRepositories:
    workspace: WorkspaceRepository
    session: SessionRepository
    node: NodeRepository
    command_log: CommandLogRepository
