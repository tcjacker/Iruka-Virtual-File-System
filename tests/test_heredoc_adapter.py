from __future__ import annotations

import unittest

from tests.support import configure_test_dependencies

configure_test_dependencies()

from iruka_vfs.service_ops import file_api


class HeredocAdapterTest(unittest.TestCase):
    def test_parse_heredoc_write_command_extracts_raw_body(self) -> None:
        parser = getattr(file_api, "parse_heredoc_write_command", None)
        self.assertIsNotNone(parser)

        parsed = parser(
            "cat > /workspace/site/index.html <<'EOF'\n"
            "<html>\n"
            "  <div>$NOT_EXPANDED && still literal</div>\n"
            "</html>\n"
            "EOF"
        )

        self.assertEqual(parsed.mode, "write")
        self.assertEqual(parsed.path, "/workspace/site/index.html")
        self.assertEqual(parsed.delimiter, "EOF")
        self.assertEqual(
            parsed.content,
            "<html>\n  <div>$NOT_EXPANDED && still literal</div>\n</html>\n",
        )

    def test_parse_heredoc_write_command_rejects_missing_terminator(self) -> None:
        parser = getattr(file_api, "parse_heredoc_write_command", None)
        self.assertIsNotNone(parser)

        with self.assertRaisesRegex(ValueError, "missing heredoc terminator"):
            parser("cat > /workspace/site/index.html <<'EOF'\n<html>\n")


if __name__ == "__main__":
    unittest.main()
