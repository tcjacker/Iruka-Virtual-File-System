from __future__ import annotations

import importlib
import tempfile
import unittest
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from iruka_vfs import (
    build_profile_dependencies,
    build_workspace_seed,
    configure_vfs_dependencies,
    create_workspace,
)
from iruka_vfs.sqlalchemy_models import Base, VFSFileNode, VFSWorkspace


def _reload(module_name: str):
    module = importlib.import_module(module_name)
    return importlib.reload(module)


class WorkspaceRefreshTest(unittest.TestCase):
    def setUp(self) -> None:
        configure_vfs_dependencies(
            build_profile_dependencies(
                settings=SimpleNamespace(
                    default_tenant_id="test-tenant",
                    redis_key_namespace="test",
                    redis_url="redis://localhost:6379/0",
                    database_url="sqlite://",
                ),
                runtime_profile="persistent",
                repository_backend="pgsql",
                workspace_state_backend="local-memory",
            )
        )
        _reload("iruka_vfs.dependency_resolution")
        _reload("iruka_vfs.service_ops.state")
        _reload("iruka_vfs.models")
        _reload("iruka_vfs.pathing.resolution")
        _reload("iruka_vfs.runtime.filesystem")
        _reload("iruka_vfs.runtime.executor")
        _reload("iruka_vfs.runtime.fs_commands")
        _reload("iruka_vfs.runtime.search")
        _reload("iruka_vfs.runtime")
        _reload("iruka_vfs.command_runtime")
        _reload("iruka_vfs.integrations.agent.shell")
        _reload("iruka_vfs.service_ops.bootstrap")
        self.service_state = _reload("iruka_vfs.service_ops.state")
        _reload("iruka_vfs.service_ops.file_api")
        self.workspace_mirror = _reload("iruka_vfs.workspace_mirror")
        _reload("iruka_vfs.service")

        self.engine = create_engine(
            "sqlite+pysqlite://",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, class_=Session)
    def tearDown(self) -> None:
        self.engine.dispose()

    def test_refresh_rebuilds_workspace_mirror_from_database(self) -> None:
        with self.SessionLocal() as db:
            workspace_row = VFSWorkspace(
                tenant_id="test-tenant",
                runtime_key="runtime:refresh-1",
                metadata_json={},
            )
            db.add(workspace_row)
            db.commit()
            db.refresh(workspace_row)

            workspace = create_workspace(
                workspace=workspace_row,
                tenant_id="test-tenant",
                workspace_seed=build_workspace_seed(
                    runtime_key="runtime:refresh-1",
                    tenant_id="test-tenant",
                    workspace_files={"/workspace/files/demo.txt": "host-seed"},
                ),
            )

            workspace.ensure(db)
            original = workspace.read_file(db, "/workspace/files/demo.txt")
            self.assertEqual(original, "host-seed")

            node = db.scalar(
                select(VFSFileNode).where(
                    VFSFileNode.tenant_id == "test-tenant",
                    VFSFileNode.workspace_id == int(workspace_row.id),
                    VFSFileNode.name == "demo.txt",
                )
            )
            self.assertIsNotNone(node)
            node.content_text = "db-updated"
            node.version_no = int(node.version_no) + 1
            db.commit()

            stale = workspace.read_file(db, "/workspace/files/demo.txt")
            self.assertEqual(stale, "host-seed")

            refreshed = workspace.refresh(db, include_tree=False)
            self.assertEqual(refreshed["workspace_id"], int(workspace_row.id))

            current = workspace.read_file(db, "/workspace/files/demo.txt")
            self.assertEqual(current, "db-updated")

    def test_refresh_skips_rebuild_when_mirror_matches_database(self) -> None:
        with self.SessionLocal() as db:
            workspace_row = VFSWorkspace(
                tenant_id="test-tenant",
                runtime_key="runtime:refresh-2",
                metadata_json={},
            )
            db.add(workspace_row)
            db.commit()
            db.refresh(workspace_row)

            workspace = create_workspace(
                workspace=workspace_row,
                tenant_id="test-tenant",
                workspace_seed=build_workspace_seed(
                    runtime_key="runtime:refresh-2",
                    tenant_id="test-tenant",
                    workspace_files={"/workspace/files/demo.txt": "host-seed"},
                ),
            )

            workspace.ensure(db)
            workspace.refresh(db, include_tree=False)
            scope_key = self.workspace_mirror.workspace_scope_for_db(db)
            store = self.service_state.get_workspace_state_store()
            mirror_before = store.get_workspace_mirror(
                int(workspace_row.id),
                tenant_key="test-tenant",
                scope_key=scope_key,
            )
            self.assertIsNotNone(mirror_before)

            workspace.refresh(db, include_tree=False)

            mirror_after = store.get_workspace_mirror(
                int(workspace_row.id),
                tenant_key="test-tenant",
                scope_key=scope_key,
            )
            self.assertIs(mirror_after, mirror_before)

    def test_refresh_reloads_workspace_metadata_from_database(self) -> None:
        with self.SessionLocal() as db:
            workspace_row = VFSWorkspace(
                tenant_id="test-tenant",
                runtime_key="runtime:refresh-3",
                metadata_json={},
            )
            db.add(workspace_row)
            db.commit()
            db.refresh(workspace_row)

            workspace = create_workspace(
                workspace=workspace_row,
                tenant_id="test-tenant",
                workspace_seed=build_workspace_seed(
                    runtime_key="runtime:refresh-3",
                    tenant_id="test-tenant",
                    workspace_files={"/workspace/files/demo.txt": "host-seed"},
                ),
            )

            workspace.ensure(db)
            workspace_row.metadata_json = {
                **dict(workspace_row.metadata_json or {}),
                "virtual_access_mode": "agent",
            }
            db.add(workspace_row)
            db.commit()

            workspace.refresh(db, include_tree=False)

            scope_key = self.workspace_mirror.workspace_scope_for_db(db)
            store = self.service_state.get_workspace_state_store()
            mirror = store.get_workspace_mirror(
                int(workspace_row.id),
                tenant_key="test-tenant",
                scope_key=scope_key,
            )
            self.assertIsNotNone(mirror)
            self.assertEqual(mirror.workspace_metadata["virtual_access_mode"], "agent")

    def test_workspace_flush_persists_host_write_without_active_scope(self) -> None:
        with self.SessionLocal() as db:
            workspace_row = VFSWorkspace(
                tenant_id="test-tenant",
                runtime_key="runtime:refresh-4",
                metadata_json={},
            )
            db.add(workspace_row)
            db.commit()
            db.refresh(workspace_row)

            workspace = create_workspace(
                workspace=workspace_row,
                tenant_id="test-tenant",
                workspace_seed=build_workspace_seed(
                    runtime_key="runtime:refresh-4",
                    tenant_id="test-tenant",
                    workspace_files={"/workspace/files/demo.txt": "host-seed"},
                ),
            )

            workspace.ensure(db)
            workspace.write_file(db, "/workspace/files/demo.txt", "host-updated", overwrite=True)

            self.assertTrue(workspace.flush())

            db.expire_all()
            node = db.scalar(
                select(VFSFileNode).where(
                    VFSFileNode.tenant_id == "test-tenant",
                    VFSFileNode.workspace_id == int(workspace_row.id),
                    VFSFileNode.name == "demo.txt",
                )
            )
            self.assertIsNotNone(node)
            self.assertEqual(node.content_text, "host-updated")
            self.assertEqual(int(node.version_no), 2)

    def test_workspace_handle_rejects_different_persistence_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            other_engine = create_engine(
                f"sqlite+pysqlite:///{tmpdir}/other.db",
                future=True,
            )
            Base.metadata.create_all(bind=other_engine)
            OtherSessionLocal = sessionmaker(bind=other_engine, autoflush=False, autocommit=False, class_=Session)
            try:
                with self.SessionLocal() as db:
                    workspace_row = VFSWorkspace(
                        tenant_id="test-tenant",
                        runtime_key="runtime:refresh-bind",
                        metadata_json={},
                    )
                    db.add(workspace_row)
                    db.commit()
                    db.refresh(workspace_row)

                    workspace = create_workspace(
                        workspace=workspace_row,
                        tenant_id="test-tenant",
                        workspace_seed=build_workspace_seed(
                            runtime_key="runtime:refresh-bind",
                            tenant_id="test-tenant",
                            workspace_files={"/workspace/files/demo.txt": "host-seed"},
                        ),
                    )
                    workspace.ensure(db)

                with OtherSessionLocal() as other_db:
                    with self.assertRaisesRegex(ValueError, "workspace handle is bound to persistence target"):
                        workspace.ensure(other_db)
            finally:
                other_engine.dispose()


if __name__ == "__main__":
    unittest.main()
