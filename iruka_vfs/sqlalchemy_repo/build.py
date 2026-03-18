from __future__ import annotations

from iruka_vfs.dependencies import VFSDependencies
from iruka_vfs.repositories import VFSRepositories
from iruka_vfs.sqlalchemy_repo.command_log import SQLAlchemyCommandLogRepository
from iruka_vfs.sqlalchemy_repo.node import SQLAlchemyNodeRepository
from iruka_vfs.sqlalchemy_repo.session import SQLAlchemySessionRepository
from iruka_vfs.sqlalchemy_repo.workspace import SQLAlchemyWorkspaceRepository


def build_sqlalchemy_repositories(dependencies: VFSDependencies) -> VFSRepositories:
    return VFSRepositories(
        workspace=SQLAlchemyWorkspaceRepository(AgentWorkspace=dependencies.AgentWorkspace),
        session=SQLAlchemySessionRepository(VirtualShellSession=dependencies.VirtualShellSession),
        node=SQLAlchemyNodeRepository(VirtualFileNode=dependencies.VirtualFileNode),
        command_log=SQLAlchemyCommandLogRepository(VirtualShellCommand=dependencies.VirtualShellCommand),
    )
