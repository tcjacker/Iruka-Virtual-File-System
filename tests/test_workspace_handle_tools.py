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
        with patch("iruka_vfs.service.run_virtual_bash", return_value=expected):
            with patch("iruka_vfs.service.set_workspace_access_mode", return_value="host"):
                with patch("iruka_vfs.service.ensure_virtual_workspace", return_value={"tree": ""}):
                    result = self.workspace.run(object(), "pwd")
        self.assertEqual(result, expected)

    def test_workspace_write_delegates_to_structured_write_payload(self) -> None:
        expected = {
            "operation": "tool_write",
            "path": "/workspace/a.txt",
            "version": 3,
            "created": True,
            "bytes_written": 5,
        }
        with patch("iruka_vfs.service_ops.file_api.tool_write_workspace_file", return_value=expected):
            with patch("iruka_vfs.service.set_workspace_access_mode", return_value="host"):
                with patch("iruka_vfs.service.ensure_virtual_workspace", return_value={"tree": ""}):
                    result = self.workspace.write(object(), "/workspace/a.txt", "hello")
        self.assertEqual(result, expected)

    def test_workspace_edit_delegates_to_structured_edit_payload(self) -> None:
        expected = {
            "operation": "tool_edit",
            "path": "/workspace/a.txt",
            "version": 4,
            "replacements": 1,
        }
        with patch("iruka_vfs.service_ops.file_api.tool_edit_workspace_file", return_value=expected):
            with patch("iruka_vfs.service.set_workspace_access_mode", return_value="host"):
                with patch("iruka_vfs.service.ensure_virtual_workspace", return_value={"tree": ""}):
                    result = self.workspace.edit(object(), "/workspace/a.txt", "before", "after")
        self.assertEqual(result, expected)
