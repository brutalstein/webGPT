from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from os_agent.config import load_config  # noqa: E402


class ConfigTests(unittest.TestCase):
    def test_project_config(self):
        config = load_config(ROOT / "config.json")
        self.assertEqual(config.app_name, "OS")
        self.assertEqual(config.default_provider, "gemini")
        self.assertEqual(config.provider("chatgpt").expected_email, "ebru112263gundes@gmail.com")
        self.assertEqual(config.provider("gemini").expected_email, "willieewonka224@gmail.com")
        self.assertEqual(config.provider("gemini").kind, "gemini_chrome_cdp")
        self.assertFalse(config.inject_local_memory)


if __name__ == "__main__":
    unittest.main()
