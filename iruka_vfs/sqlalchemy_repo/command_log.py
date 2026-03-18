from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import insert
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class SQLAlchemyCommandLogRepository:
    VirtualShellCommand: type[Any]

    def create_command_log(self, db: Session, payload: dict[str, Any]) -> int:
        row = self.VirtualShellCommand(**payload)
        db.add(row)
        db.commit()
        return int(row.id or 0)

    def bulk_insert_command_logs(self, db: Session, payloads: list[dict[str, Any]]) -> None:
        if not payloads:
            return
        db.execute(insert(self.VirtualShellCommand), payloads)
        db.commit()

