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


if __name__ == "__main__":
    unittest.main()
