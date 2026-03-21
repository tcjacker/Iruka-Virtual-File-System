from __future__ import annotations

import importlib
import unittest
from types import SimpleNamespace

from iruka_vfs.dependencies import VFSDependencies, configure_vfs_dependencies
from iruka_vfs.in_memory_repositories import (
    InMemoryCommandLogRepository,
    InMemoryNodeRepository,
    InMemorySessionRepository,
    InMemoryWorkspaceRepository,
    build_in_memory_repositories,
)
from tests.support import (
    DummyVirtualFileNode,
    DummyVirtualShellCommand,
    DummyVirtualShellSession,
    DummyWorkspace,
)


class InMemoryRepositoriesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dependencies = VFSDependencies(
            settings=SimpleNamespace(
                default_tenant_id="test-tenant",
                redis_key_namespace="test",
                redis_url="redis://localhost:6379/0",
                database_url="sqlite://",
            ),
            AgentWorkspace=DummyWorkspace,
            VirtualFileNode=DummyVirtualFileNode,
            VirtualShellCommand=DummyVirtualShellCommand,
            VirtualShellSession=DummyVirtualShellSession,
            load_project_state_payload=lambda *args, **kwargs: {},
        )
        self.repositories = build_in_memory_repositories(self.dependencies)

    def test_build_in_memory_repositories_returns_memory_classes(self) -> None:
        self.assertIsInstance(self.repositories.workspace, InMemoryWorkspaceRepository)
        self.assertIsInstance(self.repositories.session, InMemorySessionRepository)
        self.assertIsInstance(self.repositories.node, InMemoryNodeRepository)
        self.assertIsInstance(self.repositories.command_log, InMemoryCommandLogRepository)

    def test_workspace_repository_get_and_update_metadata(self) -> None:
        missing = self.repositories.workspace.get_workspace(None, 11, "tenant-a")
        self.assertIsNone(missing)

        updated = self.repositories.workspace.update_workspace_metadata(
            None,
            workspace_id=11,
            tenant_key="tenant-a",
            metadata_json={"virtual_access_mode": "agent"},
        )
        self.assertTrue(updated)

        workspace = self.repositories.workspace.get_workspace(None, 11, "tenant-a")
        self.assertIsNotNone(workspace)
        self.assertEqual(workspace.id, 11)
        self.assertEqual(workspace.tenant_id, "tenant-a")
        self.assertEqual(workspace.metadata_json["virtual_access_mode"], "agent")

    def test_session_repository_create_lookup_and_update(self) -> None:
        first = self.repositories.session.create_session(
            None,
            tenant_key="tenant-a",
            workspace_id=21,
            cwd_node_id=1,
            env_json={"PWD": "/inactive"},
            status="inactive",
        )
        active = self.repositories.session.create_session(
            None,
            tenant_key="tenant-a",
            workspace_id=21,
            cwd_node_id=2,
            env_json={"PWD": "/workspace"},
            status="active",
        )
        later_active = self.repositories.session.create_session(
            None,
            tenant_key="tenant-a",
            workspace_id=21,
            cwd_node_id=3,
            env_json={"PWD": "/workspace/docs"},
            status="active",
        )

        loaded = self.repositories.session.get_active_session(None, 21, "tenant-a")
        self.assertEqual(loaded.id, later_active.id)
        self.assertNotEqual(loaded.id, first.id)

        self.repositories.session.update_session_cwd(
            None,
            session_id=active.id,
            tenant_key="tenant-a",
            cwd_node_id=99,
        )
        updated = next(
            session
            for session in self.repositories.session.state.sessions.values()
            if int(session.id) == int(active.id)
        )
        self.assertEqual(updated.cwd_node_id, 99)

        self.repositories.session.update_session_cwd(
            None,
            session_id=active.id,
            tenant_key="other-tenant",
            cwd_node_id=123,
        )
        unchanged = next(
            session
            for session in self.repositories.session.state.sessions.values()
            if int(session.id) == int(active.id)
        )
        self.assertEqual(unchanged.cwd_node_id, 99)

    def test_node_repository_crud_listing_and_touch(self) -> None:
        root = self.repositories.node.create_node(
            None,
            tenant_key="tenant-a",
            workspace_id=31,
            parent_id=None,
            name="",
            node_type="dir",
            content_text="",
            version_no=1,
        )
        docs = self.repositories.node.create_node(
            None,
            tenant_key="tenant-a",
            workspace_id=31,
            parent_id=root.id,
            name="docs",
            node_type="dir",
            content_text="",
            version_no=1,
        )
        file_node = self.repositories.node.create_node(
            None,
            tenant_key="tenant-a",
            workspace_id=31,
            parent_id=docs.id,
            name="readme.md",
            node_type="file",
            content_text="hello",
            version_no=1,
        )

        self.assertEqual(self.repositories.node.get_root(None, 31, "tenant-a").id, root.id)
        self.assertEqual(
            self.repositories.node.get_child(
                None,
                tenant_key="tenant-a",
                workspace_id=31,
                parent_id=root.id,
                name="docs",
                node_type="dir",
            ).id,
            docs.id,
        )
        self.assertEqual(self.repositories.node.get_node(None, file_node.id, "tenant-a").id, file_node.id)

        names = [node.name for node in self.repositories.node.list_workspace_nodes(None, 31, "tenant-a")]
        self.assertEqual(names, ["", "docs", "readme.md"])

        self.repositories.node.update_node_content(
            None,
            node_id=file_node.id,
            tenant_key="tenant-a",
            parent_id=docs.id,
            name="guide.md",
            node_type="file",
            content_text="updated",
            version_no=2,
        )
        refreshed = self.repositories.node.get_node(None, file_node.id, "tenant-a")
        self.assertEqual(refreshed.name, "guide.md")
        self.assertEqual(refreshed.content_text, "updated")
        self.assertEqual(refreshed.version_no, 2)

        self.repositories.node.touch_node(None, node=refreshed)
        self.assertEqual(refreshed.content_text, "updated")

    def test_node_repository_search_variants(self) -> None:
        root = self.repositories.node.create_node(
            None,
            tenant_key="tenant-a",
            workspace_id=41,
            parent_id=None,
            name="",
            node_type="dir",
            content_text="",
            version_no=1,
        )
        docs = self.repositories.node.create_node(
            None,
            tenant_key="tenant-a",
            workspace_id=41,
            parent_id=root.id,
            name="docs",
            node_type="dir",
            content_text="",
            version_no=1,
        )
        self.repositories.node.create_node(
            None,
            tenant_key="tenant-a",
            workspace_id=41,
            parent_id=docs.id,
            name="guide.md",
            node_type="file",
            content_text="Hello PostgreSQL",
            version_no=1,
        )
        self.repositories.node.create_node(
            None,
            tenant_key="tenant-a",
            workspace_id=41,
            parent_id=docs.id,
            name="caps.md",
            node_type="file",
            content_text="HELLO WORLD",
            version_no=1,
        )

        ci_rows = self.repositories.node.search_subtree_files(
            None,
            tenant_key="tenant-a",
            workspace_id=41,
            root_id=root.id,
            pattern="hello",
            use_case_insensitive=True,
            use_literal_case_sensitive=False,
        )
        self.assertEqual([row["rel_path"] for row in ci_rows], ["docs/caps.md", "docs/guide.md"])

        cs_rows = self.repositories.node.search_subtree_files(
            None,
            tenant_key="tenant-a",
            workspace_id=41,
            root_id=root.id,
            pattern="Hello",
            use_case_insensitive=False,
            use_literal_case_sensitive=True,
        )
        self.assertEqual([row["rel_path"] for row in cs_rows], ["docs/guide.md"])

        all_rows = self.repositories.node.search_subtree_files(
            None,
            tenant_key="tenant-a",
            workspace_id=41,
            root_id=root.id,
            pattern="ignored",
            use_case_insensitive=False,
            use_literal_case_sensitive=False,
        )
        self.assertEqual([row["rel_path"] for row in all_rows], ["docs/caps.md", "docs/guide.md"])

    def test_command_log_repository_create_and_bulk_insert(self) -> None:
        command_id = self.repositories.command_log.create_command_log(
            None,
            {
                "tenant_id": "tenant-a",
                "session_id": 51,
                "raw_cmd": "ls",
                "parsed_json": {"segments": [{"cmd": "ls"}]},
                "exit_code": 0,
                "stdout_text": "docs",
                "stderr_text": "",
                "artifacts_json": {},
            },
        )
        self.assertEqual(command_id, 1)

        self.repositories.command_log.bulk_insert_command_logs(
            None,
            [
                {
                    "tenant_id": "tenant-a",
                    "session_id": 51,
                    "raw_cmd": "pwd",
                    "parsed_json": {},
                    "exit_code": 0,
                    "stdout_text": "/workspace",
                    "stderr_text": "",
                    "artifacts_json": {},
                },
                {
                    "tenant_id": "tenant-a",
                    "session_id": 51,
                    "raw_cmd": "cat guide.md",
                    "parsed_json": {},
                    "exit_code": 0,
                    "stdout_text": "hello",
                    "stderr_text": "",
                    "artifacts_json": {},
                },
            ],
        )

        logs = self.repositories.command_log.state.command_logs
        self.assertEqual(sorted(logs.keys()), [1, 2, 3])
        self.assertEqual(logs[3]["raw_cmd"], "cat guide.md")

    def test_ephemeral_profiles_resolve_memory_repositories(self) -> None:
        for profile in ("ephemeral-local", "ephemeral-redis"):
            configure_vfs_dependencies(
                VFSDependencies(
                    settings=SimpleNamespace(
                        default_tenant_id="test-tenant",
                        redis_key_namespace="test",
                        redis_url="redis://localhost:6379/0",
                        database_url="sqlite://",
                    ),
                    AgentWorkspace=DummyWorkspace,
                    VirtualFileNode=DummyVirtualFileNode,
                    VirtualShellCommand=DummyVirtualShellCommand,
                    VirtualShellSession=DummyVirtualShellSession,
                    load_project_state_payload=lambda *args, **kwargs: {},
                    runtime_profile=profile,
                )
            )
            dependency_resolution = importlib.reload(importlib.import_module("iruka_vfs.dependency_resolution"))
            repositories = dependency_resolution.resolve_vfs_repositories()
            self.assertEqual(type(repositories.workspace).__name__, "InMemoryWorkspaceRepository")
            self.assertEqual(type(repositories.session).__name__, "InMemorySessionRepository")
            self.assertEqual(type(repositories.node).__name__, "InMemoryNodeRepository")
            self.assertEqual(type(repositories.command_log).__name__, "InMemoryCommandLogRepository")


if __name__ == "__main__":
    unittest.main()
