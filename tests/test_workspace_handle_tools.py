from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.support import DummyWorkspace, configure_test_dependencies

configure_test_dependencies()

from iruka_vfs.sdk.workspace_handle import VirtualWorkspace


class WorkspaceHandleToolsTest(unittest.TestCase):
    def test_workspace_file_tree_delegates_to_service(self) -> None:
        workspace = VirtualWorkspace(
            workspace=DummyWorkspace(id=7, tenant_id="tenant-a"),
            runtime_seed=SimpleNamespace(),
            tenant_id="tenant-a",
        )
        expected = {"path": "/workspace", "name": "workspace", "type": "dir", "children": []}

        with patch("iruka_vfs.service_ops.file_api.get_workspace_file_tree", return_value=expected) as file_tree:
            result = workspace.file_tree(object(), "/workspace")

        self.assertEqual(result, expected)
        file_tree.assert_called_once()

    def test_workspace_tool_write_delegates_to_service(self) -> None:
        workspace = VirtualWorkspace(
            workspace=DummyWorkspace(id=7, tenant_id="tenant-a"),
            runtime_seed=SimpleNamespace(),
            tenant_id="tenant-a",
        )
        expected = {"operation": "tool_write", "path": "/workspace/a.txt"}

        with patch("iruka_vfs.service_ops.file_api.tool_write_workspace_file", return_value=expected) as tool_write:
            result = workspace.tool_write(object(), "/workspace/a.txt", "hello")

        self.assertEqual(result, expected)
        tool_write.assert_called_once()

    def test_workspace_tool_edit_delegates_to_service(self) -> None:
        workspace = VirtualWorkspace(
            workspace=DummyWorkspace(id=7, tenant_id="tenant-a"),
            runtime_seed=SimpleNamespace(),
            tenant_id="tenant-a",
        )
        expected = {"operation": "tool_edit", "path": "/workspace/a.txt", "replacements": 1}

        with patch("iruka_vfs.service_ops.file_api.tool_edit_workspace_file", return_value=expected) as tool_edit:
            result = workspace.tool_edit(object(), "/workspace/a.txt", "before", "after")

        self.assertEqual(result, expected)
        tool_edit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
