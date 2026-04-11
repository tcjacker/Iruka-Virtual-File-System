from __future__ import annotations

from contextlib import ExitStack
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

        with ExitStack() as stack:
            stack.enter_context(patch("iruka_vfs.service_ops.file_api.assert_workspace_tenant", return_value="tenant-a"))
            stack.enter_context(patch("iruka_vfs.service_ops.file_api.ensure_async_log_worker"))
            stack.enter_context(patch("iruka_vfs.service_ops.file_api.ensure_workspace_checkpoint_worker"))
            stack.enter_context(patch("iruka_vfs.service_ops.file_api.ensure_mem_cache_worker"))
            stack.enter_context(patch("iruka_vfs.service_ops.file_api.ensure_virtual_workspace"))
            stack.enter_context(patch("iruka_vfs.service_ops.file_api.assert_workspace_access_mode"))
            stack.enter_context(patch("iruka_vfs.service_ops.file_api.get_workspace_mirror", return_value=mirror))
            stack.enter_context(patch("iruka_vfs.service_ops.file_api.workspace_lock", return_value=(_DummyLock(), "mirror-key")))
            stack.enter_context(patch("iruka_vfs.service_ops.file_api.set_active_workspace_tenant"))
            stack.enter_context(patch("iruka_vfs.service_ops.file_api.set_active_workspace_scope"))
            stack.enter_context(patch("iruka_vfs.service_ops.file_api.set_active_workspace_mirror"))
            stack.enter_context(patch("iruka_vfs.service_ops.file_api.set_workspace_mirror"))
            stack.enter_context(patch("iruka_vfs.service_ops.file_api.mirror_has_dirty_state", return_value=False))
            stack.enter_context(patch("iruka_vfs.service_ops.file_api.must_get_node", return_value=mirror.nodes[1]))
            stack.enter_context(patch("iruka_vfs.service_ops.file_api.node_path", return_value="/workspace"))
            stack.enter_context(patch("iruka_vfs.service_ops.file_api.run_command_chain", return_value=result))
            stack.enter_context(
                patch("iruka_vfs.service_ops.file_api.truncate_for_log", side_effect=lambda text, limit: (text, {"truncated": False}))
            )
            stack.enter_context(patch("iruka_vfs.service_ops.file_api.prepare_log_artifacts", return_value={}))
            stack.enter_context(patch("iruka_vfs.service_ops.file_api._repositories", repositories))
            stack.enter_context(patch("iruka_vfs.service_ops.file_api.ASYNC_COMMAND_LOGGING", False))
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

    def test_run_virtual_bash_executes_heredoc_write_without_command_parser(self) -> None:
        workspace = DummyWorkspace(id=7, tenant_id="tenant-a", metadata_json={"virtual_access_mode": "agent"})
        mirror = _build_mirror()
        db = SimpleNamespace(get_bind=lambda: None, rollback=lambda: None)
        repositories = SimpleNamespace(command_log=SimpleNamespace(create_command_log=lambda db, payload: 124))
        file_node = SimpleNamespace(id=5, workspace_id=7, tenant_id="tenant-a", node_type="file")
        raw_cmd = "cat > /workspace/site/index.html <<'EOF'\n<html>\nDog Cafe\n</html>\nEOF"

        with ExitStack() as stack:
            stack.enter_context(patch("iruka_vfs.service_ops.file_api.assert_workspace_tenant", return_value="tenant-a"))
            stack.enter_context(patch("iruka_vfs.service_ops.file_api.ensure_async_log_worker"))
            stack.enter_context(patch("iruka_vfs.service_ops.file_api.ensure_workspace_checkpoint_worker"))
            stack.enter_context(patch("iruka_vfs.service_ops.file_api.ensure_mem_cache_worker"))
            stack.enter_context(patch("iruka_vfs.service_ops.file_api.ensure_virtual_workspace"))
            stack.enter_context(patch("iruka_vfs.service_ops.file_api.assert_workspace_access_mode"))
            stack.enter_context(patch("iruka_vfs.service_ops.file_api.get_workspace_mirror", return_value=mirror))
            stack.enter_context(patch("iruka_vfs.service_ops.file_api.workspace_lock", return_value=(_DummyLock(), "mirror-key")))
            stack.enter_context(patch("iruka_vfs.service_ops.file_api.set_active_workspace_tenant"))
            stack.enter_context(patch("iruka_vfs.service_ops.file_api.set_active_workspace_scope"))
            stack.enter_context(patch("iruka_vfs.service_ops.file_api.set_active_workspace_mirror"))
            stack.enter_context(patch("iruka_vfs.service_ops.file_api.set_workspace_mirror"))
            stack.enter_context(patch("iruka_vfs.service_ops.file_api.mirror_has_dirty_state", return_value=False))
            stack.enter_context(patch("iruka_vfs.service_ops.file_api.must_get_node", return_value=mirror.nodes[1]))
            stack.enter_context(
                patch(
                    "iruka_vfs.service_ops.file_api.node_path",
                    side_effect=lambda db, node: (
                        "/workspace"
                        if node.id == 1
                        else "/workspace/site"
                        if node.id == 3
                        else "/workspace/site/index.html"
                    ),
                )
            )
            stack.enter_context(patch("iruka_vfs.service_ops.file_api.resolve_path", return_value=None))
            stack.enter_context(
                patch("iruka_vfs.service_ops.file_api.resolve_parent_for_create", return_value=(SimpleNamespace(id=3), "index.html"))
            )
            stack.enter_context(patch("iruka_vfs.service_ops.file_api.allow_write_path", return_value=(True, "")))
            run_command_chain = stack.enter_context(
                patch(
                    "iruka_vfs.service_ops.file_api.run_command_chain",
                    return_value=VirtualCommandResult("", "parse error: input redirect < is not supported", 2, {}),
                )
            )
            get_or_create_child_file = stack.enter_context(
                patch("iruka_vfs.service_ops.file_api.get_or_create_child_file", return_value=file_node, create=True)
            )
            write_file = stack.enter_context(
                patch("iruka_vfs.service_ops.file_api.write_file", return_value=2, create=True)
            )
            stack.enter_context(
                patch("iruka_vfs.service_ops.file_api.truncate_for_log", side_effect=lambda text, limit: (text, {"truncated": False}))
            )
            stack.enter_context(
                patch("iruka_vfs.service_ops.file_api.prepare_log_artifacts", side_effect=lambda payload, max_chars: payload)
            )
            stack.enter_context(patch("iruka_vfs.service_ops.file_api._repositories", repositories))
            stack.enter_context(patch("iruka_vfs.service_ops.file_api.ASYNC_COMMAND_LOGGING", False))
            payload = run_virtual_bash(
                db,
                workspace,
                raw_cmd,
                runtime_seed=SimpleNamespace(),
                tenant_id="tenant-a",
            )

        self.assertEqual(payload["exit_code"], 0)
        self.assertEqual(payload["stdout"], "")
        self.assertEqual(payload["stderr"], "")
        self.assertEqual(payload["cwd"], "/workspace")
        self.assertEqual(payload["artifacts"]["protocol"], "heredoc_write")
        self.assertEqual(payload["artifacts"]["path"], "/workspace/site/index.html")
        self.assertEqual(payload["artifacts"]["mode"], "write")
        self.assertEqual(payload["artifacts"]["content_bytes"], len("<html>\nDog Cafe\n</html>\n"))
        run_command_chain.assert_not_called()
        get_or_create_child_file.assert_called_once()
        write_file.assert_called_once()


if __name__ == "__main__":
    unittest.main()
