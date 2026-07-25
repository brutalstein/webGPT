from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from os_agent.core.session_store import SessionStore  # noqa: E402


class PersistentSessionTests(unittest.TestCase):
    def test_provider_state_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp:
            store = SessionStore(Path(temp) / "sessions.json")
            session_id = store.create("gemini")
            state = {
                "remote_url": "https://gemini.google.com/app/example",
                "model": "3.1 Pro",
            }
            store.update_provider_state(session_id, state)

            record = store.get(session_id)
            self.assertIsNotNone(record)
            self.assertEqual(record["provider_state"], state)

    def test_latest_session_is_selected_per_provider(self):
        with tempfile.TemporaryDirectory() as temp:
            store = SessionStore(Path(temp) / "sessions.json")
            first = store.create("gemini")
            second = store.create("chatgpt")
            third = store.create("gemini")
            store.add_turn(first, "user", "eski")
            store.add_turn(third, "user", "yeni")

            latest_gemini = store.latest_for_provider("gemini")
            latest_chatgpt = store.latest_for_provider("chatgpt")
            self.assertEqual(latest_gemini["session_id"], third)
            self.assertEqual(latest_chatgpt["session_id"], second)

    def test_first_user_message_becomes_title(self):
        with tempfile.TemporaryDirectory() as temp:
            store = SessionStore(Path(temp) / "sessions.json")
            session_id = store.create("gemini")
            store.add_turn(session_id, "user", "Kalıcı session başlığı testi")
            record = store.get(session_id)
            self.assertEqual(record["title"], "Kalıcı session başlığı testi")


if __name__ == "__main__":
    unittest.main()
