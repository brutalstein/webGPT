from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from os_agent.core.commands import parse_command  # noqa: E402


class CommandTests(unittest.TestCase):
    def test_use_command(self):
        command = parse_command("/use chatgpt")
        self.assertIsNotNone(command)
        self.assertEqual(command.name, "use")
        self.assertEqual(command.argument, "chatgpt")

    def test_plain_prompt(self):
        self.assertIsNone(parse_command("merhaba"))


if __name__ == "__main__":
    unittest.main()
