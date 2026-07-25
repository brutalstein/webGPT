from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from os_agent.providers.gemini_chrome.browser import GeminiBrowserController  # noqa: E402
from os_agent.providers.gemini_chrome.setup import ManualGeminiSetup  # noqa: E402


class GeminiBrowserSafetyTests(unittest.TestCase):
    def test_cdp_launch_does_not_use_no_sandbox(self):
        source = inspect.getsource(GeminiBrowserController._launch_cdp)
        self.assertNotIn('"--no-sandbox"', source)
        self.assertIn('"--remote-debugging-address=127.0.0.1"', source)
        self.assertIn('f"--user-data-dir={profile}"', source)

    def test_persistent_fallback_enables_sandbox(self):
        source = inspect.getsource(GeminiBrowserController._launch_persistent)
        self.assertIn("chromium_sandbox=True", source)
        self.assertNotIn('"--no-sandbox"', source)

    def test_manual_setup_has_no_automation_or_debugging_flags(self):
        source = inspect.getsource(ManualGeminiSetup.run)
        self.assertNotIn("sync_playwright", source)
        self.assertNotIn("remote-debugging-port", source)
        self.assertNotIn('"--no-sandbox"', source)

    def test_single_page_selection_closes_extras(self):
        source = inspect.getsource(GeminiBrowserController._choose_single_page)
        self.assertIn('"gemini.google.com" in page.url', source)
        self.assertIn("page.close()", source)


if __name__ == "__main__":
    unittest.main()
