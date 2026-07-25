from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class ProjectLayoutTests(unittest.TestCase):
    def test_root_has_single_windows_entrypoint(self):
        batch_files = sorted(path.name for path in ROOT.glob("*.bat"))
        self.assertEqual(batch_files, ["os.bat"])

    def test_modern_cli_has_only_three_chat_commands(self):
        source = (ROOT / "src/os_agent/ui/app.py").read_text(encoding="utf-8")
        self.assertIn("/menu · /new · /exit", source)
        self.assertNotIn("/resume", source)
        self.assertNotIn("/sessions", source)


if __name__ == "__main__":
    unittest.main()
