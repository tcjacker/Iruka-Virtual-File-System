from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.support import DummyWorkspace, configure_test_dependencies

configure_test_dependencies()

from iruka_vfs.models import VirtualCommandResult, WorkspaceMirror
from iruka_vfs.service_ops.file_api import run_virtual_bash


class _DummyLock:
    def acquire(self, blocking: bool = True) -> bool:
        return True

    def release(self) -> None:
        return None


def _build_mirror() -> WorkspaceMirror:
    return WorkspaceMirror(
        tenant_key="tenant-a",
        scope_key="scope-a",
        workspace_id=7,
        root_id=1,
        session_id=11,
        cwd_node_id=1,
        nodes={1: SimpleNamespace(id=1, name="workspace", parent_id=None, node_type="dir")},
        path_to_id={"/workspace": 1},
        children_by_parent={None: [1]},
        workspace_metadata={},
        lock=_DummyLock(),
    )


class RunVirtualBashLoggingTest(unittest.TestCase):
    def test_run_virtual_bash_logs_failed_command(self) -> None:
        workspace = DummyWorkspace(id=7, tenant_id="tenant-a", metadata_json={"virtual_access_mode": "agent"})
        mirror = _build_mirror()
        result = VirtualCommandResult(stdout="", stderr="unsupported command: badcmd", exit_code=127, artifacts={})
        db = SimpleNamespace(get_bind=lambda: None, rollback=lambda: None)
        repositories = SimpleNamespace(command_log=SimpleNamespace(create_command_log=lambda db, payload: 123))

        with patch("iruka_vfs.service_ops.file_api.assert_workspace_tenant", return_value="tenant-a"), patch(
            "iruka_vfs.service_ops.file_api.ensure_async_log_worker"
        ), patch("iruka_vfs.service_ops.file_api.ensure_workspace_checkpoint_worker"), patch(
            "iruka_vfs.service_ops.file_api.ensure_mem_cache_worker"
        ), patch("iruka_vfs.service_ops.file_api.ensure_virtual_workspace"), patch(
            "iruka_vfs.service_ops.file_api.assert_workspace_access_mode"
        ), patch(
            "iruka_vfs.service_ops.file_api.get_workspace_mirror", return_value=mirror
        ), patch(
            "iruka_vfs.service_ops.file_api.workspace_lock", return_value=(_DummyLock(), "mirror-key")
        ), patch(
            "iruka_vfs.service_ops.file_api.set_active_workspace_tenant"
        ), patch(
            "iruka_vfs.service_ops.file_api.set_active_workspace_scope"
        ), patch(
            "iruka_vfs.service_ops.file_api.set_active_workspace_mirror"
        ), patch(
            "iruka_vfs.service_ops.file_api.set_workspace_mirror"
        ), patch(
            "iruka_vfs.service_ops.file_api.mirror_has_dirty_state", return_value=False
        ), patch(
            "iruka_vfs.service_ops.file_api.must_get_node", return_value=mirror.nodes[1]
        ), patch(
            "iruka_vfs.service_ops.file_api.node_path", return_value="/workspace"
        ), patch(
            "iruka_vfs.service_ops.file_api.run_command_chain", return_value=result
        ), patch(
            "iruka_vfs.service_ops.file_api.truncate_for_log", side_effect=lambda text, limit: (text, {"truncated": False})
        ), patch(
            "iruka_vfs.service_ops.file_api.prepare_log_artifacts", return_value={}
        ), patch(
            "iruka_vfs.service_ops.file_api._repositories", repositories
        ), patch(
            "iruka_vfs.service_ops.file_api.ASYNC_COMMAND_LOGGING", False
        ):
            with self.assertLogs("iruka_vfs.service_ops.file_api", level="ERROR") as captured:
                payload = run_virtual_bash(
                    db,
                    workspace,
                    "badcmd --flag",
                    runtime_seed=SimpleNamespace(),
                    tenant_id="tenant-a",
                )

        self.assertEqual(payload["exit_code"], 127)
        self.assertIn("badcmd --flag", "\n".join(captured.output))
        self.assertIn("tenant-a", "\n".join(captured.output))
        self.assertIn("workspace_id=7", "\n".join(captured.output))


if __name__ == "__main__":
    unittest.main()
