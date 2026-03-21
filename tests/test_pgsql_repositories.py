from __future__ import annotations

import unittest
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from iruka_vfs.dependencies import VFSDependencies
from iruka_vfs.pgsql_repositories import (
    PostgreSQLCommandLogRepository,
    PostgreSQLNodeRepository,
    PostgreSQLSessionRepository,
    PostgreSQLWorkspaceRepository,
    build_pgsql_repositories,
)
from iruka_vfs.sqlalchemy_models import Base, VFSFileNode, VFSShellCommand, VFSShellSession, VFSWorkspace


class _FakeExecuteResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return list(self._rows)


class _FakeExecuteSession:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def execute(self, statement, params):
        self.calls.append((statement, params))
        return _FakeExecuteResult(self.rows)


class PostgreSQLRepositoriesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, class_=Session)
        self.workspace_repo = PostgreSQLWorkspaceRepository(AgentWorkspace=VFSWorkspace)
        self.session_repo = PostgreSQLSessionRepository(VirtualShellSession=VFSShellSession)
        self.node_repo = PostgreSQLNodeRepository(VirtualFileNode=VFSFileNode)
        self.command_log_repo = PostgreSQLCommandLogRepository(VirtualShellCommand=VFSShellCommand)

    def tearDown(self) -> None:
        self.engine.dispose()

    def _create_workspace(self, db: Session, *, tenant_id: str = "tenant-a", runtime_key: str = "runtime:1") -> VFSWorkspace:
        workspace = VFSWorkspace(tenant_id=tenant_id, runtime_key=runtime_key, metadata_json={})
        db.add(workspace)
        db.commit()
        db.refresh(workspace)
        return workspace

    def _create_root(self, db: Session, workspace: VFSWorkspace, *, tenant_id: str = "tenant-a") -> VFSFileNode:
        return self.node_repo.create_node(
            db,
            tenant_key=tenant_id,
            workspace_id=int(workspace.id),
            parent_id=None,
            name="",
            node_type="dir",
            content_text="",
            version_no=1,
        )

    def test_build_pgsql_repositories_returns_postgresql_classes(self) -> None:
        dependencies = VFSDependencies(
            settings=SimpleNamespace(default_tenant_id="tenant-a", redis_key_namespace="test", redis_url="redis://localhost:6379/0", database_url="postgresql://example"),
            AgentWorkspace=VFSWorkspace,
            VirtualFileNode=VFSFileNode,
            VirtualShellCommand=VFSShellCommand,
            VirtualShellSession=VFSShellSession,
            load_project_state_payload=lambda *args, **kwargs: {},
        )
        repositories = build_pgsql_repositories(dependencies)
        self.assertIsInstance(repositories.workspace, PostgreSQLWorkspaceRepository)
        self.assertIsInstance(repositories.session, PostgreSQLSessionRepository)
        self.assertIsInstance(repositories.node, PostgreSQLNodeRepository)
        self.assertIsInstance(repositories.command_log, PostgreSQLCommandLogRepository)

    def test_workspace_repository_get_and_update_metadata(self) -> None:
        with self.SessionLocal() as db:
            workspace = self._create_workspace(db)
            loaded = self.workspace_repo.get_workspace(db, int(workspace.id), "tenant-a")
            self.assertIsNotNone(loaded)
            updated = self.workspace_repo.update_workspace_metadata(
                db,
                workspace_id=int(workspace.id),
                tenant_key="tenant-a",
                metadata_json={"virtual_access_mode": "agent"},
            )
            db.commit()
            self.assertTrue(updated)
            refreshed = self.workspace_repo.get_workspace(db, int(workspace.id), "tenant-a")
            self.assertEqual(refreshed.metadata_json["virtual_access_mode"], "agent")
            missing = self.workspace_repo.update_workspace_metadata(
                db,
                workspace_id=999,
                tenant_key="tenant-a",
                metadata_json={"x": 1},
            )
            self.assertFalse(missing)

    def test_session_repository_create_lookup_and_update(self) -> None:
        with self.SessionLocal() as db:
            workspace = self._create_workspace(db)
            root = self._create_root(db, workspace)
            db.commit()
            first = self.session_repo.create_session(
                db,
                tenant_key="tenant-a",
                workspace_id=int(workspace.id),
                cwd_node_id=int(root.id),
                env_json={"PWD": "/"},
                status="inactive",
            )
            active = self.session_repo.create_session(
                db,
                tenant_key="tenant-a",
                workspace_id=int(workspace.id),
                cwd_node_id=int(root.id),
                env_json={"PWD": "/workspace"},
                status="active",
            )
            db.commit()
            loaded = self.session_repo.get_active_session(db, int(workspace.id), "tenant-a")
            self.assertEqual(int(loaded.id), int(active.id))
            self.assertNotEqual(int(loaded.id), int(first.id))
            self.session_repo.update_session_cwd(
                db,
                session_id=int(active.id),
                tenant_key="tenant-a",
                cwd_node_id=123,
            )
            db.commit()
            refreshed = self.session_repo.get_active_session(db, int(workspace.id), "tenant-a")
            self.assertEqual(int(refreshed.cwd_node_id), 123)
            self.session_repo.update_session_cwd(
                db,
                session_id=int(active.id),
                tenant_key="other-tenant",
                cwd_node_id=456,
            )
            db.commit()
            unchanged = self.session_repo.get_active_session(db, int(workspace.id), "tenant-a")
            self.assertEqual(int(unchanged.cwd_node_id), 123)

    def test_node_repository_crud_and_listing(self) -> None:
        with self.SessionLocal() as db:
            workspace = self._create_workspace(db)
            root = self._create_root(db, workspace)
            docs = self.node_repo.create_node(
                db,
                tenant_key="tenant-a",
                workspace_id=int(workspace.id),
                parent_id=int(root.id),
                name="docs",
                node_type="dir",
                content_text="",
                version_no=1,
            )
            file_node = self.node_repo.create_node(
                db,
                tenant_key="tenant-a",
                workspace_id=int(workspace.id),
                parent_id=int(docs.id),
                name="readme.md",
                node_type="file",
                content_text="hello",
                version_no=1,
            )
            db.commit()

            self.assertEqual(int(self.node_repo.get_root(db, int(workspace.id), "tenant-a").id), int(root.id))
            self.assertEqual(
                int(
                    self.node_repo.get_child(
                        db,
                        tenant_key="tenant-a",
                        workspace_id=int(workspace.id),
                        parent_id=int(root.id),
                        name="docs",
                        node_type="dir",
                    ).id
                ),
                int(docs.id),
            )
            self.assertEqual(int(self.node_repo.get_node(db, int(file_node.id), "tenant-a").id), int(file_node.id))

            nodes = self.node_repo.list_workspace_nodes(db, int(workspace.id), "tenant-a")
            self.assertEqual([node.name for node in nodes], ["", "docs", "readme.md"])

            self.node_repo.update_node_content(
                db,
                node_id=int(file_node.id),
                tenant_key="tenant-a",
                parent_id=int(docs.id),
                name="guide.md",
                node_type="file",
                content_text="updated",
                version_no=2,
            )
            db.commit()
            refreshed = self.node_repo.get_node(db, int(file_node.id), "tenant-a")
            self.assertEqual(refreshed.name, "guide.md")
            self.assertEqual(refreshed.content_text, "updated")
            self.assertEqual(int(refreshed.version_no), 2)

            before_ts = refreshed.updated_at
            self.node_repo.touch_node(db, node=refreshed)
            db.commit()
            db.refresh(refreshed)
            self.assertGreaterEqual(refreshed.updated_at, before_ts)

    def test_command_log_repository_create_and_bulk_insert(self) -> None:
        with self.SessionLocal() as db:
            workspace = self._create_workspace(db)
            root = self._create_root(db, workspace)
            session = self.session_repo.create_session(
                db,
                tenant_key="tenant-a",
                workspace_id=int(workspace.id),
                cwd_node_id=int(root.id),
                env_json={"PWD": "/workspace"},
                status="active",
            )
            db.commit()

            command_id = self.command_log_repo.create_command_log(
                db,
                {
                    "tenant_id": "tenant-a",
                    "session_id": int(session.id),
                    "raw_cmd": "ls",
                    "parsed_json": {"segments": [{"cmd": "ls"}]},
                    "exit_code": 0,
                    "stdout_text": "docs",
                    "stderr_text": "",
                    "artifacts_json": {},
                },
            )
            self.assertGreater(command_id, 0)

            self.command_log_repo.bulk_insert_command_logs(
                db,
                [
                    {
                        "tenant_id": "tenant-a",
                        "session_id": int(session.id),
                        "raw_cmd": "pwd",
                        "parsed_json": {},
                        "exit_code": 0,
                        "stdout_text": "/workspace",
                        "stderr_text": "",
                        "artifacts_json": {},
                    },
                    {
                        "tenant_id": "tenant-a",
                        "session_id": int(session.id),
                        "raw_cmd": "cat readme.md",
                        "parsed_json": {},
                        "exit_code": 0,
                        "stdout_text": "hello",
                        "stderr_text": "",
                        "artifacts_json": {},
                    },
                ],
            )
            count = db.query(VFSShellCommand).count()
            self.assertEqual(count, 3)

    def test_search_subtree_files_builds_case_insensitive_query(self) -> None:
        fake_db = _FakeExecuteSession(rows=[{"id": 1, "rel_path": "docs/readme.md", "content_text": "Hello"}])
        rows = self.node_repo.search_subtree_files(
            fake_db,
            tenant_key="tenant-a",
            workspace_id=1,
            root_id=10,
            pattern="hello",
            use_case_insensitive=True,
            use_literal_case_sensitive=False,
        )
        statement, params = fake_db.calls[0]
        self.assertIn("ILIKE", str(statement))
        self.assertEqual(params["needle_ci"], "hello")
        self.assertEqual(rows[0]["rel_path"], "docs/readme.md")

    def test_search_subtree_files_builds_case_sensitive_query(self) -> None:
        fake_db = _FakeExecuteSession(rows=[{"id": 2, "rel_path": "docs/guide.md", "content_text": "Hello"}])
        rows = self.node_repo.search_subtree_files(
            fake_db,
            tenant_key="tenant-a",
            workspace_id=1,
            root_id=10,
            pattern="Hello",
            use_case_insensitive=False,
            use_literal_case_sensitive=True,
        )
        statement, params = fake_db.calls[0]
        self.assertIn("LIKE", str(statement))
        self.assertEqual(params["needle_cs"], "Hello")
        self.assertEqual(rows[0]["id"], 2)

    def test_search_subtree_files_without_filter_omits_needles(self) -> None:
        fake_db = _FakeExecuteSession(rows=[{"id": 3, "rel_path": "docs/all.txt", "content_text": "anything"}])
        rows = self.node_repo.search_subtree_files(
            fake_db,
            tenant_key="tenant-a",
            workspace_id=1,
            root_id=10,
            pattern="ignored",
            use_case_insensitive=False,
            use_literal_case_sensitive=False,
        )
        statement, params = fake_db.calls[0]
        self.assertNotIn("needle_ci", params)
        self.assertNotIn("needle_cs", params)
        self.assertIn("WHERE node_type = 'file'", str(statement))
        self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
