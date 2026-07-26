from __future__ import annotations

import inspect
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from os_agent.providers.gemini_chrome import selector_health  # noqa: E402
from os_agent.providers.gemini_chrome.browser import GeminiBrowserController  # noqa: E402
from os_agent.providers.gemini_chrome.client import GeminiClient  # noqa: E402
from os_agent.providers.gemini_chrome.selectors import SelectorRegistry  # noqa: E402
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

    def test_selector_contract_has_valid_fallback_chains(self):
        config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        gemini = config["providers"]["gemini"]
        registry = SelectorRegistry.from_config(gemini.get("selector_contract", {}))
        self.assertEqual(registry.validate_static(), ())
        self.assertGreaterEqual(len(registry.candidates("input")), 3)
        self.assertGreaterEqual(len(registry.candidates("send_button")), 3)
        self.assertGreaterEqual(len(registry.candidates("model_button")), 3)

    def test_model_selection_strings_are_configuration_driven(self):
        source = inspect.getsource(GeminiClient.ensure_model_selected)
        source += inspect.getsource(GeminiClient._find_model_button)
        source += inspect.getsource(GeminiClient._visible_model_button_contains)
        self.assertNotIn("3.1 Pro", source)
        self.assertNotIn('"Flash"', source)
        self.assertIn("model_policy", source)

        config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        gemini = config["providers"]["gemini"]
        aliases = gemini["model_ui"]["aliases"]
        self.assertIn(gemini["preferred_model"], aliases)
        self.assertGreaterEqual(len(aliases[gemini["preferred_model"]]), 2)

    def test_live_selector_monitor_uses_page_timer_not_playwright_thread(self):
        source = inspect.getsource(selector_health)
        self.assertIn("setInterval", source)
        self.assertIn("add_init_script", source)
        self.assertNotIn("threading.Thread", source)

    def test_weekly_selector_contract_workflow_exists(self):
        workflow = ROOT / ".github" / "workflows" / "gemini-selector-health.yml"
        source = workflow.read_text(encoding="utf-8")
        self.assertIn("schedule:", source)
        self.assertIn("workflow_dispatch:", source)
        self.assertIn("selector_check", source)


if __name__ == "__main__":
    unittest.main()
