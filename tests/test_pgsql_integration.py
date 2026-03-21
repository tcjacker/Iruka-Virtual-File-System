from __future__ import annotations

import importlib
import os
import unittest
import uuid
from types import SimpleNamespace

from sqlalchemy import create_engine, delete, event, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from iruka_vfs.dependencies import configure_vfs_dependencies
from iruka_vfs.pgsql_repositories import build_pgsql_repositories
from iruka_vfs.profile_setup import build_profile_dependencies
from iruka_vfs.sqlalchemy_models import Base, VFSFileNode, VFSShellCommand, VFSShellSession, VFSWorkspace


TEST_DATABASE_URL = os.environ.get("VFS_TEST_DATABASE_URL", "").strip()


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _engine_with_search_path(database_url: str, schema_name: str) -> Engine:
    engine = create_engine(database_url, future=True)

    @event.listens_for(engine, "connect")
    def _set_search_path(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(f"SET search_path TO {_quote_ident(schema_name)}")
        finally:
            cursor.close()

    return engine


@unittest.skipUnless(TEST_DATABASE_URL, "VFS_TEST_DATABASE_URL is not set")
class PostgreSQLIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema_name = f"itest_vfs_{uuid.uuid4().hex[:12]}"
        admin_engine = create_engine(TEST_DATABASE_URL, future=True)
        with admin_engine.begin() as conn:
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {_quote_ident(cls.schema_name)}"))
        admin_engine.dispose()

        cls.engine = _engine_with_search_path(TEST_DATABASE_URL, cls.schema_name)
        Base.metadata.create_all(bind=cls.engine)
        cls.SessionLocal = sessionmaker(bind=cls.engine, autoflush=False, autocommit=False, class_=Session)
        cls.test_tenant = "itest-vfs-pgsql"

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            Base.metadata.drop_all(bind=cls.engine)
        finally:
            cls.engine.dispose()

        admin_engine = create_engine(TEST_DATABASE_URL, future=True)
        with admin_engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {_quote_ident(cls.schema_name)} CASCADE"))
        admin_engine.dispose()

    def setUp(self) -> None:
        self._cleanup_rows()

    def tearDown(self) -> None:
        self._cleanup_rows()

    def _cleanup_rows(self) -> None:
        with self.SessionLocal() as db:
            workspace_ids = list(db.scalars(select(VFSWorkspace.id).where(VFSWorkspace.tenant_id == self.test_tenant)).all())
            if workspace_ids:
                db.execute(delete(VFSShellCommand).where(VFSShellCommand.tenant_id == self.test_tenant))
                db.execute(delete(VFSShellSession).where(VFSShellSession.tenant_id == self.test_tenant))
                db.execute(delete(VFSFileNode).where(VFSFileNode.tenant_id == self.test_tenant))
                db.execute(delete(VFSWorkspace).where(VFSWorkspace.tenant_id == self.test_tenant))
                db.commit()

    def _base_dependencies(self, *, workspace_state_backend: str = "local-memory"):
        return build_profile_dependencies(
            settings=SimpleNamespace(
                default_tenant_id=self.test_tenant,
                redis_key_namespace="test",
                redis_url="redis://localhost:6379/0",
                database_url=TEST_DATABASE_URL,
            ),
            VirtualFileNode=VFSFileNode,
            VirtualShellCommand=VFSShellCommand,
            VirtualShellSession=VFSShellSession,
            load_project_state_payload=lambda *args, **kwargs: {},
            runtime_profile="persistent",
            repository_backend="pgsql",
            workspace_state_backend=workspace_state_backend,
        )

    def test_pgsql_repositories_round_trip_against_real_database(self) -> None:
        repositories = build_pgsql_repositories(self._base_dependencies())
        with self.SessionLocal() as db:
            workspace = VFSWorkspace(
                tenant_id=self.test_tenant,
                runtime_key="runtime:pg-itest",
                metadata_json={},
            )
            db.add(workspace)
            db.commit()
            db.refresh(workspace)

            root = repositories.node.create_node(
                db,
                tenant_key=self.test_tenant,
                workspace_id=int(workspace.id),
                parent_id=None,
                name="",
                node_type="dir",
                content_text="",
                version_no=1,
            )
            docs = repositories.node.create_node(
                db,
                tenant_key=self.test_tenant,
                workspace_id=int(workspace.id),
                parent_id=int(root.id),
                name="docs",
                node_type="dir",
                content_text="",
                version_no=1,
            )
            file_node = repositories.node.create_node(
                db,
                tenant_key=self.test_tenant,
                workspace_id=int(workspace.id),
                parent_id=int(docs.id),
                name="readme.md",
                node_type="file",
                content_text="Hello PostgreSQL",
                version_no=1,
            )
            session = repositories.session.create_session(
                db,
                tenant_key=self.test_tenant,
                workspace_id=int(workspace.id),
                cwd_node_id=int(docs.id),
                env_json={"PWD": "/workspace/docs"},
                status="active",
            )
            db.commit()

            repositories.workspace.update_workspace_metadata(
                db,
                workspace_id=int(workspace.id),
                tenant_key=self.test_tenant,
                metadata_json={"virtual_access_mode": "agent"},
            )
            repositories.session.update_session_cwd(
                db,
                session_id=int(session.id),
                tenant_key=self.test_tenant,
                cwd_node_id=int(root.id),
            )
            repositories.node.update_node_content(
                db,
                node_id=int(file_node.id),
                tenant_key=self.test_tenant,
                parent_id=int(docs.id),
                name="guide.md",
                node_type="file",
                content_text="Hello PostgreSQL world",
                version_no=2,
            )
            command_id = repositories.command_log.create_command_log(
                db,
                {
                    "tenant_id": self.test_tenant,
                    "session_id": int(session.id),
                    "raw_cmd": "cat guide.md",
                    "parsed_json": {},
                    "exit_code": 0,
                    "stdout_text": "Hello PostgreSQL world",
                    "stderr_text": "",
                    "artifacts_json": {},
                },
            )
            repositories.command_log.bulk_insert_command_logs(
                db,
                [
                    {
                        "tenant_id": self.test_tenant,
                        "session_id": int(session.id),
                        "raw_cmd": "pwd",
                        "parsed_json": {},
                        "exit_code": 0,
                        "stdout_text": "/",
                        "stderr_text": "",
                        "artifacts_json": {},
                    }
                ],
            )

            workspace_row = repositories.workspace.get_workspace(db, int(workspace.id), self.test_tenant)
            session_row = repositories.session.get_active_session(db, int(workspace.id), self.test_tenant)
            node_row = repositories.node.get_node(db, int(file_node.id), self.test_tenant)
            search_rows = repositories.node.search_subtree_files(
                db,
                tenant_key=self.test_tenant,
                workspace_id=int(workspace.id),
                root_id=int(root.id),
                pattern="postgresql",
                use_case_insensitive=True,
                use_literal_case_sensitive=False,
            )

            self.assertEqual(workspace_row.metadata_json["virtual_access_mode"], "agent")
            self.assertEqual(int(session_row.cwd_node_id), int(root.id))
            self.assertEqual(node_row.name, "guide.md")
            self.assertEqual(node_row.content_text, "Hello PostgreSQL world")
            self.assertEqual(int(node_row.version_no), 2)
            self.assertGreater(command_id, 0)
            self.assertEqual(len(search_rows), 1)
            self.assertEqual(search_rows[0]["rel_path"], "docs/guide.md")
            log_count = db.query(VFSShellCommand).filter(VFSShellCommand.tenant_id == self.test_tenant).count()
            self.assertEqual(log_count, 2)

    def test_flush_workspace_mirror_persists_dirty_content_to_pgsql(self) -> None:
        configure_vfs_dependencies(self._base_dependencies(workspace_state_backend="local-memory"))
        dependency_resolution = importlib.reload(importlib.import_module("iruka_vfs.dependency_resolution"))
        service_state = importlib.reload(importlib.import_module("iruka_vfs.service_ops.state"))
        importlib.reload(importlib.import_module("iruka_vfs.workspace_mirror"))
        importlib.reload(importlib.import_module("iruka_vfs.service"))
        checkpoint = importlib.reload(importlib.import_module("iruka_vfs.mirror.checkpoint"))
        mirror_api = importlib.import_module("iruka_vfs.workspace_mirror")
        runtime_state = importlib.import_module("iruka_vfs.runtime_state")

        repositories = dependency_resolution.resolve_vfs_repositories()
        with self.SessionLocal() as db:
            workspace = VFSWorkspace(
                tenant_id=self.test_tenant,
                runtime_key="runtime:flush-itest",
                metadata_json={},
            )
            db.add(workspace)
            db.commit()
            db.refresh(workspace)

            root = repositories.node.create_node(
                db,
                tenant_key=self.test_tenant,
                workspace_id=int(workspace.id),
                parent_id=None,
                name="",
                node_type="dir",
                content_text="",
                version_no=1,
            )
            workspace_dir = repositories.node.create_node(
                db,
                tenant_key=self.test_tenant,
                workspace_id=int(workspace.id),
                parent_id=int(root.id),
                name="workspace",
                node_type="dir",
                content_text="",
                version_no=1,
            )
            file_node = repositories.node.create_node(
                db,
                tenant_key=self.test_tenant,
                workspace_id=int(workspace.id),
                parent_id=int(workspace_dir.id),
                name="story.md",
                node_type="file",
                content_text="draft-1",
                version_no=1,
            )
            session = repositories.session.create_session(
                db,
                tenant_key=self.test_tenant,
                workspace_id=int(workspace.id),
                cwd_node_id=int(workspace_dir.id),
                env_json={"PWD": "/workspace"},
                status="active",
            )
            db.commit()

            mirror = mirror_api.build_workspace_mirror(db, workspace, session=session)
            with mirror.lock:
                mirror.nodes[int(file_node.id)].content_text = "draft-2"
                mirror.nodes[int(file_node.id)].version_no = 2
                mirror.dirty_content_node_ids.add(int(file_node.id))
                mirror.revision += 1
            mirror_api.set_workspace_mirror(mirror)

            runtime_state.workspace_checkpoint_session_maker = sessionmaker(
                bind=self.engine,
                autoflush=False,
                autocommit=False,
                class_=Session,
            )

            ok = checkpoint.flush_workspace_mirror(
                None,
                workspace_ref=service_state.get_workspace_state_store().workspace_ref(mirror=mirror),
            )
            self.assertTrue(ok)

        with self.SessionLocal() as verify_db:
            refreshed = repositories.node.get_node(verify_db, int(file_node.id), self.test_tenant)
            self.assertEqual(refreshed.content_text, "draft-2")
            self.assertEqual(int(refreshed.version_no), 2)
            current_mirror = service_state.get_workspace_state_store().get_workspace_mirror(
                int(workspace.id),
                tenant_key=self.test_tenant,
            )
            self.assertFalse(bool(current_mirror.dirty_content_node_ids))


if __name__ == "__main__":
    unittest.main()
