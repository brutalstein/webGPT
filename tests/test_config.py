from __future__ import annotations

import json
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
            self.assertEqual(config.provider("chatgpt").kind, "openai_responses_api")
            self.assertEqual(config.provider("chatgpt").preferred_browser, "none")
            self.assertTrue(config.providers["chatgpt"].enabled)
            self.assertFalse(config.inject_local_memory)
            self.assertEqual(config.database_path.name, "os-state.db")

    def test_disabled_provider_is_not_selectable(self):
        raw = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        raw["providers"]["chatgpt"]["enabled"] = False
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"LOCALAPPDATA": temp}):
            path = Path(temp) / "config.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            config = load_config(path)
            with self.assertRaises(ConfigurationError):
                config.provider("chatgpt")


if __name__ == "__main__":
    unittest.main()
