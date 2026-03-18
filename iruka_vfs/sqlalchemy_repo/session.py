from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class SQLAlchemySessionRepository:
    VirtualShellSession: type[Any]

    def get_active_session(self, db: Session, workspace_id: int, tenant_key: str) -> Any | None:
        return db.scalars(
            select(self.VirtualShellSession)
            .where(
                self.VirtualShellSession.tenant_id == tenant_key,
                self.VirtualShellSession.workspace_id == workspace_id,
                self.VirtualShellSession.status == "active",
            )
            .order_by(self.VirtualShellSession.id.desc())
        ).first()

    def create_session(
        self,
        db: Session,
        *,
        tenant_key: str,
        workspace_id: int,
        cwd_node_id: int,
        env_json: dict[str, Any],
        status: str,
    ) -> Any:
        session = self.VirtualShellSession(
            tenant_id=tenant_key,
            workspace_id=workspace_id,
            cwd_node_id=cwd_node_id,
            env_json=env_json,
            status=status,
        )
        db.add(session)
        db.flush()
        return session

    def update_session_cwd(
        self,
        db: Session,
        *,
        session_id: int,
        tenant_key: str,
        cwd_node_id: int,
    ) -> None:
        session = db.get(self.VirtualShellSession, session_id)
        if not session or str(getattr(session, "tenant_id", "") or "") != tenant_key:
            return
        session.cwd_node_id = cwd_node_id
        db.add(session)

