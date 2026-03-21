from __future__ import annotations

import importlib
import unittest
from types import SimpleNamespace

from iruka_vfs import build_profile_dependencies, build_profile_persistent_dependencies
from iruka_vfs.dependencies import VFSDependencies, configure_vfs_dependencies
from iruka_vfs.runtime_seed import RuntimeSeed
from tests.support import (
    DummyVirtualFileNode,
    DummyVirtualShellCommand,
    DummyVirtualShellSession,
    DummyWorkspace,
)


def _configure_profile(runtime_profile: str) -> None:
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
            runtime_profile=runtime_profile,
        )
    )


def _reload(module_name: str):
    module = importlib.import_module(module_name)
    return importlib.reload(module)


class RuntimeProfilesTest(unittest.TestCase):
    def test_manual_vfs_dependencies_uses_internal_defaults(self) -> None:
        configure_vfs_dependencies(
            VFSDependencies(
                settings=SimpleNamespace(
                    default_tenant_id="test-tenant",
                    redis_key_namespace="test",
                    redis_url="redis://localhost:6379/0",
                    database_url="sqlite://",
                ),
                runtime_profile="ephemeral-local",
            )
        )
        dependency_resolution = _reload("iruka_vfs.dependency_resolution")
        state_module = _reload("iruka_vfs.service_ops.state")

        repositories = dependency_resolution.resolve_vfs_repositories()
        store = state_module.get_workspace_state_store()

        self.assertEqual(type(repositories.workspace).__name__, "InMemoryWorkspaceRepository")
        self.assertEqual(type(store).__name__, "LocalMemoryStateStore")

    def test_minimal_ephemeral_local_profile_dependencies_use_internal_defaults(self) -> None:
        configure_vfs_dependencies(
            build_profile_dependencies(
                settings=SimpleNamespace(
                    default_tenant_id="test-tenant",
                    redis_key_namespace="test",
                    redis_url="redis://localhost:6379/0",
                    database_url="sqlite://",
                ),
                runtime_profile="ephemeral-local",
            )
        )
        dependency_resolution = _reload("iruka_vfs.dependency_resolution")
        state_module = _reload("iruka_vfs.service_ops.state")

        repositories = dependency_resolution.resolve_vfs_repositories()
        store = state_module.get_workspace_state_store()

        self.assertEqual(type(repositories.workspace).__name__, "InMemoryWorkspaceRepository")
        self.assertEqual(type(store).__name__, "LocalMemoryStateStore")

    def test_build_profile_persistent_dependencies_resolves_persistent_layers(self) -> None:
        configure_vfs_dependencies(
            build_profile_persistent_dependencies(
                settings=SimpleNamespace(
                    default_tenant_id="test-tenant",
                    redis_key_namespace="test",
                    redis_url="redis://localhost:6379/0",
                    database_url="sqlite://",
                ),
            )
        )
        dependency_resolution = _reload("iruka_vfs.dependency_resolution")
        state_module = _reload("iruka_vfs.service_ops.state")

        repositories = dependency_resolution.resolve_vfs_repositories()
        store = state_module.get_workspace_state_store()

        self.assertEqual(type(repositories.workspace).__name__, "PostgreSQLWorkspaceRepository")
        self.assertEqual(type(store).__name__, "RedisWorkspaceStateStore")

    def test_persistent_profile_resolves_sqlalchemy_and_redis(self) -> None:
        _configure_profile("persistent")
        dependency_resolution = _reload("iruka_vfs.dependency_resolution")
        state_module = _reload("iruka_vfs.service_ops.state")

        repositories = dependency_resolution.resolve_vfs_repositories()
        store = state_module.get_workspace_state_store()

        self.assertEqual(type(repositories.workspace).__name__, "PostgreSQLWorkspaceRepository")
        self.assertEqual(type(store).__name__, "RedisWorkspaceStateStore")

    def test_ephemeral_local_profile_resolves_memory_layers(self) -> None:
        _configure_profile("ephemeral-local")
        dependency_resolution = _reload("iruka_vfs.dependency_resolution")
        state_module = _reload("iruka_vfs.service_ops.state")

        repositories = dependency_resolution.resolve_vfs_repositories()
        store = state_module.get_workspace_state_store()

        self.assertEqual(type(repositories.workspace).__name__, "InMemoryWorkspaceRepository")
        self.assertEqual(type(store).__name__, "LocalMemoryStateStore")

    def test_ephemeral_redis_profile_resolves_memory_repositories_and_redis_store(self) -> None:
        _configure_profile("ephemeral-redis")
        dependency_resolution = _reload("iruka_vfs.dependency_resolution")
        state_module = _reload("iruka_vfs.service_ops.state")

        repositories = dependency_resolution.resolve_vfs_repositories()
        store = state_module.get_workspace_state_store()

        self.assertEqual(type(repositories.workspace).__name__, "InMemoryWorkspaceRepository")
        self.assertEqual(type(store).__name__, "RedisWorkspaceStateStore")

    def test_ephemeral_local_workspace_bootstrap_flow(self) -> None:
        _configure_profile("ephemeral-local")
        _reload("iruka_vfs.dependency_resolution")
        service_state = _reload("iruka_vfs.service_ops.state")
        _reload("iruka_vfs.workspace_mirror")
        _reload("iruka_vfs.service")
        bootstrap = _reload("iruka_vfs.service_ops.bootstrap")

        workspace = DummyWorkspace(id=101, tenant_id="test-tenant", metadata_json={})
        runtime_seed = RuntimeSeed(
            runtime_key="runtime:test-101",
            tenant_id="test-tenant",
            primary_file=None,
            workspace_files={"/workspace/files/demo.txt": "hello"},
            context_files={"context.md": "ctx"},
            skill_files={"skill.md": "skill"},
            metadata={},
        )

        snapshot = bootstrap.ensure_virtual_workspace(
            None,
            workspace,
            runtime_seed,
            include_tree=False,
            tenant_id="test-tenant",
        )
        store = service_state.get_workspace_state_store()
        mirror = store.get_workspace_mirror(101, tenant_key="test-tenant")

        self.assertEqual(snapshot["workspace_id"], 101)
        self.assertIsNotNone(mirror)
        self.assertIn("/workspace/files/demo.txt", mirror.path_to_id)
        self.assertEqual(type(store).__name__, "LocalMemoryStateStore")


if __name__ == "__main__":
    unittest.main()
