from __future__ import annotations

import importlib
import unittest
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from iruka_vfs import WritableFileSource, build_profile_dependencies, configure_vfs_dependencies, create_workspace
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
        _reload("iruka_vfs.service_ops.bootstrap")
        _reload("iruka_vfs.service_ops.file_api")
        _reload("iruka_vfs.workspace_mirror")
        _reload("iruka_vfs.service")

        self.engine = create_engine(
            "sqlite+pysqlite://",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, class_=Session)
        self.host_text = {"value": "host-seed"}

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
                runtime_key="runtime:refresh-1",
                primary_file=WritableFileSource(
                    file_id="demo-file:refresh-1",
                    virtual_path="/workspace/files/demo.txt",
                    read_text=lambda: self.host_text["value"],
                    write_text=lambda text: self.host_text.__setitem__("value", text),
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


if __name__ == "__main__":
    unittest.main()
