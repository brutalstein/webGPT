from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from os_agent.browser.manager import PersistentBrowser  # noqa: E402
from os_agent.config import load_config  # noqa: E402
from os_agent.providers.chatgpt_companion import (  # noqa: E402
    ChatGPTCompanionSettings,
    ChatGPTWindowController,
    ClipboardExchange,
)


class ChatGPTCompanionTests(unittest.TestCase):
    def test_companion_defaults_are_enabled(self):
        config = load_config(ROOT / "config.json")
        settings = ChatGPTCompanionSettings.from_provider(config.provider("chatgpt"))
        self.assertTrue(settings.background_idle)
        self.assertTrue(settings.restore_for_interaction)
        self.assertTrue(settings.minimize_after_exchange)
        self.assertEqual(settings.clipboard_retry_count, 3)

    def test_clipboard_candidate_rejects_stale_values(self):
        self.assertFalse(
            ClipboardExchange.is_response_candidate("", prompt="soru", previous="önceki")
        )
        self.assertFalse(
            ClipboardExchange.is_response_candidate("soru", prompt="soru", previous="önceki")
        )
        self.assertFalse(
            ClipboardExchange.is_response_candidate("önceki", prompt="soru", previous="önceki")
        )
        self.assertTrue(
            ClipboardExchange.is_response_candidate("yeni yanıt", prompt="soru", previous="önceki")
        )
        self.assertFalse(
            ClipboardExchange.is_response_candidate(
                "yeni yanıt",
                prompt="soru",
                previous="önceki",
                clipboard_changed=False,
            )
        )
        self.assertTrue(
            ClipboardExchange.is_response_candidate(
                "önceki",
                prompt="soru",
                previous="önceki",
                clipboard_changed=True,
            )
        )

    def test_window_title_ranking_prefers_chatgpt(self):
        self.assertLess(
            ChatGPTWindowController._window_rank("ChatGPT - Google Chrome"),
            ChatGPTWindowController._window_rank("Google Chrome"),
        )

    def test_browser_extra_args_are_filtered(self):
        config = load_config(ROOT / "config.json")
        settings = config.provider("chatgpt")
        browser = PersistentBrowser(config, settings, playwright=None)  # type: ignore[arg-type]
        self.assertIn("--start-minimized", browser._safe_extra_args())

        settings.raw["browser_args"] = [
            "--start-minimized",
            "--no-sandbox",
            "--remote-debugging-port=9222",
            "not-an-arg",
        ]
        self.assertEqual(browser._safe_extra_args(), ["--start-minimized"])

    def test_native_window_controller_is_safe_off_windows(self):
        controller = ChatGPTWindowController(Path("/tmp/nonexistent-profile"))
        if os.name != "nt":
            self.assertFalse(controller.supported)
            self.assertFalse(controller.minimize())
            self.assertFalse(controller.restore_and_focus())


if __name__ == "__main__":
    unittest.main()
