from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class SQLAlchemyWorkspaceRepository:
    AgentWorkspace: type[Any]

    def get_workspace(self, db: Session, workspace_id: int, tenant_key: str) -> Any | None:
        return db.scalars(
            select(self.AgentWorkspace).where(
                self.AgentWorkspace.id == workspace_id,
                self.AgentWorkspace.tenant_id == tenant_key,
            )
        ).first()

    def update_workspace_metadata(
        self,
        db: Session,
        *,
        workspace_id: int,
        tenant_key: str,
        metadata_json: dict[str, Any],
    ) -> bool:
        workspace = self.get_workspace(db, workspace_id, tenant_key)
        if not workspace:
            return False
        workspace.metadata_json = metadata_json
        workspace.tenant_id = tenant_key
        db.add(workspace)
        return True

