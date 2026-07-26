from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from os_agent.providers.gemini_chrome.selector_health import critical_failures  # noqa: E402
from os_agent.providers.gemini_chrome.selectors import (  # noqa: E402
    ModelSelectionPolicy,
    SelectorHealthPolicy,
    SelectorRegistry,
)


class GeminiSelectorRegistryTests(unittest.TestCase):
    def test_configured_fallbacks_precede_defaults_and_are_deduplicated(self):
        registry = SelectorRegistry.from_config(
            {
                "chains": {
                    "input": [
                        '[data-os="prompt"]',
                        'rich-textarea div[contenteditable="true"]',
                        '[data-os="prompt"]',
                    ]
                }
            }
        )
        input_chain = registry.chain("input")
        self.assertEqual(input_chain.candidates[0], '[data-os="prompt"]')
        self.assertEqual(input_chain.candidates.count('[data-os="prompt"]'), 1)
        self.assertGreaterEqual(len(input_chain.candidates), 3)

    def test_per_chain_replace_defaults_is_supported(self):
        registry = SelectorRegistry.from_config(
            {
                "chains": {
                    "response": {
                        "replace_defaults": True,
                        "candidates": ["article[data-answer]"],
                    }
                }
            }
        )
        self.assertEqual(registry.candidates("response"), ("article[data-answer]",))

    def test_unknown_chain_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Bilinmeyen selector"):
            SelectorRegistry.from_config({"chains": {"unknown": ["div"]}})

    def test_playwright_only_selector_is_rejected_from_live_probe_contract(self):
        with self.assertRaisesRegex(ValueError, "querySelector"):
            SelectorRegistry.from_config(
                {
                    "chains": {
                        "model_button": {
                            "replace_defaults": True,
                            "candidates": ['button:has-text("Pro")'],
                        }
                    }
                }
            )

    def test_model_alias_matching_is_config_driven_and_boundary_aware(self):
        policy = ModelSelectionPolicy.from_config(
            {
                "default_names": ["default", "hesap varsayılanı"],
                "aliases": {"Model X": ["Gemini Model X", "X Pro"]},
            }
        )
        self.assertTrue(policy.is_default("Hesap Varsayılanı"))
        self.assertTrue(policy.matches("Gemini Model X", "Model X"))
        self.assertTrue(policy.matches("Aktif: X Pro", "Model X"))
        self.assertFalse(policy.matches("Model XL", "Model X"))

    def test_selector_health_intervals_are_bounded(self):
        policy = SelectorHealthPolicy.from_config(
            {
                "interval_seconds": 1,
                "read_interval_seconds": 1,
                "failure_threshold": 0,
                "report_interval_seconds": 1,
            }
        )
        self.assertEqual(policy.interval_seconds, 30)
        self.assertEqual(policy.read_interval_seconds, 5)
        self.assertEqual(policy.failure_threshold, 1)
        self.assertEqual(policy.report_interval_seconds, 60)

    def test_only_missing_critical_groups_fail_health(self):
        snapshot = {
            "groups": {
                "input": {"critical": True, "present": False},
                "response": {"critical": False, "present": False},
                "model_button": {"critical": False, "present": True},
            }
        }
        self.assertEqual(critical_failures(snapshot), ("input",))
        snapshot["resolutions"] = {"input": {"strategy": "accessible-role"}}
        self.assertEqual(critical_failures(snapshot), ())

    def test_unknown_model_role_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "desteklenmeyen roller"):
            ModelSelectionPolicy.from_config({"option_roles": ["made-up-role"]})


if __name__ == "__main__":
    unittest.main()
