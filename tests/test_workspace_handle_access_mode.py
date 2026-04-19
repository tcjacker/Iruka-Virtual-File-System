from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import call, patch

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
                ):
                    self.workspace.run(db, "pwd")

        ensure_workspace.assert_called_once_with(
            db,
            self.workspace.workspace,
            self.workspace.runtime_seed,
            include_tree=False,
            tenant_id="tenant-a",
        )
        self.assertEqual(
            set_mode.call_args_list,
            [
                call(
                    db,
                    self.workspace.workspace,
                    runtime_seed=self.workspace.runtime_seed,
                    mode="agent",
                    tenant_id="tenant-a",
                    flush=True,
                ),
                call(
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
            with patch("iruka_vfs.service.set_workspace_access_mode", return_value="host") as set_mode:
                with patch("iruka_vfs.service_ops.file_api.tool_write_workspace_file", return_value=expected):
                    result = self.workspace.write(db, "/workspace/a.txt", "hello")
        self.assertEqual(result, expected)
        ensure_workspace.assert_called_once_with(
            db,
            self.workspace.workspace,
            self.workspace.runtime_seed,
            include_tree=False,
            tenant_id="tenant-a",
        )
        self.assertEqual(
            set_mode.call_args_list,
            [
                call(
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
            with patch("iruka_vfs.service.set_workspace_access_mode", return_value="host") as set_mode:
                with patch("iruka_vfs.service_ops.file_api.tool_edit_workspace_file", return_value=expected):
                    result = self.workspace.edit(db, "/workspace/a.txt", "before", "after")
        self.assertEqual(result, expected)
        ensure_workspace.assert_called_once_with(
            db,
            self.workspace.workspace,
            self.workspace.runtime_seed,
            include_tree=False,
            tenant_id="tenant-a",
        )
        self.assertEqual(
            set_mode.call_args_list,
            [
                call(
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
                ):
                    with self.assertRaisesRegex(RuntimeError, "failed to restore host mode") as captured:
                        self.workspace.run(db, "pwd")
        ensure_workspace.assert_called_once_with(
            db,
            self.workspace.workspace,
            self.workspace.runtime_seed,
            include_tree=False,
            tenant_id="tenant-a",
        )
        self.assertEqual(
            set_mode.call_args_list,
            [
                call(
                    db,
                    self.workspace.workspace,
                    runtime_seed=self.workspace.runtime_seed,
                    mode="agent",
                    tenant_id="tenant-a",
                    flush=True,
                ),
                call(
                    db,
                    self.workspace.workspace,
                    runtime_seed=self.workspace.runtime_seed,
                    mode="host",
                    tenant_id="tenant-a",
                    flush=True,
                ),
            ],
        )
        self.assertIn("post-condition", "\n".join(getattr(captured.exception, "__notes__", [])))

    def test_action_failure_keeps_original_exception_and_adds_recovery_note(self) -> None:
        db = object()
        action_error = ValueError("boom")
        recovery_error = RuntimeError("failed to restore host mode")
        with patch("iruka_vfs.service.ensure_virtual_workspace", return_value={"tree": ""}) as ensure_workspace:
            with patch("iruka_vfs.service.set_workspace_access_mode", side_effect=["agent", recovery_error]) as set_mode:
                with patch("iruka_vfs.service.run_virtual_bash", side_effect=action_error):
                    with self.assertRaisesRegex(ValueError, "boom") as captured:
                        self.workspace.run(db, "pwd")
        ensure_workspace.assert_called_once_with(
            db,
            self.workspace.workspace,
            self.workspace.runtime_seed,
            include_tree=False,
            tenant_id="tenant-a",
        )
        self.assertEqual(
            set_mode.call_args_list,
            [
                call(
                    db,
                    self.workspace.workspace,
                    runtime_seed=self.workspace.runtime_seed,
                    mode="agent",
                    tenant_id="tenant-a",
                    flush=True,
                ),
                call(
                    db,
                    self.workspace.workspace,
                    runtime_seed=self.workspace.runtime_seed,
                    mode="host",
                    tenant_id="tenant-a",
                    flush=True,
                ),
            ],
        )
        self.assertIn("failed to restore host mode", "\n".join(getattr(captured.exception, "__notes__", [])))
