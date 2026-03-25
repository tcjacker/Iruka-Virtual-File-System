from __future__ import annotations

import importlib
import time
import unittest
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from iruka_vfs.dependencies import VFSDependencies, configure_vfs_dependencies
from iruka_vfs.runtime_seed import RuntimeSeed
from iruka_vfs.sqlalchemy_models import Base, VFSFileNode, VFSShellCommand, VFSShellSession, VFSWorkspace
from tests.support import InMemoryRedis


def _reload(module_name: str):
    module = importlib.import_module(module_name)
    return importlib.reload(module)


def _configure_ephemeral_redis() -> None:
    configure_vfs_dependencies(
        VFSDependencies(
            settings=SimpleNamespace(
                default_tenant_id="test-tenant",
                redis_key_namespace="test",
                redis_url="redis://localhost:6379/0",
                database_url="sqlite://",
            ),
            AgentWorkspace=VFSWorkspace,
            VirtualFileNode=VFSFileNode,
            VirtualShellCommand=VFSShellCommand,
            VirtualShellSession=VFSShellSession,
            load_project_state_payload=lambda *args, **kwargs: {},
            runtime_profile="ephemeral-redis",
        )
    )


class EphemeralRedisFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        _configure_ephemeral_redis()
        self.redis_client = InMemoryRedis()
        self.dependency_resolution = _reload("iruka_vfs.dependency_resolution")
        self.service_state = _reload("iruka_vfs.service_ops.state")
        _reload("iruka_vfs.models")
        _reload("iruka_vfs.pathing.resolution")
        _reload("iruka_vfs.runtime.filesystem")
        self.bootstrap = _reload("iruka_vfs.service_ops.bootstrap")
        self.access_mode = _reload("iruka_vfs.service_ops.access_mode")
        self.file_api = _reload("iruka_vfs.service_ops.file_api")
        self.workspace_mirror = _reload("iruka_vfs.workspace_mirror")
        self.checkpoint = _reload("iruka_vfs.mirror.checkpoint")
        self.service = _reload("iruka_vfs.service")
        self.service._redis_client = self.redis_client

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

    def _wait_for_command_logs(self, expected_count: int, timeout_seconds: float = 2.0) -> list[dict]:
        repositories = self.dependency_resolution.resolve_vfs_repositories()
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            logs = list(repositories.command_log.state.command_logs.values())
            if len(logs) >= expected_count:
                logs.sort(key=lambda item: int(item["id"]))
                return logs
            time.sleep(0.05)
        logs = list(repositories.command_log.state.command_logs.values())
        logs.sort(key=lambda item: int(item["id"]))
        return logs

    def test_ephemeral_redis_flow_bootstrap_edit_and_command_logging(self) -> None:
        workspace = VFSWorkspace(id=401, tenant_id="test-tenant", runtime_key="runtime:e2e-401", metadata_json={})
        runtime_seed = RuntimeSeed(
            runtime_key="runtime:e2e-401",
            tenant_id="test-tenant",
            workspace_files={"/workspace/files/demo.txt": "hello"},
            metadata={},
        )

        with self.SessionLocal() as db:
            scope_key = self.workspace_mirror.workspace_scope_for_db(db)
            snapshot = self.bootstrap.ensure_virtual_workspace(
                db,
                workspace,
                runtime_seed,
                include_tree=False,
                tenant_id="test-tenant",
            )
            self.assertEqual(snapshot["workspace_id"], 401)

            mode = self.access_mode.set_workspace_access_mode(
                db,
                workspace,
                workspace_seed=runtime_seed,
                mode="agent",
                tenant_id="test-tenant",
                flush=False,
            )
            self.assertEqual(mode, "agent")

            first = self.file_api.run_virtual_bash(
                db,
                workspace,
                "cd /workspace/files && pwd && ls && cat demo.txt",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(first["exit_code"], 0)
            self.assertEqual(first["cwd"], "/workspace/files")
            self.assertEqual(first["stdout"], "/workspace/files\ndemo.txt\nhello")

            second = self.file_api.run_virtual_bash(
                db,
                workspace,
                "edit /workspace/files/demo.txt --find hello --replace hello-redis",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(second["exit_code"], 0)
            self.assertIn("edited 1 occurrence(s)", second["stdout"])

            third = self.file_api.run_virtual_bash(
                db,
                workspace,
                "cat /workspace/files/demo.txt",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(third["exit_code"], 0)
            self.assertEqual(third["stdout"], "hello-redis")

        store = self.service_state.get_workspace_state_store()
        self.assertEqual(type(store).__name__, "RedisWorkspaceStateStore")
        mirror = store.get_workspace_mirror(401, tenant_key="test-tenant", scope_key=scope_key)
        self.assertIsNotNone(mirror)
        file_node_id = mirror.path_to_id["/workspace/files/demo.txt"]
        file_node = mirror.nodes[file_node_id]
        self.assertEqual(file_node.content_text, "hello-redis")
        self.assertEqual(int(file_node.version_no), 2)
        self.assertEqual(int(mirror.cwd_node_id), int(mirror.path_to_id["/workspace/files"]))
        self.assertIn(int(file_node_id), mirror.dirty_content_node_ids)
        self.assertTrue(bool(self.redis_client.store))

        runtime_state = importlib.import_module("iruka_vfs.runtime_state")
        runtime_state.workspace_checkpoint_session_maker = self.SessionLocal
        workspace_ref = store.workspace_ref(mirror=mirror)
        self.assertTrue(self.checkpoint.flush_workspace_mirror(None, workspace_ref=workspace_ref))

        refreshed_mirror = store.get_workspace_mirror(401, tenant_key="test-tenant", scope_key=scope_key)
        self.assertIsNotNone(refreshed_mirror)
        self.assertFalse(bool(refreshed_mirror.dirty_content_node_ids))
        repositories = self.dependency_resolution.resolve_vfs_repositories()
        persisted_node = repositories.node.get_node(None, int(file_node_id), "test-tenant")
        self.assertIsNotNone(persisted_node)
        self.assertEqual(persisted_node.content_text, "hello-redis")
        self.assertEqual(int(persisted_node.version_no), 2)

        logs = self._wait_for_command_logs(expected_count=3)
        self.assertEqual(len(logs), 3)
        self.assertEqual(
            [log["raw_cmd"] for log in logs],
            [
                "cd /workspace/files && pwd && ls && cat demo.txt",
                "edit /workspace/files/demo.txt --find hello --replace hello-redis",
                "cat /workspace/files/demo.txt",
            ],
        )
        self.assertEqual(logs[-1]["stdout_text"], "hello-redis")

    def test_high_level_flush_workspace_uses_active_scope(self) -> None:
        workspace = VFSWorkspace(id=402, tenant_id="test-tenant", runtime_key="runtime:e2e-402", metadata_json={})
        runtime_seed = RuntimeSeed(
            runtime_key="runtime:e2e-402",
            tenant_id="test-tenant",
            workspace_files={"/workspace/files/demo.txt": "hello"},
            metadata={},
        )

        with self.SessionLocal() as db:
            scope_key = self.workspace_mirror.workspace_scope_for_db(db)
            self.bootstrap.ensure_virtual_workspace(db, workspace, runtime_seed, include_tree=False, tenant_id="test-tenant")
            self.access_mode.set_workspace_access_mode(
                db,
                workspace,
                workspace_seed=runtime_seed,
                mode="agent",
                tenant_id="test-tenant",
                flush=False,
            )
            self.file_api.run_virtual_bash(
                db,
                workspace,
                "edit /workspace/files/demo.txt --find hello --replace flushed-redis",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            mirror = self.service_state.get_workspace_state_store().get_workspace_mirror(
                402,
                tenant_key="test-tenant",
                scope_key=scope_key,
            )
            self.assertIsNotNone(mirror)
            self.assertTrue(bool(mirror.dirty_content_node_ids))
            runtime_state = importlib.import_module("iruka_vfs.runtime_state")
            runtime_state.workspace_checkpoint_session_maker = self.SessionLocal
            self.workspace_mirror.set_active_workspace_scope(scope_key)
            try:
                self.assertTrue(self.file_api.flush_workspace(402, tenant_id="test-tenant"))
            finally:
                self.workspace_mirror.set_active_workspace_scope(None)

        refreshed = self.service_state.get_workspace_state_store().get_workspace_mirror(
            402,
            tenant_key="test-tenant",
            scope_key=scope_key,
        )
        self.assertIsNotNone(refreshed)
        self.assertFalse(bool(refreshed.dirty_content_node_ids))

    def test_redis_checkpoint_queue_round_trips_workspace_ref(self) -> None:
        store = self.service_state.get_workspace_state_store()
        workspace_ref = store.workspace_ref(
            workspace_id=999,
            tenant_key="test-tenant",
            scope_key="scope-1",
        )
        store.enqueue_workspace_checkpoint(workspace_ref, due_at=123.0)
        popped = store.pop_checkpoint(timeout_seconds=1)
        self.assertEqual(popped, workspace_ref)


if __name__ == "__main__":
    unittest.main()
