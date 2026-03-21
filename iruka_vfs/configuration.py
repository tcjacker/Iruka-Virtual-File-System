from __future__ import annotations

from typing import Literal

RuntimeProfile = Literal["persistent", "ephemeral-local", "ephemeral-redis"]
RepositoryBackend = Literal["pgsql", "sqlalchemy", "memory"]
WorkspaceStateBackend = Literal["redis", "local-memory"]
