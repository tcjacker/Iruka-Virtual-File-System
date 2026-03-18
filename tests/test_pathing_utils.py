from __future__ import annotations

import unittest

from tests.support import configure_test_dependencies

configure_test_dependencies()

from iruka_vfs.pathing.utils import path_is_under


class PathingUtilsTest(unittest.TestCase):
    def test_path_is_under_accepts_descendant(self) -> None:
        self.assertTrue(path_is_under("/workspace/docs/a.md", "/workspace"))

    def test_path_is_under_rejects_sibling_prefix(self) -> None:
        self.assertFalse(path_is_under("/workspace-other/docs/a.md", "/workspace"))

    def test_path_is_under_accepts_same_path(self) -> None:
        self.assertTrue(path_is_under("/workspace", "/workspace"))


if __name__ == "__main__":
    unittest.main()
