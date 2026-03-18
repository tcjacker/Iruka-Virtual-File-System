from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session


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

