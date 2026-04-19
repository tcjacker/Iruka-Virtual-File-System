from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from iruka_vfs.runtime_seed import RuntimeSeed

logger = logging.getLogger(__name__)


def _append_recovery_note(
    exc: Exception,
    *,
    workspace_id: int,
    tenant_id: str,
    original_mode: str | None,
    recovery_target_mode: str,
    recovery_exc: Exception,
) -> None:
    exc.add_note(
        "workspace mode recovery failed: "
        f"workspace_id={workspace_id} tenant_id={tenant_id} "
        f"original_mode={original_mode!r} recovery_target_mode={recovery_target_mode!r} "
        f"recovery_error={type(recovery_exc).__name__}: {recovery_exc}"
    )


def _log_recovery_failure(
    *,
    workspace_id: int,
    tenant_id: str,
    original_mode: str | None,
    target_mode: str,
    action_name: str,
    action_exc: Exception | None,
    recovery_exc: Exception,
    action_succeeded: bool,
) -> None:
    logger.error(
        "workspace mode recovery failed",
        extra={
            "workspace_id": workspace_id,
            "tenant_id": tenant_id,
            "original_mode": original_mode,
            "target_mode": target_mode,
            "attempted_recovery_mode": "host",
            "action_name": action_name,
            "action_exception_type": None if action_exc is None else type(action_exc).__name__,
            "recovery_exception_type": type(recovery_exc).__name__,
            "action_succeeded": action_succeeded,
        },
    )


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

    def _run_with_mode(
        self,
        db: Session,
        *,
        action_name: str,
        target_mode: str,
        action: Callable[[], Any],
    ) -> Any:
        from iruka_vfs import service

        service.ensure_virtual_workspace(
            db,
            self.workspace,
            self.runtime_seed,
            include_tree=False,
            tenant_id=self.tenant_id,
        )
        original_mode = service.get_workspace_access_mode(
            db,
            self.workspace,
            runtime_seed=self.runtime_seed,
            tenant_id=self.tenant_id,
        )
        action_result = None
        action_exc: Exception | None = None
        service.set_workspace_access_mode(
            db,
            self.workspace,
            runtime_seed=self.runtime_seed,
            mode=target_mode,
            tenant_id=self.tenant_id,
            flush=True,
        )
        try:
            action_result = action()
        except Exception as exc:
            action_exc = exc
        finally:
            try:
                service.set_workspace_access_mode(
                    db,
                    self.workspace,
                    runtime_seed=self.runtime_seed,
                    mode="host",
                    tenant_id=self.tenant_id,
                    flush=True,
                )
            except Exception as recovery_exc:
                _log_recovery_failure(
                    workspace_id=self.workspace_id,
                    tenant_id=self.tenant_id,
                    original_mode=original_mode,
                    target_mode=target_mode,
                    action_name=action_name,
                    action_exc=action_exc,
                    recovery_exc=recovery_exc,
                    action_succeeded=action_exc is None,
                )
                if action_exc is not None:
                    _append_recovery_note(
                        action_exc,
                        workspace_id=self.workspace_id,
                        tenant_id=self.tenant_id,
                        original_mode=original_mode,
                        recovery_target_mode="host",
                        recovery_exc=recovery_exc,
                    )
                    raise action_exc
                recovery_exc.add_note(
                    f"action {action_name!r} succeeded but post-condition failed: "
                    f"workspace {self.workspace_id} original_mode={original_mode!r} "
                    "recovery_target_mode='host' was not restored"
                )
                raise recovery_exc
        if action_exc is not None:
            raise action_exc
        return action_result

    def run(self, db: Session, raw_cmd: str) -> dict[str, Any]:
        from iruka_vfs import service

        return self._run_with_mode(
            db,
            action_name="run",
            target_mode="agent",
            action=lambda: service.run_virtual_bash(
                db,
                self.workspace,
                raw_cmd,
                runtime_seed=self.runtime_seed,
                tenant_id=self.tenant_id,
            ),
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

    def bootstrap_snapshot(
        self,
        db: Session,
        *,
        include_tree: bool = True,
        chunk_size: int = 1000,
    ) -> dict[str, Any]:
        from iruka_vfs import service

        return service.bootstrap_workspace_snapshot(
            db,
            self.workspace,
            self.runtime_seed,
            include_tree=include_tree,
            tenant_id=self.tenant_id,
            chunk_size=chunk_size,
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

    def write(self, db: Session, path: str, content: str) -> dict[str, Any]:
        from iruka_vfs.service_ops.file_api import tool_write_workspace_file

        return self._run_with_mode(
            db,
            action_name="write",
            target_mode="host",
            action=lambda: tool_write_workspace_file(
                db,
                self.workspace,
                path,
                content,
                runtime_seed=self.runtime_seed,
                tenant_id=self.tenant_id,
            ),
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

    def edit(
        self,
        db: Session,
        path: str,
        old_text: str,
        new_text: str,
        *,
        replace_all: bool = False,
    ) -> dict[str, Any]:
        from iruka_vfs.service_ops.file_api import tool_edit_workspace_file

        return self._run_with_mode(
            db,
            action_name="edit",
            target_mode="host",
            action=lambda: tool_edit_workspace_file(
                db,
                self.workspace,
                path,
                old_text,
                new_text,
                replace_all=replace_all,
                runtime_seed=self.runtime_seed,
                tenant_id=self.tenant_id,
            ),
        )

    def file_tree(self, db: Session, path: str = "/workspace") -> dict[str, Any]:
        from iruka_vfs.service_ops.file_api import get_workspace_file_tree

        return self._run_with_mode(
            db,
            action_name="file_tree",
            target_mode="host",
            action=lambda: get_workspace_file_tree(
                db,
                self.workspace,
                path,
                runtime_seed=self.runtime_seed,
                tenant_id=self.tenant_id,
            ),
        )

    def read_file(self, db: Session, path: str) -> str:
        from iruka_vfs import service

        return self._run_with_mode(
            db,
            action_name="read_file",
            target_mode="host",
            action=lambda: service.read_workspace_file(
                db,
                self.workspace,
                path,
                runtime_seed=self.runtime_seed,
                tenant_id=self.tenant_id,
            ),
        )

    def read_directory(self, db: Session, path: str, *, recursive: bool = True) -> dict[str, str]:
        from iruka_vfs import service

        return self._run_with_mode(
            db,
            action_name="read_directory",
            target_mode="host",
            action=lambda: service.read_workspace_directory(
                db,
                self.workspace,
                path,
                runtime_seed=self.runtime_seed,
                tenant_id=self.tenant_id,
                recursive=recursive,
            ),
        )

    def tree(self, db: Session) -> str:
        snapshot = self.ensure(db, include_tree=True)
        return str(snapshot.get("tree") or "")
