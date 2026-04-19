from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from tests.support import DummyWorkspace, configure_test_dependencies

configure_test_dependencies()

from iruka_vfs.sdk.workspace_handle import VirtualWorkspace


class WorkspaceHandleAccessModeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = VirtualWorkspace(
            workspace=DummyWorkspace(id=7, tenant_id="tenant-a"),
            runtime_seed=SimpleNamespace(),
            tenant_id="tenant-a",
        )

    def test_run_bootstraps_switches_to_agent_then_back_to_host(self) -> None:
        db = object()
        with patch("iruka_vfs.service.ensure_virtual_workspace", return_value={"tree": ""}) as ensure_workspace:
            with patch("iruka_vfs.service.get_workspace_access_mode", return_value="host") as get_mode:
                with patch("iruka_vfs.service.set_workspace_access_mode", side_effect=["agent", "host"]) as set_mode:
                    with patch(
                        "iruka_vfs.service.run_virtual_bash",
                        return_value={
                            "exit_code": 0,
                            "stdout": "",
                            "stderr": "",
                            "artifacts": {},
                            "cwd": "/workspace",
                            "session_id": 1,
                            "command_id": 2,
                        },
                    ) as run_virtual_bash:
                        parent = Mock()
                        parent.attach_mock(ensure_workspace, "ensure_virtual_workspace")
                        parent.attach_mock(get_mode, "get_workspace_access_mode")
                        parent.attach_mock(set_mode, "set_workspace_access_mode")
                        parent.attach_mock(run_virtual_bash, "run_virtual_bash")
                        self.workspace.run(db, "pwd")

        self.assertEqual(
            parent.mock_calls,
            [
                call.ensure_virtual_workspace(
                    db,
                    self.workspace.workspace,
                    self.workspace.runtime_seed,
                    include_tree=False,
                    tenant_id="tenant-a",
                ),
                call.get_workspace_access_mode(
                    db,
                    self.workspace.workspace,
                    runtime_seed=self.workspace.runtime_seed,
                    tenant_id="tenant-a",
                ),
                call.set_workspace_access_mode(
                    db,
                    self.workspace.workspace,
                    runtime_seed=self.workspace.runtime_seed,
                    mode="agent",
                    tenant_id="tenant-a",
                    flush=True,
                ),
                call.run_virtual_bash(
                    db,
                    self.workspace.workspace,
                    "pwd",
                    runtime_seed=self.workspace.runtime_seed,
                    tenant_id="tenant-a",
                ),
                call.set_workspace_access_mode(
                    db,
                    self.workspace.workspace,
                    runtime_seed=self.workspace.runtime_seed,
                    mode="host",
                    tenant_id="tenant-a",
                    flush=True,
                ),
            ],
        )

    def test_write_restores_host_mode_after_success(self) -> None:
        db = object()
        expected = {
            "operation": "tool_write",
            "path": "/workspace/a.txt",
            "version": 3,
            "created": True,
            "bytes_written": 5,
        }
        with patch("iruka_vfs.service.ensure_virtual_workspace", return_value={"tree": ""}) as ensure_workspace:
            with patch("iruka_vfs.service.get_workspace_access_mode", return_value="host") as get_mode:
                with patch("iruka_vfs.service.set_workspace_access_mode", side_effect=["host", "host"]) as set_mode:
                    with patch(
                        "iruka_vfs.service_ops.file_api.tool_write_workspace_file",
                        return_value=expected,
                    ) as tool_write:
                        parent = Mock()
                        parent.attach_mock(ensure_workspace, "ensure_virtual_workspace")
                        parent.attach_mock(get_mode, "get_workspace_access_mode")
                        parent.attach_mock(set_mode, "set_workspace_access_mode")
                        parent.attach_mock(tool_write, "tool_write_workspace_file")
                        result = self.workspace.write(db, "/workspace/a.txt", "hello")

        self.assertEqual(result, expected)
        self.assertEqual(
            parent.mock_calls,
            [
                call.ensure_virtual_workspace(
                    db,
                    self.workspace.workspace,
                    self.workspace.runtime_seed,
                    include_tree=False,
                    tenant_id="tenant-a",
                ),
                call.get_workspace_access_mode(
                    db,
                    self.workspace.workspace,
                    runtime_seed=self.workspace.runtime_seed,
                    tenant_id="tenant-a",
                ),
                call.set_workspace_access_mode(
                    db,
                    self.workspace.workspace,
                    runtime_seed=self.workspace.runtime_seed,
                    mode="host",
                    tenant_id="tenant-a",
                    flush=True,
                ),
                call.tool_write_workspace_file(
                    db,
                    self.workspace.workspace,
                    "/workspace/a.txt",
                    "hello",
                    runtime_seed=self.workspace.runtime_seed,
                    tenant_id="tenant-a",
                ),
                call.set_workspace_access_mode(
                    db,
                    self.workspace.workspace,
                    runtime_seed=self.workspace.runtime_seed,
                    mode="host",
                    tenant_id="tenant-a",
                    flush=True,
                ),
            ],
        )

    def test_read_file_restores_host_mode_after_success(self) -> None:
        db = object()
        with patch("iruka_vfs.service.ensure_virtual_workspace", return_value={"tree": ""}) as ensure_workspace:
            with patch("iruka_vfs.service.get_workspace_access_mode", return_value="host") as get_mode:
                with patch("iruka_vfs.service.set_workspace_access_mode", side_effect=["host", "host"]) as set_mode:
                    with patch("iruka_vfs.service.read_workspace_file", return_value="hello") as read_file:
                        parent = Mock()
                        parent.attach_mock(ensure_workspace, "ensure_virtual_workspace")
                        parent.attach_mock(get_mode, "get_workspace_access_mode")
                        parent.attach_mock(set_mode, "set_workspace_access_mode")
                        parent.attach_mock(read_file, "read_workspace_file")
                        result = self.workspace.read_file(db, "/workspace/a.txt")

        self.assertEqual(result, "hello")
        self.assertEqual(
            parent.mock_calls,
            [
                call.ensure_virtual_workspace(
                    db,
                    self.workspace.workspace,
                    self.workspace.runtime_seed,
                    include_tree=False,
                    tenant_id="tenant-a",
                ),
                call.get_workspace_access_mode(
                    db,
                    self.workspace.workspace,
                    runtime_seed=self.workspace.runtime_seed,
                    tenant_id="tenant-a",
                ),
                call.set_workspace_access_mode(
                    db,
                    self.workspace.workspace,
                    runtime_seed=self.workspace.runtime_seed,
                    mode="host",
                    tenant_id="tenant-a",
                    flush=True,
                ),
                call.read_workspace_file(
                    db,
                    self.workspace.workspace,
                    "/workspace/a.txt",
                    runtime_seed=self.workspace.runtime_seed,
                    tenant_id="tenant-a",
                ),
                call.set_workspace_access_mode(
                    db,
                    self.workspace.workspace,
                    runtime_seed=self.workspace.runtime_seed,
                    mode="host",
                    tenant_id="tenant-a",
                    flush=True,
                ),
            ],
        )

    def test_edit_restores_host_mode_after_success(self) -> None:
        db = object()
        expected = {
            "operation": "tool_edit",
            "path": "/workspace/a.txt",
            "version": 4,
            "replacements": 1,
        }
        with patch("iruka_vfs.service.ensure_virtual_workspace", return_value={"tree": ""}) as ensure_workspace:
            with patch("iruka_vfs.service.get_workspace_access_mode", return_value="host") as get_mode:
                with patch("iruka_vfs.service.set_workspace_access_mode", side_effect=["host", "host"]) as set_mode:
                    with patch("iruka_vfs.service_ops.file_api.tool_edit_workspace_file", return_value=expected) as tool_edit:
                        parent = Mock()
                        parent.attach_mock(ensure_workspace, "ensure_virtual_workspace")
                        parent.attach_mock(get_mode, "get_workspace_access_mode")
                        parent.attach_mock(set_mode, "set_workspace_access_mode")
                        parent.attach_mock(tool_edit, "tool_edit_workspace_file")
                        result = self.workspace.edit(db, "/workspace/a.txt", "before", "after")

        self.assertEqual(result, expected)
        self.assertEqual(
            parent.mock_calls,
            [
                call.ensure_virtual_workspace(
                    db,
                    self.workspace.workspace,
                    self.workspace.runtime_seed,
                    include_tree=False,
                    tenant_id="tenant-a",
                ),
                call.get_workspace_access_mode(
                    db,
                    self.workspace.workspace,
                    runtime_seed=self.workspace.runtime_seed,
                    tenant_id="tenant-a",
                ),
                call.set_workspace_access_mode(
                    db,
                    self.workspace.workspace,
                    runtime_seed=self.workspace.runtime_seed,
                    mode="host",
                    tenant_id="tenant-a",
                    flush=True,
                ),
                call.tool_edit_workspace_file(
                    db,
                    self.workspace.workspace,
                    "/workspace/a.txt",
                    "before",
                    "after",
                    replace_all=False,
                    runtime_seed=self.workspace.runtime_seed,
                    tenant_id="tenant-a",
                ),
                call.set_workspace_access_mode(
                    db,
                    self.workspace.workspace,
                    runtime_seed=self.workspace.runtime_seed,
                    mode="host",
                    tenant_id="tenant-a",
                    flush=True,
                ),
            ],
        )

    def test_read_directory_restores_host_mode_after_success(self) -> None:
        db = object()
        expected = {
            "/workspace/a.txt": "hello",
            "/workspace/b.txt": "world",
        }
        with patch("iruka_vfs.service.ensure_virtual_workspace", return_value={"tree": ""}) as ensure_workspace:
            with patch("iruka_vfs.service.get_workspace_access_mode", return_value="host") as get_mode:
                with patch("iruka_vfs.service.set_workspace_access_mode", side_effect=["host", "host"]) as set_mode:
                    with patch("iruka_vfs.service.read_workspace_directory", return_value=expected) as read_directory:
                        parent = Mock()
                        parent.attach_mock(ensure_workspace, "ensure_virtual_workspace")
                        parent.attach_mock(get_mode, "get_workspace_access_mode")
                        parent.attach_mock(set_mode, "set_workspace_access_mode")
                        parent.attach_mock(read_directory, "read_workspace_directory")
                        result = self.workspace.read_directory(db, "/workspace", recursive=False)

        self.assertEqual(result, expected)
        self.assertEqual(
            parent.mock_calls,
            [
                call.ensure_virtual_workspace(
                    db,
                    self.workspace.workspace,
                    self.workspace.runtime_seed,
                    include_tree=False,
                    tenant_id="tenant-a",
                ),
                call.get_workspace_access_mode(
                    db,
                    self.workspace.workspace,
                    runtime_seed=self.workspace.runtime_seed,
                    tenant_id="tenant-a",
                ),
                call.set_workspace_access_mode(
                    db,
                    self.workspace.workspace,
                    runtime_seed=self.workspace.runtime_seed,
                    mode="host",
                    tenant_id="tenant-a",
                    flush=True,
                ),
                call.read_workspace_directory(
                    db,
                    self.workspace.workspace,
                    "/workspace",
                    runtime_seed=self.workspace.runtime_seed,
                    tenant_id="tenant-a",
                    recursive=False,
                ),
                call.set_workspace_access_mode(
                    db,
                    self.workspace.workspace,
                    runtime_seed=self.workspace.runtime_seed,
                    mode="host",
                    tenant_id="tenant-a",
                    flush=True,
                ),
            ],
        )

    def test_file_tree_restores_host_mode_after_success(self) -> None:
        db = object()
        expected = {"path": "/workspace", "name": "workspace", "type": "dir", "children": []}
        with patch("iruka_vfs.service.ensure_virtual_workspace", return_value={"tree": ""}) as ensure_workspace:
            with patch("iruka_vfs.service.get_workspace_access_mode", return_value="host") as get_mode:
                with patch("iruka_vfs.service.set_workspace_access_mode", side_effect=["host", "host"]) as set_mode:
                    with patch("iruka_vfs.service_ops.file_api.get_workspace_file_tree", return_value=expected) as file_tree:
                        parent = Mock()
                        parent.attach_mock(ensure_workspace, "ensure_virtual_workspace")
                        parent.attach_mock(get_mode, "get_workspace_access_mode")
                        parent.attach_mock(set_mode, "set_workspace_access_mode")
                        parent.attach_mock(file_tree, "get_workspace_file_tree")
                        result = self.workspace.file_tree(db, "/workspace")

        self.assertEqual(result, expected)
        self.assertEqual(
            parent.mock_calls,
            [
                call.ensure_virtual_workspace(
                    db,
                    self.workspace.workspace,
                    self.workspace.runtime_seed,
                    include_tree=False,
                    tenant_id="tenant-a",
                ),
                call.get_workspace_access_mode(
                    db,
                    self.workspace.workspace,
                    runtime_seed=self.workspace.runtime_seed,
                    tenant_id="tenant-a",
                ),
                call.set_workspace_access_mode(
                    db,
                    self.workspace.workspace,
                    runtime_seed=self.workspace.runtime_seed,
                    mode="host",
                    tenant_id="tenant-a",
                    flush=True,
                ),
                call.get_workspace_file_tree(
                    db,
                    self.workspace.workspace,
                    "/workspace",
                    runtime_seed=self.workspace.runtime_seed,
                    tenant_id="tenant-a",
                ),
                call.set_workspace_access_mode(
                    db,
                    self.workspace.workspace,
                    runtime_seed=self.workspace.runtime_seed,
                    mode="host",
                    tenant_id="tenant-a",
                    flush=True,
                ),
            ],
        )

    def test_success_then_host_recovery_failure_raises_recovery_exception(self) -> None:
        db = object()
        recovery_error = RuntimeError("failed to restore host mode")
        with patch("iruka_vfs.service.ensure_virtual_workspace", return_value={"tree": ""}) as ensure_workspace:
            with patch("iruka_vfs.service.get_workspace_access_mode", return_value="readonly") as get_mode:
                with patch("iruka_vfs.service.set_workspace_access_mode", side_effect=["agent", recovery_error]) as set_mode:
                    with patch(
                        "iruka_vfs.service.run_virtual_bash",
                        return_value={
                            "exit_code": 0,
                            "stdout": "",
                            "stderr": "",
                            "artifacts": {},
                            "cwd": "/workspace",
                            "session_id": 1,
                            "command_id": 2,
                        },
                    ) as run_virtual_bash:
                        with patch("iruka_vfs.sdk.workspace_handle.logger.error") as log_error:
                            parent = Mock()
                            parent.attach_mock(ensure_workspace, "ensure_virtual_workspace")
                            parent.attach_mock(get_mode, "get_workspace_access_mode")
                            parent.attach_mock(set_mode, "set_workspace_access_mode")
                            parent.attach_mock(run_virtual_bash, "run_virtual_bash")
                            with self.assertRaisesRegex(RuntimeError, "failed to restore host mode") as captured:
                                self.workspace.run(db, "pwd")

        self.assertEqual(
            parent.mock_calls,
            [
                call.ensure_virtual_workspace(
                    db,
                    self.workspace.workspace,
                    self.workspace.runtime_seed,
                    include_tree=False,
                    tenant_id="tenant-a",
                ),
                call.get_workspace_access_mode(
                    db,
                    self.workspace.workspace,
                    runtime_seed=self.workspace.runtime_seed,
                    tenant_id="tenant-a",
                ),
                call.set_workspace_access_mode(
                    db,
                    self.workspace.workspace,
                    runtime_seed=self.workspace.runtime_seed,
                    mode="agent",
                    tenant_id="tenant-a",
                    flush=True,
                ),
                call.run_virtual_bash(
                    db,
                    self.workspace.workspace,
                    "pwd",
                    runtime_seed=self.workspace.runtime_seed,
                    tenant_id="tenant-a",
                ),
                call.set_workspace_access_mode(
                    db,
                    self.workspace.workspace,
                    runtime_seed=self.workspace.runtime_seed,
                    mode="host",
                    tenant_id="tenant-a",
                    flush=True,
                ),
            ],
        )
        log_error.assert_called_once_with(
            "workspace mode recovery failed",
            extra={
                "workspace_id": 7,
                "tenant_id": "tenant-a",
                "original_mode": "readonly",
                "target_mode": "agent",
                "attempted_recovery_mode": "host",
                "action_name": "run",
                "action_exception_type": None,
                "recovery_exception_type": "RuntimeError",
                "action_succeeded": True,
            },
        )
        self.assertIn("post-condition", "\n".join(getattr(captured.exception, "__notes__", [])))

    def test_target_mode_entry_failure_still_attempts_host_recovery(self) -> None:
        db = object()
        entry_error = RuntimeError("failed to enter agent mode")
        with patch("iruka_vfs.service.ensure_virtual_workspace", return_value={"tree": ""}) as ensure_workspace:
            with patch("iruka_vfs.service.get_workspace_access_mode", return_value="readonly") as get_mode:
                with patch("iruka_vfs.service.set_workspace_access_mode", side_effect=[entry_error, "host"]) as set_mode:
                    with patch("iruka_vfs.service.run_virtual_bash") as run_virtual_bash:
                        parent = Mock()
                        parent.attach_mock(ensure_workspace, "ensure_virtual_workspace")
                        parent.attach_mock(get_mode, "get_workspace_access_mode")
                        parent.attach_mock(set_mode, "set_workspace_access_mode")
                        parent.attach_mock(run_virtual_bash, "run_virtual_bash")
                        with self.assertRaisesRegex(RuntimeError, "failed to enter agent mode"):
                            self.workspace.run(db, "pwd")

        self.assertEqual(
            parent.mock_calls,
            [
                call.ensure_virtual_workspace(
                    db,
                    self.workspace.workspace,
                    self.workspace.runtime_seed,
                    include_tree=False,
                    tenant_id="tenant-a",
                ),
                call.get_workspace_access_mode(
                    db,
                    self.workspace.workspace,
                    runtime_seed=self.workspace.runtime_seed,
                    tenant_id="tenant-a",
                ),
                call.set_workspace_access_mode(
                    db,
                    self.workspace.workspace,
                    runtime_seed=self.workspace.runtime_seed,
                    mode="agent",
                    tenant_id="tenant-a",
                    flush=True,
                ),
                call.set_workspace_access_mode(
                    db,
                    self.workspace.workspace,
                    runtime_seed=self.workspace.runtime_seed,
                    mode="host",
                    tenant_id="tenant-a",
                    flush=True,
                ),
            ],
        )
        run_virtual_bash.assert_not_called()

    def test_action_failure_keeps_original_exception_and_adds_recovery_note(self) -> None:
        db = object()
        action_error = ValueError("boom")
        recovery_error = RuntimeError("failed to restore host mode")
        with patch("iruka_vfs.service.ensure_virtual_workspace", return_value={"tree": ""}) as ensure_workspace:
            with patch("iruka_vfs.service.get_workspace_access_mode", return_value="readonly") as get_mode:
                with patch("iruka_vfs.service.set_workspace_access_mode", side_effect=["agent", recovery_error]) as set_mode:
                    with patch("iruka_vfs.service.run_virtual_bash", side_effect=action_error) as run_virtual_bash:
                        with patch("iruka_vfs.sdk.workspace_handle.logger.error") as log_error:
                            parent = Mock()
                            parent.attach_mock(ensure_workspace, "ensure_virtual_workspace")
                            parent.attach_mock(get_mode, "get_workspace_access_mode")
                            parent.attach_mock(set_mode, "set_workspace_access_mode")
                            parent.attach_mock(run_virtual_bash, "run_virtual_bash")
                            with self.assertRaisesRegex(ValueError, "boom") as captured:
                                self.workspace.run(db, "pwd")

        self.assertEqual(
            parent.mock_calls,
            [
                call.ensure_virtual_workspace(
                    db,
                    self.workspace.workspace,
                    self.workspace.runtime_seed,
                    include_tree=False,
                    tenant_id="tenant-a",
                ),
                call.get_workspace_access_mode(
                    db,
                    self.workspace.workspace,
                    runtime_seed=self.workspace.runtime_seed,
                    tenant_id="tenant-a",
                ),
                call.set_workspace_access_mode(
                    db,
                    self.workspace.workspace,
                    runtime_seed=self.workspace.runtime_seed,
                    mode="agent",
                    tenant_id="tenant-a",
                    flush=True,
                ),
                call.run_virtual_bash(
                    db,
                    self.workspace.workspace,
                    "pwd",
                    runtime_seed=self.workspace.runtime_seed,
                    tenant_id="tenant-a",
                ),
                call.set_workspace_access_mode(
                    db,
                    self.workspace.workspace,
                    runtime_seed=self.workspace.runtime_seed,
                    mode="host",
                    tenant_id="tenant-a",
                    flush=True,
                ),
            ],
        )
        log_error.assert_called_once_with(
            "workspace mode recovery failed",
            extra={
                "workspace_id": 7,
                "tenant_id": "tenant-a",
                "original_mode": "readonly",
                "target_mode": "agent",
                "attempted_recovery_mode": "host",
                "action_name": "run",
                "action_exception_type": "ValueError",
                "recovery_exception_type": "RuntimeError",
                "action_succeeded": False,
            },
        )
        notes = "\n".join(getattr(captured.exception, "__notes__", []))
        self.assertIn("failed to restore host mode", notes)
        self.assertIn("original_mode='readonly'", notes)
        self.assertIn("recovery_target_mode='host'", notes)
