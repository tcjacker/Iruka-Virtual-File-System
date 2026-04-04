from __future__ import annotations

import unittest

from iruka_vfs.command_parser import parse_pipeline_and_redirect, split_chain
from tests.support import configure_test_dependencies

configure_test_dependencies()

from iruka_vfs.runtime.executor import _unsupported_command_error


class CommandParserTest(unittest.TestCase):
    def test_split_chain_preserves_and_operator(self) -> None:
        pieces = split_chain("echo hi && cat file.txt")
        self.assertEqual(
            pieces,
            [
                {"op": ";", "cmd": "echo hi"},
                {"op": "&&", "cmd": "cat file.txt"},
            ],
        )

    def test_parse_pipeline_and_redirect(self) -> None:
        parsed, error = parse_pipeline_and_redirect("cat a.txt | rg hello > out.txt")
        self.assertIsNone(error)
        self.assertEqual(parsed["pipeline"], [["cat", "a.txt"], ["rg", "hello"]])
        self.assertEqual(parsed["redirect"], {"op": ">", "path": "out.txt"})

    def test_parse_pipeline_rejects_input_redirect(self) -> None:
        parsed, error = parse_pipeline_and_redirect("cat < a.txt")
        self.assertEqual(parsed, {})
        self.assertEqual(error, "parse error: input redirect < is not supported; use cat FILE | COMMAND instead")

    def test_parse_pipeline_surfaces_quote_repair_hint(self) -> None:
        parsed, error = parse_pipeline_and_redirect('cat "a.txt')
        self.assertEqual(parsed, {})
        self.assertEqual(error, "parse error: No closing quotation. Close all quotes before retrying")

    def test_unsupported_command_error_hints_for_trailing_punctuation(self) -> None:
        error = _unsupported_command_error("status:")
        self.assertIn("unsupported command: status:", error)
        self.assertIn("remove trailing punctuation", error)
        self.assertIn("supported commands:", error)


if __name__ == "__main__":
    unittest.main()
