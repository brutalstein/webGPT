from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from os_agent.config import load_config  # noqa: E402
from os_agent.errors import ConfigurationError  # noqa: E402


class ConfigTests(unittest.TestCase):
    def test_project_config(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"LOCALAPPDATA": temp}):
            config = load_config(ROOT / "config.json")
            self.assertEqual(config.app_name, "OS")
            self.assertEqual(config.default_provider, "gemini")
            self.assertEqual(config.provider("gemini").expected_email, "willieewonka224@gmail.com")
            self.assertEqual(config.provider("gemini").kind, "gemini_chrome_cdp")
            self.assertFalse(config.inject_local_memory)
            self.assertEqual(config.database_path.name, "os-state.db")

            chatgpt = config.provider("chatgpt")
            self.assertTrue(chatgpt.enabled)
            self.assertEqual(chatgpt.kind, "chatgpt_manual_web")
            self.assertEqual(chatgpt.get("interaction_mode"), "background_companion")
            self.assertTrue(chatgpt.get("background_idle"))

    def test_unknown_provider_is_not_selectable(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"LOCALAPPDATA": temp}):
            config = load_config(ROOT / "config.json")
            with self.assertRaises(ConfigurationError):
                config.provider("unknown-provider")


if __name__ == "__main__":
    unittest.main()
