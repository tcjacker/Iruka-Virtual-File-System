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
        self.assertEqual(
            error,
            "parse error: unsupported `|| cat b.txt` fallback. "
            "Supported forms are `|| true`, `|| :`, and `|| help`. "
            "Otherwise remove the `|| ...` tail and run the main command directly, or rewrite it as `;` / `&&` explicitly.",
        )

    def test_rejects_input_redirect_with_explicit_error(self) -> None:
        parsed, error = parse_pipeline_and_redirect("cat < input.txt")
        self.assertEqual(parsed, {})
        self.assertEqual(error, "parse error: input redirect < is not supported")

    def test_rejects_stderr_redirect_with_explicit_error(self) -> None:
        parsed, error = parse_pipeline_and_redirect("cat a.txt 2> err.txt")
        self.assertEqual(parsed, {})
        self.assertEqual(
            error,
            "parse error: general 1>/2> redirects are not supported. "
            "Use `2>/dev/null` to discard stderr, `2>&1` to merge stderr into stdout, "
            "or remove the stderr redirect tail and run the main command directly.",
        )

    def test_rejects_command_substitution_with_explicit_error(self) -> None:
        parsed, error = parse_pipeline_and_redirect("echo $(pwd)")
        self.assertEqual(parsed, {})
        self.assertEqual(
            error,
            "parse error: command substitution is not supported; use plain commands only",
        )

    def test_parse_here_string_into_stdin(self) -> None:
        parsed, error = parse_pipeline_and_redirect("grep demo <<< 'alpha demo beta'")
        self.assertIsNone(error)
        self.assertEqual(parsed["pipeline"], [["grep", "demo"]])
        self.assertEqual(parsed["stdin_text"], "alpha demo beta\n")

    def test_rejects_here_string_command_substitution_with_guidance(self) -> None:
        parsed, error = parse_pipeline_and_redirect("grep demo <<< $(cat file.txt)")
        self.assertEqual(parsed, {})
        self.assertEqual(
            error,
            "parse error: here-string command substitution is not supported. "
            "Use `cat <file> | <command>`, `echo <text> | <command>`, or `cat <<'EOF' | <command>` instead.",
        )


if __name__ == "__main__":
    unittest.main()
