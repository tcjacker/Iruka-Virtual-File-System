from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from iruka_vfs.file_sources import ExternalFileSource
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

    def tree(self, db: Session) -> str:
        snapshot = self.ensure(db, include_tree=True)
        return str(snapshot.get("tree") or "")


def create_workspace_handle(
    *,
    workspace: Any,
    tenant_id: str | None = None,
    runtime_key: str | None = None,
    chapter_id: int | None = None,
    primary_file: ExternalFileSource | None = None,
    context_files: dict[str, str] | None = None,
    skill_files: dict[str, str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> VirtualWorkspace:
    resolved_tenant_id = str(tenant_id or getattr(workspace, "tenant_id", "") or "").strip()
    if not resolved_tenant_id:
        raise ValueError("tenant_id is required either explicitly or on workspace.tenant_id")

    resolved_runtime_key = str(runtime_key or getattr(workspace, "runtime_key", "") or "").strip()
    if not resolved_runtime_key:
        raise ValueError("runtime_key is required either explicitly or on workspace.runtime_key")

    resolved_chapter_id = chapter_id
    if resolved_chapter_id is None:
        workspace_chapter_id = getattr(workspace, "chapter_id", None)
        if workspace_chapter_id is not None:
            resolved_chapter_id = int(workspace_chapter_id)

    seed = RuntimeSeed(
        runtime_key=resolved_runtime_key,
        tenant_id=resolved_tenant_id,
        chapter_id=resolved_chapter_id,
        primary_file=primary_file,
        context_files=dict(context_files or {}),
        skill_files=dict(skill_files or {}),
        metadata=dict(metadata or {}),
    )
    return VirtualWorkspace(
        workspace=workspace,
        runtime_seed=seed,
        tenant_id=resolved_tenant_id,
    )


create_workspace = create_workspace_handle
VirtualWorkspaceHandle = VirtualWorkspace
