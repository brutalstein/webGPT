from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from os_agent.providers.gemini_chrome.client import GeminiClient  # noqa: E402


class GeminiClientStateTests(unittest.TestCase):
    def test_turkish_block_message_is_known(self):
        self.assertIn("oturumunuz açılamadı", GeminiClient.BLOCKED_SIGNIN_PATTERNS)
        self.assertIn("bu tarayıcı veya uygulama güvenli olmayabilir", GeminiClient.BLOCKED_SIGNIN_PATTERNS)

    def test_english_block_message_is_known(self):
        self.assertIn("couldn't sign you in", GeminiClient.BLOCKED_SIGNIN_PATTERNS)
        self.assertIn("this browser or app may not be secure", GeminiClient.BLOCKED_SIGNIN_PATTERNS)


if __name__ == "__main__":
    unittest.main()
