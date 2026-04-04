from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.support import configure_test_dependencies

configure_test_dependencies()

from iruka_vfs import service
from iruka_vfs.runtime.executor import exec_argv


class RuntimeExecutorTest(unittest.TestCase):
    def test_exec_argv_sed_prints_inclusive_line_range(self) -> None:
        session = SimpleNamespace(workspace_id=1, cwd_node_id=10)
        node = SimpleNamespace(id=11, node_type="file")
        with patch.object(service, "_resolve_path", return_value=node), patch.object(
            service,
            "_get_node_content",
            return_value="line1\nline2\nline3\nline4\n",
        ):
            result = exec_argv(None, session, ["sed", "-n", "2,3p", "notes.txt"])
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, "line2\nline3")

    def test_exec_argv_sed_clamps_end_line_to_file_length(self) -> None:
        session = SimpleNamespace(workspace_id=1, cwd_node_id=10)
        node = SimpleNamespace(id=11, node_type="file")
        with patch.object(service, "_resolve_path", return_value=node), patch.object(
            service,
            "_get_node_content",
            return_value="line1\nline2\nline3\n",
        ):
            result = exec_argv(None, session, ["sed", "-n", "2,9p", "notes.txt"])
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, "line2\nline3")

    def test_exec_argv_sed_rejects_invalid_expression(self) -> None:
        session = SimpleNamespace(workspace_id=1, cwd_node_id=10)
        result = exec_argv(None, session, ["sed", "-n", "3p", "notes.txt"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("supported form", result.stderr)

    def test_exec_argv_sed_rejects_missing_file(self) -> None:
        session = SimpleNamespace(workspace_id=1, cwd_node_id=10)
        with patch.object(service, "_resolve_path", return_value=None):
            result = exec_argv(None, session, ["sed", "-n", "1,2p", "missing.txt"])
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.stderr, "sed: missing.txt: No such file")


if __name__ == "__main__":
    unittest.main()
