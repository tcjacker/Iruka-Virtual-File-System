from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import insert, select, text
from sqlalchemy.orm import Session

from iruka_vfs.dependencies import VFSDependencies
from iruka_vfs.repositories import VFSRepositories


@dataclass(frozen=True)
class SQLAlchemyWorkspaceRepository:
    AgentWorkspace: type[Any]

    def get_workspace(self, db: Session, workspace_id: int, tenant_key: str) -> Any | None:
        return db.scalars(
            select(self.AgentWorkspace).where(
                self.AgentWorkspace.id == workspace_id,
                self.AgentWorkspace.tenant_id == tenant_key,
            )
        ).first()

    def update_workspace_metadata(
        self,
        db: Session,
        *,
        workspace_id: int,
        tenant_key: str,
        metadata_json: dict[str, Any],
    ) -> bool:
        workspace = self.get_workspace(db, workspace_id, tenant_key)
        if not workspace:
            return False
        workspace.metadata_json = metadata_json
        workspace.tenant_id = tenant_key
        db.add(workspace)
        return True


@dataclass(frozen=True)
class SQLAlchemySessionRepository:
    VirtualShellSession: type[Any]

    def get_active_session(self, db: Session, workspace_id: int, tenant_key: str) -> Any | None:
        return db.scalars(
            select(self.VirtualShellSession)
            .where(
                self.VirtualShellSession.tenant_id == tenant_key,
                self.VirtualShellSession.workspace_id == workspace_id,
                self.VirtualShellSession.status == "active",
            )
            .order_by(self.VirtualShellSession.id.desc())
        ).first()

    def create_session(
        self,
        db: Session,
        *,
        tenant_key: str,
        workspace_id: int,
        cwd_node_id: int,
        env_json: dict[str, Any],
        status: str,
    ) -> Any:
        session = self.VirtualShellSession(
            tenant_id=tenant_key,
            workspace_id=workspace_id,
            cwd_node_id=cwd_node_id,
            env_json=env_json,
            status=status,
        )
        db.add(session)
        db.flush()
        return session

    def update_session_cwd(
        self,
        db: Session,
        *,
        session_id: int,
        tenant_key: str,
        cwd_node_id: int,
    ) -> None:
        session = db.get(self.VirtualShellSession, session_id)
        if not session or str(getattr(session, "tenant_id", "") or "") != tenant_key:
            return
        session.cwd_node_id = cwd_node_id
        db.add(session)


@dataclass(frozen=True)
class SQLAlchemyNodeRepository:
    VirtualFileNode: type[Any]

    def get_root(self, db: Session, workspace_id: int, tenant_key: str) -> Any | None:
        return db.scalars(
            select(self.VirtualFileNode)
            .where(
                self.VirtualFileNode.tenant_id == tenant_key,
                self.VirtualFileNode.workspace_id == workspace_id,
                self.VirtualFileNode.parent_id.is_(None),
                self.VirtualFileNode.name == "",
            )
        ).first()

    def get_child(
        self,
        db: Session,
        *,
        tenant_key: str,
        workspace_id: int,
        parent_id: int,
        name: str,
        node_type: str,
    ) -> Any | None:
        return db.scalars(
            select(self.VirtualFileNode).where(
                self.VirtualFileNode.tenant_id == tenant_key,
                self.VirtualFileNode.workspace_id == workspace_id,
                self.VirtualFileNode.parent_id == parent_id,
                self.VirtualFileNode.name == name,
                self.VirtualFileNode.node_type == node_type,
            )
        ).first()

    def create_node(
        self,
        db: Session,
        *,
        tenant_key: str,
        workspace_id: int,
        parent_id: int | None,
        name: str,
        node_type: str,
        content_text: str,
        version_no: int,
    ) -> Any:
        node = self.VirtualFileNode(
            tenant_id=tenant_key,
            workspace_id=workspace_id,
            parent_id=parent_id,
            name=name,
            node_type=node_type,
            content_text=content_text,
            version_no=version_no,
        )
        db.add(node)
        db.flush()
        return node

    def get_node(self, db: Session, node_id: int, tenant_key: str) -> Any | None:
        return db.scalars(
            select(self.VirtualFileNode).where(
                self.VirtualFileNode.tenant_id == tenant_key,
                self.VirtualFileNode.id == node_id,
            )
        ).first()

    def list_workspace_nodes(self, db: Session, workspace_id: int, tenant_key: str) -> list[Any]:
        return list(
            db.scalars(
                select(self.VirtualFileNode)
                .where(
                    self.VirtualFileNode.tenant_id == tenant_key,
                    self.VirtualFileNode.workspace_id == workspace_id,
                )
                .order_by(
                    self.VirtualFileNode.parent_id.asc(),
                    self.VirtualFileNode.node_type.asc(),
                    self.VirtualFileNode.name.asc(),
                )
            ).all()
        )

    def update_node_content(
        self,
        db: Session,
        *,
        node_id: int,
        tenant_key: str,
        parent_id: int | None,
        name: str,
        node_type: str,
        content_text: str,
        version_no: int,
    ) -> None:
        node = self.get_node(db, node_id, tenant_key)
        if not node:
            return
        node.parent_id = parent_id
        node.name = name
        node.node_type = node_type
        node.content_text = content_text
        node.version_no = version_no
        db.add(node)

    def touch_node(self, db: Session, *, node: Any) -> None:
        db.add(node)
        db.flush()

    def search_subtree_files(
        self,
        db: Session,
        *,
        tenant_key: str,
        workspace_id: int,
        root_id: int,
        pattern: str,
        use_case_insensitive: bool,
        use_literal_case_sensitive: bool,
    ) -> list[dict[str, Any]]:
        sql_filter = ""
        params: dict[str, Any] = {
            "tenant_id": tenant_key,
            "workspace_id": workspace_id,
            "root_id": root_id,
        }
        if use_case_insensitive:
            sql_filter = "AND content_text ILIKE '%' || :needle_ci || '%'"
            params["needle_ci"] = pattern
        elif use_literal_case_sensitive:
            sql_filter = "AND content_text LIKE '%' || :needle_cs || '%'"
            params["needle_cs"] = pattern

        rows = db.execute(
            text(
                f"""
                WITH RECURSIVE subtree AS (
                  SELECT id, parent_id, node_type, name, content_text, ''::text AS rel_path
                  FROM virtual_file_nodes
                  WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :root_id
                  UNION ALL
                  SELECT c.id, c.parent_id, c.node_type, c.name, c.content_text,
                         CASE WHEN s.rel_path = '' THEN c.name ELSE s.rel_path || '/' || c.name END AS rel_path
                  FROM virtual_file_nodes c
                  JOIN subtree s ON c.parent_id = s.id
                  WHERE c.tenant_id = :tenant_id AND c.workspace_id = :workspace_id
                )
                SELECT id, rel_path, content_text
                FROM subtree
                WHERE node_type = 'file'
                {sql_filter}
                ORDER BY rel_path
                """
            ),
            params,
        ).mappings()
        return [dict(row) for row in rows]


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


def build_sqlalchemy_repositories(dependencies: VFSDependencies) -> VFSRepositories:
    return VFSRepositories(
        workspace=SQLAlchemyWorkspaceRepository(AgentWorkspace=dependencies.AgentWorkspace),
        session=SQLAlchemySessionRepository(VirtualShellSession=dependencies.VirtualShellSession),
        node=SQLAlchemyNodeRepository(VirtualFileNode=dependencies.VirtualFileNode),
        command_log=SQLAlchemyCommandLogRepository(VirtualShellCommand=dependencies.VirtualShellCommand),
    )
