from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.support import DummyWorkspace, configure_test_dependencies

configure_test_dependencies()

from iruka_vfs.service_ops.access_mode import (
    assert_workspace_access_mode,
    assert_workspace_readable,
    workspace_access_mode_for_runtime,
)
from iruka_vfs.service_ops.bootstrap import workspace_access_mode_from_metadata


class ServiceOpsAccessModeTest(unittest.TestCase):
    def test_workspace_access_mode_from_metadata_defaults_to_host(self) -> None:
        self.assertEqual(workspace_access_mode_from_metadata(None), "host")
        self.assertEqual(workspace_access_mode_from_metadata({"virtual_access_mode": "invalid"}), "host")

    def test_workspace_access_mode_for_runtime_reads_workspace_metadata_without_mirror(self) -> None:
        workspace = DummyWorkspace(id=7, tenant_id="tenant-a", metadata_json={"virtual_access_mode": "agent"})
        with patch("iruka_vfs.service_ops.access_mode.get_workspace_mirror", return_value=None):
            mode = workspace_access_mode_for_runtime(workspace, 7, "tenant-a")
        self.assertEqual(mode, "agent")

    def test_assert_workspace_access_mode_raises_on_mismatch(self) -> None:
        workspace = DummyWorkspace(id=9, tenant_id="tenant-a", metadata_json={"virtual_access_mode": "host"})
        with patch("iruka_vfs.service_ops.access_mode.get_workspace_mirror", return_value=None):
            with self.assertRaises(PermissionError):
                assert_workspace_access_mode(
                    workspace,
                    tenant_key="tenant-a",
                    required_mode="agent",
                )

    def test_assert_workspace_readable_allows_agent_mode(self) -> None:
        workspace = DummyWorkspace(id=10, tenant_id="tenant-a", metadata_json={"virtual_access_mode": "agent"})
        with patch("iruka_vfs.service_ops.access_mode.get_workspace_mirror", return_value=None):
            mode = assert_workspace_readable(
                workspace,
                tenant_key="tenant-a",
            )
        self.assertEqual(mode, "agent")


if __name__ == "__main__":
    unittest.main()
