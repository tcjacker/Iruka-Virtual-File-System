from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from iruka_vfs.file_sources import ExternalFileSource


@dataclass(frozen=True)
class RuntimeSeed:
    runtime_key: str
    tenant_id: str
    primary_file: ExternalFileSource | None = None
    workspace_files: dict[str, str] = field(default_factory=dict)
    context_files: dict[str, str] = field(default_factory=dict)
    skill_files: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
