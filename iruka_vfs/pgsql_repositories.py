from __future__ import annotations

from iruka_vfs.repositories import VFSRepositories
from iruka_vfs.sqlalchemy_repo.command_log import SQLAlchemyCommandLogRepository
from iruka_vfs.sqlalchemy_repo.node import SQLAlchemyNodeRepository
from iruka_vfs.sqlalchemy_repo.session import SQLAlchemySessionRepository
from iruka_vfs.sqlalchemy_repo.workspace import SQLAlchemyWorkspaceRepository


class PostgreSQLWorkspaceRepository(SQLAlchemyWorkspaceRepository):
    pass


class PostgreSQLSessionRepository(SQLAlchemySessionRepository):
    pass


class PostgreSQLNodeRepository(SQLAlchemyNodeRepository):
    pass


class PostgreSQLCommandLogRepository(SQLAlchemyCommandLogRepository):
    pass


def build_pgsql_repositories(dependencies):
    return VFSRepositories(
        workspace=PostgreSQLWorkspaceRepository(AgentWorkspace=dependencies.AgentWorkspace),
        session=PostgreSQLSessionRepository(VirtualShellSession=dependencies.VirtualShellSession),
        node=PostgreSQLNodeRepository(VirtualFileNode=dependencies.VirtualFileNode),
        command_log=PostgreSQLCommandLogRepository(VirtualShellCommand=dependencies.VirtualShellCommand),
    )


__all__ = [
    "PostgreSQLWorkspaceRepository",
    "PostgreSQLSessionRepository",
    "PostgreSQLNodeRepository",
    "PostgreSQLCommandLogRepository",
    "build_pgsql_repositories",
]
