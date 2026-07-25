from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from os_agent.config import load_config  # noqa: E402
from os_agent.core.memory_store import MemoryStore  # noqa: E402
from os_agent.core.storage import StateDatabase  # noqa: E402


class ChatGPTModernCliTests(unittest.TestCase):
    def test_chatgpt_is_fully_automatic_api_provider_with_memory(self):
        config = load_config(ROOT / "config.json")
        chatgpt = config.provider("chatgpt")
        self.assertTrue(chatgpt.enabled)
        self.assertEqual(chatgpt.kind, "openai_responses_api")
        self.assertTrue(chatgpt.get("inject_local_memory", False))
        self.assertEqual(chatgpt.preferred_browser, "none")

    def test_memory_entries_keep_global_and_provider_scopes(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = StateDatabase(Path(tmp) / "state.db")
            memory = MemoryStore(database)
            memory.set("dil", "Türkçe")
            memory.set("hitap", "Ustam", provider="chatgpt")

            entries = memory.list_entries()
            self.assertEqual(len(entries), 2)
            self.assertEqual(memory.combined("chatgpt"), {"dil": "Türkçe", "hitap": "Ustam"})
            self.assertEqual(memory.combined("gemini"), {"dil": "Türkçe"})


if __name__ == "__main__":
    unittest.main()
