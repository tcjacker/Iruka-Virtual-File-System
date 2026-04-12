from __future__ import annotations

import unittest
from threading import RLock
from types import SimpleNamespace
from unittest.mock import patch

from tests.support import DummyVirtualFileNode, DummyWorkspace, configure_test_dependencies

configure_test_dependencies()

from iruka_vfs.models import WorkspaceMirror
from iruka_vfs.service_ops.file_api import get_workspace_file_tree


def build_mirror() -> WorkspaceMirror:
    root = DummyVirtualFileNode(id=1, parent_id=None, name="workspace", node_type="dir")
    pages = DummyVirtualFileNode(id=2, parent_id=1, name="pages", node_type="dir")
    index = DummyVirtualFileNode(id=3, parent_id=2, name="index.html", node_type="file")
    empty_dir = DummyVirtualFileNode(id=4, parent_id=1, name="empty", node_type="dir")
    readme = DummyVirtualFileNode(id=5, parent_id=1, name="README.md", node_type="file")
    return WorkspaceMirror(
        tenant_key="tenant-a",
        scope_key="scope-a",
        workspace_id=7,
        root_id=1,
        session_id=9,
        cwd_node_id=1,
        nodes={1: root, 2: pages, 3: index, 4: empty_dir, 5: readme},
        path_to_id={
            "/workspace": 1,
            "/workspace/pages": 2,
            "/workspace/pages/index.html": 3,
            "/workspace/empty": 4,
            "/workspace/README.md": 5,
        },
        children_by_parent={None: [1], 1: [5, 4, 2], 2: [3], 4: []},
        workspace_metadata={},
        lock=RLock(),
    )


class WorkspaceFileTreeTest(unittest.TestCase):
    def test_get_workspace_file_tree_returns_recursive_dir_tree(self) -> None:
        workspace = DummyWorkspace(id=7, tenant_id="tenant-a", metadata_json={"virtual_access_mode": "host"})
        mirror = build_mirror()

        with patch("iruka_vfs.service_ops.file_api.assert_workspace_tenant", return_value="tenant-a"), patch(
            "iruka_vfs.service_ops.file_api.ensure_virtual_workspace"
        ), patch("iruka_vfs.service_ops.file_api.assert_workspace_access_mode"), patch(
            "iruka_vfs.service_ops.file_api.workspace_scope_for_db", return_value="scope-a"
        ), patch(
            "iruka_vfs.service_ops.file_api.get_workspace_mirror", return_value=mirror
        ):
            payload = get_workspace_file_tree(
                object(),
                workspace,
                "/workspace",
                runtime_seed=SimpleNamespace(),
                tenant_id="tenant-a",
            )

        self.assertEqual(payload["path"], "/workspace")
        self.assertEqual(payload["name"], "workspace")
        self.assertEqual(payload["type"], "dir")
        self.assertEqual([child["path"] for child in payload["children"]], [
            "/workspace/README.md",
            "/workspace/empty",
            "/workspace/pages",
        ])
        self.assertEqual(payload["children"][1]["children"], [])
        self.assertEqual(
            payload["children"][2]["children"],
            [{"path": "/workspace/pages/index.html", "name": "index.html", "type": "file"}],
        )

    def test_get_workspace_file_tree_returns_single_file_node_for_file_path(self) -> None:
        workspace = DummyWorkspace(id=7, tenant_id="tenant-a", metadata_json={"virtual_access_mode": "host"})
        mirror = build_mirror()

        with patch("iruka_vfs.service_ops.file_api.assert_workspace_tenant", return_value="tenant-a"), patch(
            "iruka_vfs.service_ops.file_api.ensure_virtual_workspace"
        ), patch("iruka_vfs.service_ops.file_api.assert_workspace_access_mode"), patch(
            "iruka_vfs.service_ops.file_api.workspace_scope_for_db", return_value="scope-a"
        ), patch(
            "iruka_vfs.service_ops.file_api.get_workspace_mirror", return_value=mirror
        ):
            payload = get_workspace_file_tree(
                object(),
                workspace,
                "/workspace/README.md",
                runtime_seed=SimpleNamespace(),
                tenant_id="tenant-a",
            )

        self.assertEqual(
            payload,
            {"path": "/workspace/README.md", "name": "README.md", "type": "file"},
        )

    def test_get_workspace_file_tree_rejects_missing_path(self) -> None:
        workspace = DummyWorkspace(id=7, tenant_id="tenant-a", metadata_json={"virtual_access_mode": "host"})
        mirror = build_mirror()

        with patch("iruka_vfs.service_ops.file_api.assert_workspace_tenant", return_value="tenant-a"), patch(
            "iruka_vfs.service_ops.file_api.ensure_virtual_workspace"
        ), patch("iruka_vfs.service_ops.file_api.assert_workspace_access_mode"), patch(
            "iruka_vfs.service_ops.file_api.workspace_scope_for_db", return_value="scope-a"
        ), patch(
            "iruka_vfs.service_ops.file_api.get_workspace_mirror", return_value=mirror
        ):
            with self.assertRaisesRegex(FileNotFoundError, "workspace path not found"):
                get_workspace_file_tree(
                    object(),
                    workspace,
                    "/workspace/missing",
                    runtime_seed=SimpleNamespace(),
                    tenant_id="tenant-a",
                )

    def test_get_workspace_file_tree_rejects_paths_outside_workspace(self) -> None:
        workspace = DummyWorkspace(id=7, tenant_id="tenant-a", metadata_json={"virtual_access_mode": "host"})

        with patch("iruka_vfs.service_ops.file_api.assert_workspace_tenant", return_value="tenant-a"), patch(
            "iruka_vfs.service_ops.file_api.ensure_virtual_workspace"
        ), patch("iruka_vfs.service_ops.file_api.assert_workspace_access_mode"), patch(
            "iruka_vfs.service_ops.file_api.workspace_scope_for_db", return_value="scope-a"
        ):
            with self.assertRaisesRegex(PermissionError, "must stay under /workspace"):
                get_workspace_file_tree(
                    object(),
                    workspace,
                    "/tmp/outside",
                    runtime_seed=SimpleNamespace(),
                    tenant_id="tenant-a",
                )


if __name__ == "__main__":
    unittest.main()
