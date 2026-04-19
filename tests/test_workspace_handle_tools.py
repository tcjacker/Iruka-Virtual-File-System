from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.support import DummyWorkspace, configure_test_dependencies

configure_test_dependencies()

from iruka_vfs.sdk.workspace_handle import VirtualWorkspace


class WorkspaceHandlePublicApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = VirtualWorkspace(
            workspace=DummyWorkspace(id=7, tenant_id="tenant-a"),
            runtime_seed=SimpleNamespace(),
            tenant_id="tenant-a",
        )

    def test_workspace_run_delegates_to_virtual_bash_payload(self) -> None:
        expected = {
            "stdout": "ok",
            "stderr": "",
            "exit_code": 0,
            "artifacts": {},
            "cwd": "/workspace",
            "session_id": 11,
            "command_id": 21,
        }
        db = object()
        with patch("iruka_vfs.service.ensure_virtual_workspace", return_value={"tree": ""}):
            with patch("iruka_vfs.service.set_workspace_access_mode", side_effect=["agent", "host"]):
                with patch("iruka_vfs.service.run_virtual_bash", return_value=expected) as run_virtual_bash:
                    result = self.workspace.run(db, "pwd")
        self.assertEqual(result, expected)
        run_virtual_bash.assert_called_once_with(
            db,
            self.workspace.workspace,
            "pwd",
            runtime_seed=self.workspace.runtime_seed,
            tenant_id="tenant-a",
        )

    def test_workspace_write_delegates_to_structured_write_payload(self) -> None:
        expected = {
            "operation": "tool_write",
            "path": "/workspace/a.txt",
            "version": 3,
            "created": True,
            "bytes_written": 5,
        }
        db = object()
        with patch("iruka_vfs.service.ensure_virtual_workspace", return_value={"tree": ""}):
            with patch("iruka_vfs.service.set_workspace_access_mode", return_value="host"):
                with patch(
                    "iruka_vfs.service_ops.file_api.tool_write_workspace_file",
                    return_value=expected,
                ) as tool_write:
                    result = self.workspace.write(db, "/workspace/a.txt", "hello")
        self.assertEqual(result, expected)
        tool_write.assert_called_once_with(
            db,
            self.workspace.workspace,
            "/workspace/a.txt",
            "hello",
            runtime_seed=self.workspace.runtime_seed,
            tenant_id="tenant-a",
        )

    def test_workspace_edit_delegates_to_structured_edit_payload(self) -> None:
        expected = {
            "operation": "tool_edit",
            "path": "/workspace/a.txt",
            "version": 4,
            "replacements": 1,
        }
        db = object()
        with patch("iruka_vfs.service.ensure_virtual_workspace", return_value={"tree": ""}):
            with patch("iruka_vfs.service.set_workspace_access_mode", return_value="host"):
                with patch(
                    "iruka_vfs.service_ops.file_api.tool_edit_workspace_file",
                    return_value=expected,
                ) as tool_edit:
                    result = self.workspace.edit(db, "/workspace/a.txt", "before", "after")
        self.assertEqual(result, expected)
        tool_edit.assert_called_once_with(
            db,
            self.workspace.workspace,
            "/workspace/a.txt",
            "before",
            "after",
            replace_all=False,
            runtime_seed=self.workspace.runtime_seed,
            tenant_id="tenant-a",
        )

    def test_workspace_file_tree_delegates_to_service(self) -> None:
        expected = {"path": "/workspace", "name": "workspace", "type": "dir", "children": []}
        db = object()
        with patch("iruka_vfs.service_ops.file_api.get_workspace_file_tree", return_value=expected) as file_tree:
            result = self.workspace.file_tree(db, "/workspace")
        self.assertEqual(result, expected)
        file_tree.assert_called_once_with(
            db,
            self.workspace.workspace,
            "/workspace",
            runtime_seed=self.workspace.runtime_seed,
            tenant_id="tenant-a",
        )
