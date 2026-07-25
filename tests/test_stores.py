from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from os_agent.core.memory_store import MemoryStore  # noqa: E402
from os_agent.core.session_store import SessionStore  # noqa: E402


class StoreTests(unittest.TestCase):
    def test_memory_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(Path(tmp) / "memory.json")
            store.set("proje", "OS")
            self.assertEqual(store.combined("gemini")["proje"], "OS")
            self.assertTrue(store.delete("proje"))

    def test_session_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp) / "sessions.json")
            session_id = store.create("gemini")
            store.add_turn(session_id, "user", "selam")
            recent = store.list_recent()
            self.assertEqual(recent[0]["session_id"], session_id)
            self.assertEqual(recent[0]["turns"][0]["text"], "selam")


if __name__ == "__main__":
    unittest.main()
