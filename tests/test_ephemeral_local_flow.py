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


def _reload(module_name: str):
    module = importlib.import_module(module_name)
    return importlib.reload(module)


def _configure_ephemeral_local() -> None:
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
            runtime_profile="ephemeral-local",
        )
    )


class EphemeralLocalFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        _configure_ephemeral_local()
        self.dependency_resolution = _reload("iruka_vfs.dependency_resolution")
        self.service_state = _reload("iruka_vfs.service_ops.state")
        _reload("iruka_vfs.models")
        _reload("iruka_vfs.pathing.resolution")
        _reload("iruka_vfs.runtime.filesystem")
        _reload("iruka_vfs.runtime.executor")
        _reload("iruka_vfs.runtime.fs_commands")
        _reload("iruka_vfs.runtime.search")
        _reload("iruka_vfs.runtime")
        _reload("iruka_vfs.command_runtime")
        _reload("iruka_vfs.integrations.agent.shell")
        self.bootstrap = _reload("iruka_vfs.service_ops.bootstrap")
        self.access_mode = _reload("iruka_vfs.service_ops.access_mode")
        self.file_api = _reload("iruka_vfs.service_ops.file_api")
        self.workspace_mirror = _reload("iruka_vfs.workspace_mirror")
        self.checkpoint = _reload("iruka_vfs.mirror.checkpoint")
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

    def _make_workspace(self, workspace_id: int, *, initial_text: str = "hello") -> tuple[VFSWorkspace, RuntimeSeed]:
        workspace = VFSWorkspace(
            id=workspace_id,
            tenant_id="test-tenant",
            runtime_key=f"runtime:e2e-{workspace_id}",
            metadata_json={},
        )
        runtime_seed = RuntimeSeed(
            runtime_key=f"runtime:e2e-{workspace_id}",
            tenant_id="test-tenant",
            workspace_files={"/workspace/files/demo.txt": initial_text},
            metadata={},
        )
        return workspace, runtime_seed

    def _prepare_agent_workspace(self, db: Session, workspace_id: int, *, initial_text: str = "hello") -> tuple[VFSWorkspace, RuntimeSeed]:
        workspace, runtime_seed = self._make_workspace(workspace_id, initial_text=initial_text)
        self.bootstrap.ensure_virtual_workspace(db, workspace, runtime_seed, include_tree=False, tenant_id="test-tenant")
        self.access_mode.set_workspace_access_mode(
            db,
            workspace,
            workspace_seed=runtime_seed,
            mode="agent",
            tenant_id="test-tenant",
            flush=False,
        )
        return workspace, runtime_seed

    def test_ephemeral_local_flow_bootstrap_edit_and_command_logging(self) -> None:
        workspace, runtime_seed = self._make_workspace(301)

        with self.SessionLocal() as db:
            scope_key = self.workspace_mirror.workspace_scope_for_db(db)
            snapshot = self.bootstrap.ensure_virtual_workspace(
                db,
                workspace,
                runtime_seed,
                include_tree=False,
                tenant_id="test-tenant",
            )
            self.assertEqual(snapshot["workspace_id"], 301)

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
                "edit /workspace/files/demo.txt --find hello --replace hello-world",
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
            self.assertEqual(third["stdout"], "hello-world")

        store = self.service_state.get_workspace_state_store()
        mirror = store.get_workspace_mirror(301, tenant_key="test-tenant", scope_key=scope_key)
        self.assertIsNotNone(mirror)
        file_node_id = mirror.path_to_id["/workspace/files/demo.txt"]
        file_node = mirror.nodes[file_node_id]
        self.assertEqual(file_node.content_text, "hello-world")
        self.assertEqual(int(file_node.version_no), 2)
        self.assertEqual(int(mirror.cwd_node_id), int(mirror.path_to_id["/workspace/files"]))
        self.assertIn(int(file_node_id), mirror.dirty_content_node_ids)

        runtime_state = importlib.import_module("iruka_vfs.runtime_state")
        runtime_state.workspace_checkpoint_session_maker = self.SessionLocal
        workspace_ref = store.workspace_ref(mirror=mirror)
        self.assertTrue(self.checkpoint.flush_workspace_mirror(None, workspace_ref=workspace_ref))

        refreshed_mirror = store.get_workspace_mirror(301, tenant_key="test-tenant", scope_key=scope_key)
        self.assertIsNotNone(refreshed_mirror)
        self.assertFalse(bool(refreshed_mirror.dirty_content_node_ids))
        repositories = self.dependency_resolution.resolve_vfs_repositories()
        persisted_node = repositories.node.get_node(None, int(file_node_id), "test-tenant")
        self.assertIsNotNone(persisted_node)
        self.assertEqual(persisted_node.content_text, "hello-world")
        self.assertEqual(int(persisted_node.version_no), 2)

        logs = self._wait_for_command_logs(expected_count=3)
        self.assertEqual(len(logs), 3)
        self.assertEqual(
            [log["raw_cmd"] for log in logs],
            [
                "cd /workspace/files && pwd && ls && cat demo.txt",
                "edit /workspace/files/demo.txt --find hello --replace hello-world",
                "cat /workspace/files/demo.txt",
            ],
        )
        self.assertEqual(logs[-1]["stdout_text"], "hello-world")

    def test_patch_find_replace_updates_content_and_version(self) -> None:
        workspace, runtime_seed = self._make_workspace(302)

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
            result = self.file_api.run_virtual_bash(
                db,
                workspace,
                "patch --path /workspace/files/demo.txt --find hello --replace patched",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 0)
            self.assertIn("patch applied to /workspace/files/demo.txt -> version 2", result["stdout"])

        mirror = self.service_state.get_workspace_state_store().get_workspace_mirror(
            302,
            tenant_key="test-tenant",
            scope_key=scope_key,
        )
        self.assertIsNotNone(mirror)
        file_node = mirror.nodes[mirror.path_to_id["/workspace/files/demo.txt"]]
        self.assertEqual(file_node.content_text, "patched")
        self.assertEqual(int(file_node.version_no), 2)

    def test_patch_unified_conflict_keeps_original_content(self) -> None:
        workspace, runtime_seed = self._make_workspace(303)

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
            conflict_patch = "@@ -1,1 +1,1 @@\n-wrong\n+patched"
            result = self.file_api.run_virtual_bash(
                db,
                workspace,
                f"patch --path /workspace/files/demo.txt --unified '{conflict_patch}'",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 1)
            self.assertIn("patch: rejected hunks:", result["stderr"])

        mirror = self.service_state.get_workspace_state_store().get_workspace_mirror(
            303,
            tenant_key="test-tenant",
            scope_key=scope_key,
        )
        self.assertIsNotNone(mirror)
        file_node = mirror.nodes[mirror.path_to_id["/workspace/files/demo.txt"]]
        self.assertEqual(file_node.content_text, "hello")
        self.assertEqual(int(file_node.version_no), 1)

    def test_flush_remaps_nested_temp_nodes_to_real_parent_ids(self) -> None:
        workspace, runtime_seed = self._make_workspace(304, initial_text="seed")

        with self.SessionLocal() as db:
            self.bootstrap.ensure_virtual_workspace(db, workspace, runtime_seed, include_tree=False, tenant_id="test-tenant")
            self.file_api.write_workspace_file(
                db,
                workspace,
                "/workspace/generated/deep/file.txt",
                "nested",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            scope_key = self.workspace_mirror.workspace_scope_for_db(db)
            store = self.service_state.get_workspace_state_store()
            mirror = store.get_workspace_mirror(304, tenant_key="test-tenant", scope_key=scope_key)
            self.assertIsNotNone(mirror)

            runtime_state = importlib.import_module("iruka_vfs.runtime_state")
            runtime_state.workspace_checkpoint_session_maker = self.SessionLocal
            self.assertTrue(self.checkpoint.flush_workspace_mirror(None, workspace_ref=store.workspace_ref(mirror=mirror)))

            refreshed = store.get_workspace_mirror(304, tenant_key="test-tenant", scope_key=scope_key)
            self.assertIsNotNone(refreshed)
            dir_node_id = refreshed.path_to_id["/workspace/generated/deep"]
            file_node_id = refreshed.path_to_id["/workspace/generated/deep/file.txt"]
            self.assertGreater(int(dir_node_id), 0)
            self.assertGreater(int(file_node_id), 0)
            self.assertEqual(int(refreshed.nodes[file_node_id].parent_id), int(dir_node_id))

    def test_ensure_does_not_overwrite_persisted_seeded_file_content(self) -> None:
        workspace, runtime_seed = self._make_workspace(305, initial_text="hello")

        with self.SessionLocal() as db:
            self.bootstrap.ensure_virtual_workspace(db, workspace, runtime_seed, include_tree=False, tenant_id="test-tenant")
            self.file_api.write_workspace_file(
                db,
                workspace,
                "/workspace/files/demo.txt",
                "host-updated",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            scope_key = self.workspace_mirror.workspace_scope_for_db(db)
            store = self.service_state.get_workspace_state_store()
            mirror = store.get_workspace_mirror(305, tenant_key="test-tenant", scope_key=scope_key)

            runtime_state = importlib.import_module("iruka_vfs.runtime_state")
            runtime_state.workspace_checkpoint_session_maker = self.SessionLocal
            self.assertTrue(self.checkpoint.flush_workspace_mirror(None, workspace_ref=store.workspace_ref(mirror=mirror)))

            store.delete_workspace_mirror(305, tenant_key="test-tenant", scope_key=scope_key)
            self.service_state.clear_cached_workspace_state(scope_key, 305)

            self.bootstrap.ensure_virtual_workspace(db, workspace, runtime_seed, include_tree=False, tenant_id="test-tenant")
            content = self.file_api.read_workspace_file(
                db,
                workspace,
                "/workspace/files/demo.txt",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(content, "host-updated")

    def test_patch_requires_path(self) -> None:
        with self.SessionLocal() as db:
            workspace, runtime_seed = self._prepare_agent_workspace(db, 305)
            result = self.file_api.run_virtual_bash(
                db,
                workspace,
                "patch",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 1)
            self.assertEqual(result["stderr"], "patch: require --path")

    def test_patch_requires_complete_replace_args(self) -> None:
        with self.SessionLocal() as db:
            workspace, runtime_seed = self._prepare_agent_workspace(db, 306)
            result = self.file_api.run_virtual_bash(
                db,
                workspace,
                "patch --path /workspace/files/demo.txt --find hello",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 1)
            self.assertEqual(result["stderr"], "patch: require either --unified or (--find and --replace)")

    def test_patch_fails_when_file_is_missing(self) -> None:
        with self.SessionLocal() as db:
            workspace, runtime_seed = self._prepare_agent_workspace(db, 307)
            result = self.file_api.run_virtual_bash(
                db,
                workspace,
                "patch --path /workspace/files/missing.txt --find hello --replace patched",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 1)
            self.assertEqual(result["stderr"], "patch: file not found: /workspace/files/missing.txt")

    def test_patch_fails_when_target_text_is_missing(self) -> None:
        with self.SessionLocal() as db:
            scope_key = self.workspace_mirror.workspace_scope_for_db(db)
            workspace, runtime_seed = self._prepare_agent_workspace(db, 308)
            result = self.file_api.run_virtual_bash(
                db,
                workspace,
                "patch --path /workspace/files/demo.txt --find absent --replace patched",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 1)
            self.assertEqual(result["stderr"], "patch: target text not found")

        mirror = self.service_state.get_workspace_state_store().get_workspace_mirror(
            308,
            tenant_key="test-tenant",
            scope_key=scope_key,
        )
        self.assertIsNotNone(mirror)
        file_node = mirror.nodes[mirror.path_to_id["/workspace/files/demo.txt"]]
        self.assertEqual(file_node.content_text, "hello")
        self.assertEqual(int(file_node.version_no), 1)

    def test_run_virtual_bash_requires_agent_mode(self) -> None:
        workspace, runtime_seed = self._make_workspace(304)

        with self.SessionLocal() as db:
            self.bootstrap.ensure_virtual_workspace(db, workspace, runtime_seed, include_tree=False, tenant_id="test-tenant")
            with self.assertRaises(PermissionError):
                self.file_api.run_virtual_bash(
                    db,
                    workspace,
                    "cat /workspace/files/demo.txt",
                    workspace_seed=runtime_seed,
                    tenant_id="test-tenant",
                )

    def test_host_read_is_allowed_in_agent_mode_but_host_write_is_not(self) -> None:
        with self.SessionLocal() as db:
            workspace, runtime_seed = self._prepare_agent_workspace(db, 315, initial_text="hello-agent")

            content = self.file_api.read_workspace_file(
                db,
                workspace,
                "/workspace/files/demo.txt",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            directory = self.file_api.read_workspace_directory(
                db,
                workspace,
                "/workspace/files",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )

            self.assertEqual(content, "hello-agent")
            self.assertIn("/workspace/files/demo.txt", directory)

            with self.assertRaises(PermissionError):
                self.file_api.write_workspace_file(
                    db,
                    workspace,
                    "/workspace/files/demo.txt",
                    "host-write",
                    workspace_seed=runtime_seed,
                    tenant_id="test-tenant",
                )

    def test_high_level_flush_workspace_uses_active_scope(self) -> None:
        with self.SessionLocal() as db:
            scope_key = self.workspace_mirror.workspace_scope_for_db(db)
            workspace, runtime_seed = self._prepare_agent_workspace(db, 314)
            self.file_api.run_virtual_bash(
                db,
                workspace,
                "edit /workspace/files/demo.txt --find hello --replace flushed",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            mirror = self.service_state.get_workspace_state_store().get_workspace_mirror(
                314,
                tenant_key="test-tenant",
                scope_key=scope_key,
            )
            self.assertIsNotNone(mirror)
            self.assertTrue(bool(mirror.dirty_content_node_ids))
            runtime_state = importlib.import_module("iruka_vfs.runtime_state")
            runtime_state.workspace_checkpoint_session_maker = self.SessionLocal
            self.workspace_mirror.set_active_workspace_scope(scope_key)
            try:
                self.assertTrue(self.file_api.flush_workspace(314, tenant_id="test-tenant"))
            finally:
                self.workspace_mirror.set_active_workspace_scope(None)

        refreshed = self.service_state.get_workspace_state_store().get_workspace_mirror(
            314,
            tenant_key="test-tenant",
            scope_key=scope_key,
        )
        self.assertIsNotNone(refreshed)
        self.assertFalse(bool(refreshed.dirty_content_node_ids))

    def test_unsupported_command_returns_127(self) -> None:
        with self.SessionLocal() as db:
            workspace, runtime_seed = self._prepare_agent_workspace(db, 309)
            result = self.file_api.run_virtual_bash(
                db,
                workspace,
                "doesnotexist arg1",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 127)
            self.assertEqual(result["stderr"], "unsupported command: doesnotexist")

    def test_parse_error_is_returned_for_missing_redirect_target(self) -> None:
        with self.SessionLocal() as db:
            workspace, runtime_seed = self._prepare_agent_workspace(db, 310)
            result = self.file_api.run_virtual_bash(
                db,
                workspace,
                "echo hello >",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 2)
            self.assertEqual(result["stderr"], "parse error: redirect target is missing")

    def test_redirect_fails_when_target_is_directory(self) -> None:
        with self.SessionLocal() as db:
            workspace, runtime_seed = self._prepare_agent_workspace(db, 311)
            result = self.file_api.run_virtual_bash(
                db,
                workspace,
                "echo hello > /workspace/files",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 1)
            self.assertEqual(result["stderr"], "redirect: /workspace/files: is a directory")

    def test_redirect_fails_for_invalid_parent_path(self) -> None:
        with self.SessionLocal() as db:
            workspace, runtime_seed = self._prepare_agent_workspace(db, 312)
            result = self.file_api.run_virtual_bash(
                db,
                workspace,
                "echo hello > /workspace/missing/demo.txt",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 1)
            self.assertEqual(
                result["stderr"],
                "redirect: cannot create /workspace/missing/demo.txt: invalid parent path",
            )

    def test_redirect_fails_outside_workspace(self) -> None:
        with self.SessionLocal() as db:
            workspace, runtime_seed = self._prepare_agent_workspace(db, 313)
            result = self.file_api.run_virtual_bash(
                db,
                workspace,
                "echo hello > /tmp/out.txt",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 1)
            self.assertEqual(
                result["stderr"],
                "redirect: cannot create /tmp/out.txt: invalid parent path",
            )


if __name__ == "__main__":
    unittest.main()
