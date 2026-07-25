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
from os_agent.providers.gemini_chrome.config import GeminiChromeSettings  # noqa: E402


class GeminiProfileTests(unittest.TestCase):
    def test_previous_profile_is_reused(self):
        with tempfile.TemporaryDirectory() as temp:
            local = Path(temp)
            previous = local / "GeminiTerminalAgent" / "chrome-profile"
            previous.mkdir(parents=True)
            with patch.dict(os.environ, {"LOCALAPPDATA": str(local)}):
                app = load_config(ROOT / "config.json")
                settings = GeminiChromeSettings.from_settings(app, app.provider("gemini"))
                self.assertEqual(settings.profile_dir, previous)

    def test_os_profile_is_used_without_previous_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            local = Path(temp)
            with patch.dict(os.environ, {"LOCALAPPDATA": str(local)}):
                app = load_config(ROOT / "config.json")
                settings = GeminiChromeSettings.from_settings(app, app.provider("gemini"))
                self.assertEqual(
                    settings.profile_dir,
                    local / "OS" / "browser-profiles" / "gemini-chrome",
                )


if __name__ == "__main__":
    unittest.main()
