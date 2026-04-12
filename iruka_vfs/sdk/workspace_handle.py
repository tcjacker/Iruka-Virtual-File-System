from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from iruka_vfs.runtime_seed import RuntimeSeed


@dataclass(frozen=True)
class VirtualWorkspace:
    workspace: Any
    runtime_seed: RuntimeSeed
    tenant_id: str

    @property
    def workspace_id(self) -> int:
        return int(self.workspace.id)

    def ensure(
        self,
        db: Session,
        *,
        include_tree: bool = True,
        available_skills: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        from iruka_vfs import service

        return service.ensure_virtual_workspace(
            db,
            self.workspace,
            self.runtime_seed,
            include_tree=include_tree,
            available_skills=available_skills,
            tenant_id=self.tenant_id,
        )

    def bash(self, db: Session, raw_cmd: str) -> dict[str, Any]:
        from iruka_vfs import service

        return service.run_virtual_bash(
            db,
            self.workspace,
            raw_cmd,
            runtime_seed=self.runtime_seed,
            tenant_id=self.tenant_id,
        )

    def flush(self) -> bool:
        from iruka_vfs import service

        return service.flush_workspace(self.workspace_id, tenant_id=self.tenant_id)

    def enter_agent_mode(self, db: Session, *, flush: bool = True) -> str:
        from iruka_vfs import service

        return service.set_workspace_access_mode(
            db,
            self.workspace,
            runtime_seed=self.runtime_seed,
            mode="agent",
            tenant_id=self.tenant_id,
            flush=flush,
        )

    def enter_host_mode(self, db: Session, *, flush: bool = True) -> str:
        from iruka_vfs import service

        return service.set_workspace_access_mode(
            db,
            self.workspace,
            runtime_seed=self.runtime_seed,
            mode="host",
            tenant_id=self.tenant_id,
            flush=flush,
        )

    def access_mode(self, db: Session) -> str:
        from iruka_vfs import service

        return service.get_workspace_access_mode(
            db,
            self.workspace,
            runtime_seed=self.runtime_seed,
            tenant_id=self.tenant_id,
        )

    def write_file(self, db: Session, path: str, content: str) -> dict[str, Any]:
        from iruka_vfs import service

        return service.write_workspace_file(
            db,
            self.workspace,
            path,
            content,
            runtime_seed=self.runtime_seed,
            tenant_id=self.tenant_id,
        )

    def tool_write(self, db: Session, path: str, content: str) -> dict[str, Any]:
        from iruka_vfs.service_ops.file_api import tool_write_workspace_file

        return tool_write_workspace_file(
            db,
            self.workspace,
            path,
            content,
            runtime_seed=self.runtime_seed,
            tenant_id=self.tenant_id,
        )

    def tool_edit(
        self,
        db: Session,
        path: str,
        old_text: str,
        new_text: str,
        *,
        replace_all: bool = False,
    ) -> dict[str, Any]:
        from iruka_vfs.service_ops.file_api import tool_edit_workspace_file

        return tool_edit_workspace_file(
            db,
            self.workspace,
            path,
            old_text,
            new_text,
            replace_all=replace_all,
            runtime_seed=self.runtime_seed,
            tenant_id=self.tenant_id,
        )

    def file_tree(self, db: Session, path: str = "/workspace") -> dict[str, Any]:
        from iruka_vfs.service_ops.file_api import get_workspace_file_tree

        return get_workspace_file_tree(
            db,
            self.workspace,
            path,
            runtime_seed=self.runtime_seed,
            tenant_id=self.tenant_id,
        )

    def read_file(self, db: Session, path: str) -> str:
        from iruka_vfs import service

        return service.read_workspace_file(
            db,
            self.workspace,
            path,
            runtime_seed=self.runtime_seed,
            tenant_id=self.tenant_id,
        )

    def read_directory(self, db: Session, path: str, *, recursive: bool = True) -> dict[str, str]:
        from iruka_vfs import service

        return service.read_workspace_directory(
            db,
            self.workspace,
            path,
            runtime_seed=self.runtime_seed,
            tenant_id=self.tenant_id,
            recursive=recursive,
        )

    def tree(self, db: Session) -> str:
        snapshot = self.ensure(db, include_tree=True)
        return str(snapshot.get("tree") or "")

