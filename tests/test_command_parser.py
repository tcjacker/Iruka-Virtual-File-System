from __future__ import annotations

import unittest

from iruka_vfs.command_parser import parse_pipeline_and_redirect, split_chain


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
        self.assertEqual(parsed["redirect"], {"op": ">", "path": "out.txt", "force": False})

    def test_parse_heredoc_redirect(self) -> None:
        parsed, error = parse_pipeline_and_redirect("cat <<'EOF' > out.txt\nhello\nworld\nEOF")
        self.assertIsNone(error)
        self.assertEqual(parsed["pipeline"], [["cat"]])
        self.assertEqual(parsed["redirect"], {"op": ">", "path": "out.txt", "force": False})
        self.assertEqual(parsed["stdin_text"], "hello\nworld\n")

    def test_split_chain_preserves_heredoc_body(self) -> None:
        pieces = split_chain("mkdir -p /workspace/characters && cat <<'EOF' > /workspace/characters/ch1.md\n# 第一章\nEOF")
        self.assertEqual(
            pieces,
            [
                {"op": ";", "cmd": "mkdir -p /workspace/characters"},
                {"op": "&&", "cmd": "cat <<'EOF' > /workspace/characters/ch1.md\n# 第一章\nEOF"},
            ],
        )

    def test_rejects_or_operator_with_explicit_error(self) -> None:
        parsed, error = parse_pipeline_and_redirect("cat a.txt || cat b.txt")
        self.assertEqual(parsed, {})
        self.assertEqual(error, "parse error: || is not supported; use && or ;")

    def test_rejects_input_redirect_with_explicit_error(self) -> None:
        parsed, error = parse_pipeline_and_redirect("cat < input.txt")
        self.assertEqual(parsed, {})
        self.assertEqual(error, "parse error: input redirect < is not supported")

    def test_rejects_stderr_redirect_with_explicit_error(self) -> None:
        parsed, error = parse_pipeline_and_redirect("cat a.txt 2> err.txt")
        self.assertEqual(parsed, {})
        self.assertEqual(
            error,
            "parse error: 1>/2> redirects are not supported; only >, >>, >|, and 2>&1 are supported",
        )

    def test_rejects_command_substitution_with_explicit_error(self) -> None:
        parsed, error = parse_pipeline_and_redirect("echo $(pwd)")
        self.assertEqual(parsed, {})
        self.assertEqual(
            error,
            "parse error: command substitution is not supported; use plain commands only",
        )


if __name__ == "__main__":
    unittest.main()
