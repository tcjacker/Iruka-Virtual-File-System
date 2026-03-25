from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from iruka_vfs.runtime_seed import WorkspaceSeed


@dataclass(frozen=True)
class VirtualWorkspace:
    workspace: Any
    workspace_seed: WorkspaceSeed
    tenant_id: str

    @property
    def workspace_id(self) -> int:
        return int(self.workspace.id)

    def ensure(
        self,
        db: Session,
        *,
        include_tree: bool = True,
    ) -> dict[str, Any]:
        from iruka_vfs import service

        return service.ensure_virtual_workspace(
            db,
            self.workspace,
            self.workspace_seed,
            include_tree=include_tree,
            tenant_id=self.tenant_id,
        )

    def bash(self, db: Session, raw_cmd: str) -> dict[str, Any]:
        from iruka_vfs import service

        return service.run_virtual_bash(
            db,
            self.workspace,
            raw_cmd,
            workspace_seed=self.workspace_seed,
            tenant_id=self.tenant_id,
        )

    def refresh(self, db: Session, *, include_tree: bool = True) -> dict[str, Any]:
        from iruka_vfs import service

        return service.refresh_virtual_workspace(
            db,
            self.workspace,
            self.workspace_seed,
            include_tree=include_tree,
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
            workspace_seed=self.workspace_seed,
            mode="agent",
            tenant_id=self.tenant_id,
            flush=flush,
        )

    def enter_host_mode(self, db: Session, *, flush: bool = True) -> str:
        from iruka_vfs import service

        return service.set_workspace_access_mode(
            db,
            self.workspace,
            workspace_seed=self.workspace_seed,
            mode="host",
            tenant_id=self.tenant_id,
            flush=flush,
        )

    def access_mode(self, db: Session) -> str:
        from iruka_vfs import service

        return service.get_workspace_access_mode(
            db,
            self.workspace,
            workspace_seed=self.workspace_seed,
            tenant_id=self.tenant_id,
        )

    def write_file(self, db: Session, path: str, content: str, *, overwrite: bool = False) -> dict[str, Any]:
        from iruka_vfs import service

        return service.write_workspace_file(
            db,
            self.workspace,
            path,
            content,
            workspace_seed=self.workspace_seed,
            tenant_id=self.tenant_id,
            overwrite=overwrite,
        )

    def read_file(self, db: Session, path: str) -> str:
        from iruka_vfs import service

        return service.read_workspace_file(
            db,
            self.workspace,
            path,
            workspace_seed=self.workspace_seed,
            tenant_id=self.tenant_id,
        )

    def read_directory(self, db: Session, path: str, *, recursive: bool = True) -> dict[str, str]:
        from iruka_vfs import service

        return service.read_workspace_directory(
            db,
            self.workspace,
            path,
            workspace_seed=self.workspace_seed,
            tenant_id=self.tenant_id,
            recursive=recursive,
        )

    def tree(self, db: Session) -> str:
        snapshot = self.ensure(db, include_tree=True)
        return str(snapshot.get("tree") or "")

    @property
    def runtime_seed(self) -> WorkspaceSeed:
        return self.workspace_seed
