from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

@dataclass(frozen=True)
class WorkspaceSeed:
    runtime_key: str
    tenant_id: str
    workspace_files: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


def build_workspace_seed(
    *,
    runtime_key: str,
    tenant_id: str,
    workspace_files: dict[str, str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> WorkspaceSeed:
    return WorkspaceSeed(
        runtime_key=runtime_key,
        tenant_id=tenant_id,
        workspace_files=dict(workspace_files or {}),
        metadata=dict(metadata or {}),
    )


@dataclass(frozen=True)
class RuntimeSeed(WorkspaceSeed):
    """Legacy alias for WorkspaceSeed."""

    def workspace_seed(self) -> WorkspaceSeed:
        return WorkspaceSeed(
            runtime_key=self.runtime_key,
            tenant_id=self.tenant_id,
            workspace_files=dict(self.workspace_files),
            metadata=dict(self.metadata),
        )
