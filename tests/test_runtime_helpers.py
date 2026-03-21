from __future__ import annotations

import unittest

from tests.support import configure_test_dependencies

configure_test_dependencies()

from iruka_vfs.runtime.editing import apply_unified_patch
from iruka_vfs.runtime.search import safe_compile, search_text_lines


class RuntimeHelpersTest(unittest.TestCase):
    def test_search_text_lines_falls_back_for_invalid_regex(self) -> None:
        matches = search_text_lines("alpha\nbeta\nalphabet", "[")
        self.assertEqual(matches, [])

    def test_search_text_lines_uses_regex_when_valid(self) -> None:
        matches = search_text_lines("alpha\nbeta\nalphabet", r"alpha.*")
        self.assertEqual(matches, ["alpha", "alphabet"])

    def test_safe_compile_returns_none_for_invalid_regex(self) -> None:
        self.assertIsNone(safe_compile("["))

    def test_apply_unified_patch_updates_content(self) -> None:
        before = "hello\nworld\n"
        diff = "@@ -1,2 +1,2 @@\n hello\n-world\n+iruka"
        after, conflicts = apply_unified_patch(before, diff)
        self.assertEqual(after, "hello\niruka\n")
        self.assertEqual(conflicts, [])

    def test_apply_unified_patch_returns_conflict_on_context_mismatch(self) -> None:
        before = "hello\nworld\n"
        diff = "@@ -1,2 +1,2 @@\n hello\n-wrong\n+iruka"
        after, conflicts = apply_unified_patch(before, diff)
        self.assertEqual(after, before)
        self.assertEqual(conflicts[0]["reason"], "remove mismatch")

    def test_apply_unified_patch_returns_conflict_on_invalid_header(self) -> None:
        before = "hello\nworld\n"
        diff = "@@ invalid @@\n hello\n-world\n+iruka"
        after, conflicts = apply_unified_patch(before, diff)
        self.assertEqual(after, before)
        self.assertEqual(conflicts[0]["reason"], "invalid hunk header")


if __name__ == "__main__":
    unittest.main()
