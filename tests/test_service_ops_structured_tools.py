from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.support import DummyWorkspace, configure_test_dependencies

configure_test_dependencies()

from iruka_vfs.service_ops.file_api import tool_edit_workspace_file, tool_write_workspace_file


class StructuredToolServiceOpsTest(unittest.TestCase):
    def test_tool_write_workspace_file_returns_structured_result(self) -> None:
        workspace = DummyWorkspace(id=7, tenant_id="tenant-a", metadata_json={"virtual_access_mode": "host"})
        db = object()
        session = SimpleNamespace(workspace_id=7, cwd_node_id=1)

        with patch("iruka_vfs.service_ops.file_api.assert_workspace_tenant", return_value="tenant-a"), patch(
            "iruka_vfs.service_ops.file_api.ensure_virtual_workspace"
        ), patch("iruka_vfs.service_ops.file_api.assert_workspace_access_mode"), patch(
            "iruka_vfs.service_ops.file_api.workspace_scope_for_db", return_value="scope-a"
        ), patch(
            "iruka_vfs.service_ops.file_api.get_or_create_session", return_value=session
        ), patch(
            "iruka_vfs.service_ops.file_api.allow_write_path", return_value=(True, "")
        ), patch(
            "iruka_vfs.service_ops.file_api.seed_workspace_file",
            return_value={"path": "/workspace/docs/generated.md", "version": 3, "created": True},
        ):
            payload = tool_write_workspace_file(
                db,
                workspace,
                "/workspace/docs/generated.md",
                "hello from structured write",
                runtime_seed=SimpleNamespace(),
                tenant_id="tenant-a",
            )

        self.assertEqual(payload["operation"], "tool_write")
        self.assertEqual(payload["path"], "/workspace/docs/generated.md")
        self.assertEqual(payload["version"], 3)
        self.assertTrue(payload["created"])
        self.assertEqual(payload["bytes_written"], len("hello from structured write"))

    def test_tool_edit_workspace_file_returns_structured_result(self) -> None:
        workspace = DummyWorkspace(id=7, tenant_id="tenant-a", metadata_json={"virtual_access_mode": "host"})
        db = object()
        session = SimpleNamespace(workspace_id=7, cwd_node_id=1)
        node = SimpleNamespace(id=11, node_type="file", workspace_id=7, tenant_id="tenant-a")

        with patch("iruka_vfs.service_ops.file_api.assert_workspace_tenant", return_value="tenant-a"), patch(
            "iruka_vfs.service_ops.file_api.ensure_virtual_workspace"
        ), patch("iruka_vfs.service_ops.file_api.assert_workspace_access_mode"), patch(
            "iruka_vfs.service_ops.file_api.workspace_scope_for_db", return_value="scope-a"
        ), patch(
            "iruka_vfs.service_ops.file_api.get_or_create_session", return_value=session
        ), patch(
            "iruka_vfs.service_ops.file_api.resolve_path", return_value=node
        ), patch(
            "iruka_vfs.service_ops.file_api.node_path", return_value="/workspace/docs/generated.md"
        ), patch(
            "iruka_vfs.service_ops.file_api.allow_write_path", return_value=(True, "")
        ), patch(
            "iruka_vfs.service_ops.file_api.get_node_content", return_value="hello before world"
        ), patch(
            "iruka_vfs.service_ops.file_api.write_file", return_value=4
        ):
            payload = tool_edit_workspace_file(
                db,
                workspace,
                "/workspace/docs/generated.md",
                "before",
                "after",
                runtime_seed=SimpleNamespace(),
                tenant_id="tenant-a",
            )

        self.assertEqual(payload["operation"], "tool_edit")
        self.assertEqual(payload["path"], "/workspace/docs/generated.md")
        self.assertEqual(payload["version"], 4)
        self.assertEqual(payload["replacements"], 1)

    def test_tool_edit_workspace_file_rejects_multiple_matches_by_default(self) -> None:
        workspace = DummyWorkspace(id=7, tenant_id="tenant-a", metadata_json={"virtual_access_mode": "host"})
        db = object()
        session = SimpleNamespace(workspace_id=7, cwd_node_id=1)
        node = SimpleNamespace(id=11, node_type="file", workspace_id=7, tenant_id="tenant-a")

        with patch("iruka_vfs.service_ops.file_api.assert_workspace_tenant", return_value="tenant-a"), patch(
            "iruka_vfs.service_ops.file_api.ensure_virtual_workspace"
        ), patch("iruka_vfs.service_ops.file_api.assert_workspace_access_mode"), patch(
            "iruka_vfs.service_ops.file_api.workspace_scope_for_db", return_value="scope-a"
        ), patch(
            "iruka_vfs.service_ops.file_api.get_or_create_session", return_value=session
        ), patch(
            "iruka_vfs.service_ops.file_api.resolve_path", return_value=node
        ), patch(
            "iruka_vfs.service_ops.file_api.node_path", return_value="/workspace/docs/generated.md"
        ), patch(
            "iruka_vfs.service_ops.file_api.allow_write_path", return_value=(True, "")
        ), patch(
            "iruka_vfs.service_ops.file_api.get_node_content", return_value="dup value dup"
        ):
            with self.assertRaisesRegex(ValueError, "matches multiple times"):
                tool_edit_workspace_file(
                    db,
                    workspace,
                    "/workspace/docs/generated.md",
                    "dup",
                    "once",
                    runtime_seed=SimpleNamespace(),
                    tenant_id="tenant-a",
                )


if __name__ == "__main__":
    unittest.main()
