from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from iruka_vfs.dependencies import VFSDependencies, configure_vfs_dependencies


@dataclass
class DummyWorkspace:
    id: int = 1
    tenant_id: str = "test-tenant"
    metadata_json: dict | None = None


@dataclass
class DummyChapter:
    id: int = 1


@dataclass
class DummyVirtualFileNode:
    id: int
    tenant_id: str = "test-tenant"
    workspace_id: int = 1
    parent_id: int | None = None
    name: str = ""
    node_type: str = "file"
    content_text: str = ""
    version_no: int = 1


@dataclass
class DummyVirtualShellCommand:
    id: int = 1


@dataclass
class DummyVirtualShellSession:
    id: int = 1
    tenant_id: str = "test-tenant"
    workspace_id: int = 1
    cwd_node_id: int = 1
    env_json: dict | None = None
    status: str = "active"


def configure_test_dependencies() -> None:
    configure_vfs_dependencies(
        VFSDependencies(
            settings=SimpleNamespace(
                default_tenant_id="test-tenant",
                redis_key_namespace="test",
                redis_url="redis://localhost:6379/0",
                database_url="sqlite://",
            ),
            AgentWorkspace=DummyWorkspace,
            Chapter=DummyChapter,
            VirtualFileNode=DummyVirtualFileNode,
            VirtualShellCommand=DummyVirtualShellCommand,
            VirtualShellSession=DummyVirtualShellSession,
            load_project_state_payload=lambda *args, **kwargs: {},
            repositories=None,
        )
    )
