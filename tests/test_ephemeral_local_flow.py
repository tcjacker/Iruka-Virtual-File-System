from __future__ import annotations

import importlib
import time
import unittest
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from iruka_vfs import build_workspace_seed, create_workspace
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

    def test_host_write_file_requires_overwrite_confirmation(self) -> None:
        with self.SessionLocal() as db:
            workspace_row = VFSWorkspace(
                tenant_id="test-tenant",
                runtime_key="runtime:local-host-overwrite",
                metadata_json={},
            )
            db.add(workspace_row)
            db.commit()
            db.refresh(workspace_row)

            workspace = create_workspace(
                workspace=workspace_row,
                tenant_id="test-tenant",
                workspace_seed=build_workspace_seed(
                    runtime_key="runtime:local-host-overwrite",
                    tenant_id="test-tenant",
                    workspace_files={"/workspace/files/demo.txt": "hello"},
                ),
            )
            workspace.ensure(db)
            conflict = workspace.write_file(db, "/workspace/files/demo.txt", "host-updated")
            self.assertFalse(conflict["ok"])
            self.assertTrue(conflict["conflict"])
            self.assertEqual(conflict["reason"], "already_exists")
            self.assertTrue(conflict["requires_confirmation"])
            self.assertEqual(workspace.read_file(db, "/workspace/files/demo.txt"), "hello")

            written = workspace.write_file(db, "/workspace/files/demo.txt", "host-updated", overwrite=True)
            self.assertTrue(written["ok"])
            self.assertEqual(workspace.read_file(db, "/workspace/files/demo.txt"), "host-updated")

    def test_redirect_requires_force_to_overwrite_existing_file(self) -> None:
        workspace, runtime_seed = self._make_workspace(3021)

        with self.SessionLocal() as db:
            self.bootstrap.ensure_virtual_workspace(db, workspace, runtime_seed, include_tree=False, tenant_id="test-tenant")
            self.access_mode.set_workspace_access_mode(
                db,
                workspace,
                workspace_seed=runtime_seed,
                mode="agent",
                tenant_id="test-tenant",
                flush=False,
            )
            conflict = self.file_api.run_virtual_bash(
                db,
                workspace,
                "echo replaced > /workspace/files/demo.txt",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(conflict["exit_code"], 1)
            self.assertEqual(
                conflict["stderr"],
                "redirect: file already exists: /workspace/files/demo.txt. "
                "To overwrite this exact file, rerun the same command with >| /workspace/files/demo.txt",
            )
            self.assertEqual(conflict["artifacts"]["reason"], "already_exists")
            self.assertTrue(conflict["artifacts"]["requires_confirmation"])
            self.assertEqual(conflict["artifacts"]["suggested_overwrite_mode"], ">|")

            unchanged = self.file_api.run_virtual_bash(
                db,
                workspace,
                "cat /workspace/files/demo.txt",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(unchanged["stdout"], "hello")

            forced = self.file_api.run_virtual_bash(
                db,
                workspace,
                "echo replaced >| /workspace/files/demo.txt",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(forced["exit_code"], 0)
            updated = self.file_api.run_virtual_bash(
                db,
                workspace,
                "cat /workspace/files/demo.txt",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(updated["stdout"], "replaced")

    def test_heredoc_redirect_creates_file(self) -> None:
        workspace, runtime_seed = self._make_workspace(3022)

        with self.SessionLocal() as db:
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
                "mkdir -p /workspace/characters && cat <<'EOF' > /workspace/characters/ch1.md\n# 第一章\n\n（在此处开始撰写小说内容）\nEOF",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 0)

            created = self.file_api.run_virtual_bash(
                db,
                workspace,
                "cat /workspace/characters/ch1.md",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(created["stdout"], "# 第一章\n\n（在此处开始撰写小说内容）")

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
                overwrite=True,
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
            self.assertEqual(
                result["stderr"],
                "patch: require --path. Example: patch --path /workspace/file.txt --find old --replace new",
            )

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
            self.assertEqual(
                result["stderr"],
                "patch: require either --unified or (--find and --replace). "
                "Examples: patch --path /workspace/file.txt --unified '@@ -1,1 +1,1 @@ ...' "
                "or patch --path /workspace/file.txt --find old --replace new",
            )

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
            self.assertEqual(
                result["stderr"],
                "patch: /workspace/files/missing.txt: No such file. Try: find /workspace -name missing.txt -> cat -> edit/patch, or inspect tree",
            )

    def test_cat_missing_file_suggests_next_step(self) -> None:
        with self.SessionLocal() as db:
            workspace, runtime_seed = self._prepare_agent_workspace(db, 321)
            result = self.file_api.run_virtual_bash(
                db,
                workspace,
                "cat /workspace/brief.md",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 1)
            self.assertEqual(
                result["stderr"],
                "cat: /workspace/brief.md: No such file. Try: find /workspace -name brief.md -> cat -> edit/patch, or inspect tree",
            )

    def test_cat_missing_file_suggests_unique_existing_path(self) -> None:
        workspace = VFSWorkspace(
            id=3211,
            tenant_id="test-tenant",
            runtime_key="runtime:e2e-3211",
            metadata_json={},
        )
        runtime_seed = RuntimeSeed(
            runtime_key="runtime:e2e-3211",
            tenant_id="test-tenant",
            workspace_files={"/workspace/docs/brief.md": "hello\n"},
            metadata={},
        )
        with self.SessionLocal() as db:
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
                "cat /workspace/brief.md",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 1)
            self.assertEqual(
                result["stderr"],
                "cat: /workspace/brief.md: No such file. "
                "Most likely existing path: /workspace/docs/brief.md. "
                "Try: cat /workspace/docs/brief.md. "
                "Do not recreate /workspace/brief.md when that exact file already exists elsewhere.",
            )

    def test_edit_requires_find_replace_shows_example(self) -> None:
        with self.SessionLocal() as db:
            workspace, runtime_seed = self._prepare_agent_workspace(db, 322)
            result = self.file_api.run_virtual_bash(
                db,
                workspace,
                "edit /workspace/files/demo.txt --find hello",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 1)
            self.assertEqual(
                result["stderr"],
                "edit: require --find and --replace. Example: edit /workspace/file.txt --find old --replace new",
            )

    def test_edit_supports_unquoted_multiword_find_replace(self) -> None:
        workspace = VFSWorkspace(
            id=323,
            tenant_id="test-tenant",
            runtime_key="runtime:e2e-323",
            metadata_json={},
        )
        runtime_seed = RuntimeSeed(
            runtime_key="runtime:e2e-323",
            tenant_id="test-tenant",
            workspace_files={"/workspace/docs/summary.md": "# Summary\n\nCurrent version: 1.3.0\n"},
            metadata={},
        )
        with self.SessionLocal() as db:
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
                "edit /workspace/docs/summary.md --find Current version: 1.3.0 --replace Current version: 1.4.0",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 0)
            updated = self.file_api.run_virtual_bash(
                db,
                workspace,
                "cat /workspace/docs/summary.md",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(updated["stdout"], "# Summary\n\nCurrent version: 1.4.0")

    def test_patch_supports_unquoted_multiword_find_replace(self) -> None:
        workspace = VFSWorkspace(
            id=324,
            tenant_id="test-tenant",
            runtime_key="runtime:e2e-324",
            metadata_json={},
        )
        runtime_seed = RuntimeSeed(
            runtime_key="runtime:e2e-324",
            tenant_id="test-tenant",
            workspace_files={"/workspace/docs/summary.md": "# Summary\n\nCurrent version: 1.3.0\n"},
            metadata={},
        )
        with self.SessionLocal() as db:
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
                "patch --path /workspace/docs/summary.md --find Current version: 1.3.0 --replace Current version: 1.4.0",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 0)
            updated = self.file_api.run_virtual_bash(
                db,
                workspace,
                "cat /workspace/docs/summary.md",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(updated["stdout"], "# Summary\n\nCurrent version: 1.4.0")

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
            self.assertEqual(result["stderr"], "unsupported command: doesnotexist. Try: help")

    def test_help_command_describes_supported_shell_surface(self) -> None:
        with self.SessionLocal() as db:
            workspace, runtime_seed = self._prepare_agent_workspace(db, 315)
            result = self.file_api.run_virtual_bash(
                db,
                workspace,
                "help",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 0)
            self.assertIn("Supported commands:", result["stdout"])
            self.assertIn("- help", result["stdout"])
            self.assertIn("- find [path] [-type f|d] [-name <glob>]", result["stdout"])
            self.assertIn("- grep [-l|-c|-v] <pattern> [path...]", result["stdout"])
            self.assertIn("- xargs <command> [args...]", result["stdout"])
            self.assertIn("- cp <source> <target>", result["stdout"])
            self.assertIn("- mv <source> <target>", result["stdout"])
            self.assertIn("- rm <file>", result["stdout"])
            self.assertIn("- head [-n <count>] [file...]", result["stdout"])
            self.assertIn("- sort [file...]", result["stdout"])
            self.assertIn(">| overwrites an existing file explicitly", result["stdout"])
            self.assertIn("find /workspace -name <file> -> cat -> edit/patch", result["stdout"])
            self.assertIn("2>/dev/null and restricted || fallbacks (true, :, help)", result["stdout"])
            self.assertEqual(
                result["artifacts"]["supported_commands"],
                [
                    "pwd",
                    "cd",
                    "ls",
                    "cat",
                    "find",
                    "rg",
                    "grep",
                    "wc",
                    "mkdir",
                    "touch",
                    "cp",
                    "mv",
                    "rm",
                    "head",
                    "sort",
                    "basename",
                    "dirname",
                    "edit",
                    "patch",
                    "tree",
                    "xargs",
                    "echo",
                    "help",
                ],
            )

    def test_bash_result_includes_workspace_outline(self) -> None:
        with self.SessionLocal() as db:
            workspace, runtime_seed = self._prepare_agent_workspace(db, 319)
            result = self.file_api.run_virtual_bash(
                db,
                workspace,
                "pwd",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 0)
            self.assertEqual(result["workspace_outline"], "/\n└── workspace/\n    └── files/\n        └── demo.txt")
            self.assertEqual(result["artifacts"]["workspace_outline"], "/\n└── workspace/\n    └── files/\n        └── demo.txt")
            self.assertIn("Known files:\n- /workspace/files/demo.txt", result["workspace_bootstrap"])
            self.assertEqual(result["workspace_bootstrap"], result["artifacts"]["workspace_bootstrap"])
            self.assertIn("find /workspace -name <file>", result["discovery_hint"])
            self.assertIn("Prefer exact known paths from workspace_bootstrap", result["discovery_hint"])
            self.assertEqual(result["discovery_hint"], result["artifacts"]["discovery_hint"])

    def test_find_locates_paths_by_filename(self) -> None:
        with self.SessionLocal() as db:
            workspace, runtime_seed = self._prepare_agent_workspace(db, 320)
            result = self.file_api.run_virtual_bash(
                db,
                workspace,
                "find /workspace -name demo.txt",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 0)
            self.assertEqual(result["stdout"], "/workspace/files/demo.txt")
            self.assertEqual(result["artifacts"]["match_count"], 1)

    def test_xargs_grep_l_finds_matching_files(self) -> None:
        workspace = VFSWorkspace(
            id=325,
            tenant_id="test-tenant",
            runtime_key="runtime:e2e-325",
            metadata_json={},
        )
        runtime_seed = RuntimeSeed(
            runtime_key="runtime:e2e-325",
            tenant_id="test-tenant",
            workspace_files={
                "/workspace/docs/a.md": "TODO: finish\n",
                "/workspace/docs/b.md": "done\n",
            },
            metadata={},
        )
        with self.SessionLocal() as db:
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
                "find /workspace -type f -name '*.md' | xargs grep -l TODO",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 0)
            self.assertEqual(result["stdout"], "/workspace/docs/a.md")

    def test_grep_v_filters_stdin_path_list(self) -> None:
        workspace = VFSWorkspace(
            id=3251,
            tenant_id="test-tenant",
            runtime_key="runtime:e2e-3251",
            metadata_json={},
        )
        runtime_seed = RuntimeSeed(
            runtime_key="runtime:e2e-3251",
            tenant_id="test-tenant",
            workspace_files={
                "/workspace/docs/a.md": "TODO: finish\n",
                "/workspace/.git/config": "ignored\n",
            },
            metadata={},
        )
        with self.SessionLocal() as db:
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
                "find /workspace -type f | grep -v .git",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 0)
            self.assertEqual(result["stdout"], "/workspace/docs/a.md")

    def test_grep_c_counts_matches_per_file(self) -> None:
        workspace = VFSWorkspace(
            id=3252,
            tenant_id="test-tenant",
            runtime_key="runtime:e2e-3252",
            metadata_json={},
        )
        runtime_seed = RuntimeSeed(
            runtime_key="runtime:e2e-3252",
            tenant_id="test-tenant",
            workspace_files={
                "/workspace/docs/a.md": "TODO one\nTODO two\n",
                "/workspace/docs/b.md": "TODO one\n",
                "/workspace/docs/c.txt": "done\n",
            },
            metadata={},
        )
        with self.SessionLocal() as db:
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
                "grep -c TODO /workspace/docs",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 0)
            self.assertEqual(result["stdout"], "/workspace/docs/a.md:2\n/workspace/docs/b.md:1")

    def test_rg_c_counts_matches_per_file(self) -> None:
        workspace = VFSWorkspace(
            id=3253,
            tenant_id="test-tenant",
            runtime_key="runtime:e2e-3253",
            metadata_json={},
        )
        runtime_seed = RuntimeSeed(
            runtime_key="runtime:e2e-3253",
            tenant_id="test-tenant",
            workspace_files={"/workspace/docs/a.md": "TODO one\nTODO two\n"},
            metadata={},
        )
        with self.SessionLocal() as db:
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
                "rg -c TODO /workspace/docs/a.md",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 0)
            self.assertEqual(result["stdout"], "2")

    def test_cp_copies_file_content(self) -> None:
        with self.SessionLocal() as db:
            workspace, runtime_seed = self._prepare_agent_workspace(db, 3254, initial_text="copy-me")
            result = self.file_api.run_virtual_bash(
                db,
                workspace,
                "cp /workspace/files/demo.txt /workspace/files/copy.txt",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 0)
            copied = self.file_api.run_virtual_bash(
                db,
                workspace,
                "cat /workspace/files/copy.txt",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(copied["stdout"], "copy-me")

    def test_mv_renames_file(self) -> None:
        with self.SessionLocal() as db:
            workspace, runtime_seed = self._prepare_agent_workspace(db, 3255, initial_text="move-me")
            result = self.file_api.run_virtual_bash(
                db,
                workspace,
                "mv /workspace/files/demo.txt /workspace/files/renamed.txt",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 0)
            missing = self.file_api.run_virtual_bash(
                db,
                workspace,
                "cat /workspace/files/demo.txt",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(missing["exit_code"], 1)
            renamed = self.file_api.run_virtual_bash(
                db,
                workspace,
                "cat /workspace/files/renamed.txt",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(renamed["stdout"], "move-me")

    def test_rm_removes_single_file(self) -> None:
        with self.SessionLocal() as db:
            workspace, runtime_seed = self._prepare_agent_workspace(db, 3256, initial_text="remove-me")
            result = self.file_api.run_virtual_bash(
                db,
                workspace,
                "rm /workspace/files/demo.txt",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 0)
            missing = self.file_api.run_virtual_bash(
                db,
                workspace,
                "cat /workspace/files/demo.txt",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(missing["exit_code"], 1)

    def test_sort_sorts_pipeline_input(self) -> None:
        workspace = VFSWorkspace(
            id=3257,
            tenant_id="test-tenant",
            runtime_key="runtime:e2e-3257",
            metadata_json={},
        )
        runtime_seed = RuntimeSeed(
            runtime_key="runtime:e2e-3257",
            tenant_id="test-tenant",
            workspace_files={"/workspace/files/demo.txt": "zeta\nalpha\nbeta\n"},
            metadata={},
        )
        with self.SessionLocal() as db:
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
                "cat /workspace/files/demo.txt | sort",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 0)
            self.assertEqual(result["stdout"], "alpha\nbeta\nzeta")

    def test_head_reads_first_lines_from_file(self) -> None:
        workspace = VFSWorkspace(
            id=32571,
            tenant_id="test-tenant",
            runtime_key="runtime:e2e-32571",
            metadata_json={},
        )
        runtime_seed = RuntimeSeed(
            runtime_key="runtime:e2e-32571",
            tenant_id="test-tenant",
            workspace_files={"/workspace/files/demo.txt": "one\ntwo\nthree\nfour\n"},
            metadata={},
        )
        with self.SessionLocal() as db:
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
                "head -n 2 /workspace/files/demo.txt",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 0)
            self.assertEqual(result["stdout"], "one\ntwo")

    def test_head_reads_first_lines_from_pipeline(self) -> None:
        workspace = VFSWorkspace(
            id=32572,
            tenant_id="test-tenant",
            runtime_key="runtime:e2e-32572",
            metadata_json={},
        )
        runtime_seed = RuntimeSeed(
            runtime_key="runtime:e2e-32572",
            tenant_id="test-tenant",
            workspace_files={"/workspace/files/demo.txt": "one\ntwo\nthree\nfour\n"},
            metadata={},
        )
        with self.SessionLocal() as db:
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
                "cat /workspace/files/demo.txt | head -n 3",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 0)
            self.assertEqual(result["stdout"], "one\ntwo\nthree")

    def test_sort_sorts_file_lines(self) -> None:
        workspace = VFSWorkspace(
            id=3258,
            tenant_id="test-tenant",
            runtime_key="runtime:e2e-3258",
            metadata_json={},
        )
        runtime_seed = RuntimeSeed(
            runtime_key="runtime:e2e-3258",
            tenant_id="test-tenant",
            workspace_files={"/workspace/files/demo.txt": "zeta\nalpha\nbeta\n"},
            metadata={},
        )
        with self.SessionLocal() as db:
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
                "sort /workspace/files/demo.txt",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 0)
            self.assertEqual(result["stdout"], "alpha\nbeta\nzeta")

    def test_basename_and_dirname_work(self) -> None:
        with self.SessionLocal() as db:
            workspace, runtime_seed = self._prepare_agent_workspace(db, 3259)
            base = self.file_api.run_virtual_bash(
                db,
                workspace,
                "basename /workspace/files/demo.txt",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            directory = self.file_api.run_virtual_bash(
                db,
                workspace,
                "dirname /workspace/files/demo.txt",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(base["stdout"], "demo.txt")
            self.assertEqual(directory["stdout"], "/workspace/files")

    def test_find_exec_grep_l_finds_matching_files(self) -> None:
        workspace = VFSWorkspace(
            id=326,
            tenant_id="test-tenant",
            runtime_key="runtime:e2e-326",
            metadata_json={},
        )
        runtime_seed = RuntimeSeed(
            runtime_key="runtime:e2e-326",
            tenant_id="test-tenant",
            workspace_files={
                "/workspace/docs/a.md": "TODO: finish\n",
                "/workspace/docs/b.md": "done\n",
            },
            metadata={},
        )
        with self.SessionLocal() as db:
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
                "find /workspace -type f -exec grep -l TODO {} \\;",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 0)
            self.assertEqual(result["stdout"], "/workspace/docs/a.md")

    def test_ls_long_format_shows_file_types(self) -> None:
        with self.SessionLocal() as db:
            workspace, runtime_seed = self._prepare_agent_workspace(db, 316)
            result = self.file_api.run_virtual_bash(
                db,
                workspace,
                "ls -l /workspace",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 0)
            self.assertIn("dir  size=0 version=1 mtime=", result["stdout"])
            self.assertIn("files/", result["stdout"])
            self.assertEqual(result["artifacts"]["flags"], ["-l"])

    def test_ls_la_long_format_shows_file_entries(self) -> None:
        with self.SessionLocal() as db:
            workspace, runtime_seed = self._prepare_agent_workspace(db, 318)
            result = self.file_api.run_virtual_bash(
                db,
                workspace,
                "ls -la /workspace/files",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 0)
            self.assertIn("file size=5 version=1 mtime=", result["stdout"])
            self.assertIn("demo.txt", result["stdout"])
            self.assertEqual(result["artifacts"]["flags"], ["-la"])

    def test_ls_rejects_unknown_option_explicitly(self) -> None:
        with self.SessionLocal() as db:
            workspace, runtime_seed = self._prepare_agent_workspace(db, 317)
            result = self.file_api.run_virtual_bash(
                db,
                workspace,
                "ls -R /workspace/files",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 1)
            self.assertEqual(
                result["stderr"],
                "ls: unsupported option: -R. Try: tree for recursion or find /workspace -name <file>",
            )

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

    def test_stderr_devnull_and_or_true_are_supported_as_limited_tails(self) -> None:
        with self.SessionLocal() as db:
            workspace, runtime_seed = self._prepare_agent_workspace(db, 3101)
            result = self.file_api.run_virtual_bash(
                db,
                workspace,
                "cat /workspace/files/missing.txt 2>/dev/null || true",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 0)
            self.assertEqual(result["stdout"], "")
            self.assertEqual(result["stderr"], "")
            self.assertTrue(result["artifacts"]["ignored_error"])
            self.assertEqual(result["artifacts"]["or_fallback"], ["true"])

    def test_here_string_feeds_stdin_for_search_commands(self) -> None:
        with self.SessionLocal() as db:
            workspace, runtime_seed = self._prepare_agent_workspace(db, 31011)
            result = self.file_api.run_virtual_bash(
                db,
                workspace,
                "grep beta <<< 'alpha beta gamma'",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 0)
            self.assertEqual(result["stdout"], "alpha beta gamma")

    def test_here_string_reports_actionable_error_for_command_substitution(self) -> None:
        with self.SessionLocal() as db:
            workspace, runtime_seed = self._prepare_agent_workspace(db, 31012)
            result = self.file_api.run_virtual_bash(
                db,
                workspace,
                "grep demo <<< $(cat /workspace/files/demo.txt)",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 2)
            self.assertEqual(
                result["stderr"],
                "parse error: here-string command substitution is not supported. "
                "Use `cat <file> | <command>`, `echo <text> | <command>`, or `cat <<'EOF' | <command>` instead.",
            )

    def test_stderr_devnull_suppresses_error_output(self) -> None:
        with self.SessionLocal() as db:
            workspace, runtime_seed = self._prepare_agent_workspace(db, 3102)
            result = self.file_api.run_virtual_bash(
                db,
                workspace,
                "cat /workspace/files/missing.txt 2>/dev/null",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 1)
            self.assertEqual(result["stderr"], "")

    def test_or_colon_is_supported_as_noop_fallback(self) -> None:
        with self.SessionLocal() as db:
            workspace, runtime_seed = self._prepare_agent_workspace(db, 31021)
            result = self.file_api.run_virtual_bash(
                db,
                workspace,
                "cat /workspace/files/missing.txt || :",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 0)
            self.assertEqual(
                result["stderr"],
                "cat: /workspace/files/missing.txt: No such file. Try: find /workspace -name missing.txt -> cat -> edit/patch, or inspect tree",
            )
            self.assertEqual(result["artifacts"]["or_fallback"], [":"])

    def test_or_help_is_supported_as_guided_fallback(self) -> None:
        with self.SessionLocal() as db:
            workspace, runtime_seed = self._prepare_agent_workspace(db, 31022)
            result = self.file_api.run_virtual_bash(
                db,
                workspace,
                "doesnotexist || help",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 0)
            self.assertIn("unsupported command: doesnotexist. Try: help", result["stderr"])
            self.assertIn("Supported commands:", result["stdout"])
            self.assertEqual(result["artifacts"]["or_fallback"], ["help"])

    def test_parse_error_for_other_or_or_forms_is_actionable(self) -> None:
        with self.SessionLocal() as db:
            workspace, runtime_seed = self._prepare_agent_workspace(db, 3103)
            result = self.file_api.run_virtual_bash(
                db,
                workspace,
                "find /workspace -type f || false",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 2)
            self.assertEqual(
                result["stderr"],
                "parse error: unsupported `|| false` fallback. "
                "Supported forms are `|| true`, `|| :`, and `|| help`. "
                "Otherwise remove the `|| ...` tail and run the main command directly, or use && / ; explicitly.",
            )

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

    def test_redirect_rejects_ambiguous_root_level_create_target(self) -> None:
        workspace = VFSWorkspace(
            id=327,
            tenant_id="test-tenant",
            runtime_key="runtime:e2e-327",
            metadata_json={},
        )
        runtime_seed = RuntimeSeed(
            runtime_key="runtime:e2e-327",
            tenant_id="test-tenant",
            workspace_files={"/workspace/docs/release_notes.md": "# Release Notes\n\nOld content.\n"},
            metadata={},
        )
        with self.SessionLocal() as db:
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
                "cat <<'EOF' > /workspace/release_notes.md\n# Release Notes\n\nUpdated content.\nEOF",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(result["exit_code"], 1)
            self.assertEqual(
                result["stderr"],
                "redirect: /workspace/release_notes.md would create a new root-level file, "
                "but an existing file with the same name was found at /workspace/docs/release_notes.md. "
                "Use the existing path instead.",
            )
            existing = self.file_api.run_virtual_bash(
                db,
                workspace,
                "cat /workspace/docs/release_notes.md",
                workspace_seed=runtime_seed,
                tenant_id="test-tenant",
            )
            self.assertEqual(existing["stdout"], "# Release Notes\n\nOld content.")

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
