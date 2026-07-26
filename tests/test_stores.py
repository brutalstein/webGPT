from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from os_agent.core.memory_store import MemoryStore  # noqa: E402
from os_agent.core.session_store import SessionStore  # noqa: E402
from os_agent.core.storage import StateDatabase  # noqa: E402


class StoreTests(unittest.TestCase):
    def test_memory_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = StateDatabase(Path(tmp) / "os-state.db")
            store = MemoryStore(database)
            store.set("proje", "OS")
            store.set("model", "Gemini", provider="gemini")
            self.assertEqual(store.combined("gemini")["proje"], "OS")
            self.assertEqual(store.combined("gemini")["model"], "Gemini")
            self.assertTrue(store.delete("proje"))

    def test_session_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = StateDatabase(Path(tmp) / "os-state.db")
            store = SessionStore(database)
            session_id = store.create("gemini")
            store.add_turn(session_id, "user", "selam", metadata={"source": "terminal"})
            recent = store.list_recent(include_turns=True)
            self.assertEqual(recent[0]["session_id"], session_id)
            self.assertEqual(recent[0]["turns"][0]["text"], "selam")
            self.assertEqual(recent[0]["turns"][0]["metadata"]["source"], "terminal")
            self.assertEqual(database.quick_check(), "ok")

    def test_session_delete_cascades_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = StateDatabase(Path(tmp) / "os-state.db")
            store = SessionStore(database)
            session_id = store.create("gemini")
            store.add_turn(session_id, "user", "silinecek")
            self.assertTrue(store.delete(session_id))
            self.assertFalse(store.delete(session_id))
            self.assertIsNone(store.get(session_id))
            with database.read() as connection:
                count = connection.execute(
                    "SELECT COUNT(*) FROM messages WHERE session_id = ?", (session_id,)
                ).fetchone()[0]
            self.assertEqual(count, 0)
            self.assertEqual(database.quick_check(), "ok")


if __name__ == "__main__":
    unittest.main()
