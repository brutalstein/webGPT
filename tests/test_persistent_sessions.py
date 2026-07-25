from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from os_agent.core.session_store import SessionStore  # noqa: E402
from os_agent.core.storage import StateDatabase  # noqa: E402


class PersistentSessionTests(unittest.TestCase):
    def make_store(self, temp: str) -> SessionStore:
        return SessionStore(StateDatabase(Path(temp) / "os-state.db"))

    def test_provider_state_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp:
            store = self.make_store(temp)
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
            store = self.make_store(temp)
            first = store.create("gemini")
            store.create("chatgpt")
            third = store.create("gemini")
            store.add_turn(first, "user", "eski")
            store.add_turn(third, "user", "yeni")
            self.assertEqual(store.latest_for_provider("gemini")["session_id"], third)

    def test_first_user_message_becomes_title_and_is_searchable(self):
        with tempfile.TemporaryDirectory() as temp:
            store = self.make_store(temp)
            session_id = store.create("gemini")
            store.add_turn(session_id, "user", "Kalıcı session başlığı testi")
            record = store.get(session_id)
            self.assertEqual(record["title"], "Kalıcı session başlığı testi")
            matches = store.list_recent(search="başlığı")
            self.assertEqual(matches[0]["session_id"], session_id)

    def test_settings_and_context_snapshots_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp:
            store = self.make_store(temp)
            session_id = store.create("gemini")
            store.update_snapshots(
                session_id,
                settings_snapshot={"model": "3.1 Pro"},
                context_snapshot={"project": "OS"},
            )
            record = store.get(session_id)
            self.assertEqual(record["settings_snapshot"]["model"], "3.1 Pro")
            self.assertEqual(record["context_snapshot"]["project"], "OS")


if __name__ == "__main__":
    unittest.main()
