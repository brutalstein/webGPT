from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from os_agent.core.memory_store import MemoryStore  # noqa: E402
from os_agent.core.session_store import SessionStore  # noqa: E402
from os_agent.core.storage import StateDatabase  # noqa: E402


class StorageMigrationTests(unittest.TestCase):
    def test_legacy_json_is_imported_once(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sessions_json = root / "sessions.json"
            memory_json = root / "memory.json"
            sessions_json.write_text(
                json.dumps(
                    {
                        "sessions": {
                            "abc123": {
                                "session_id": "abc123",
                                "provider": "gemini",
                                "title": "Eski sohbet",
                                "created_at": "2026-01-01T00:00:00+00:00",
                                "updated_at": "2026-01-01T00:01:00+00:00",
                                "turns": [{"role": "user", "text": "merhaba"}],
                                "provider_state": {"remote_url": "https://gemini.google.com/app/abc"},
                            }
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            memory_json.write_text(
                json.dumps({"global": {"project": {"value": "OS"}}, "providers": {}}),
                encoding="utf-8",
            )

            database = StateDatabase(root / "os-state.db")
            self.assertEqual(database.import_legacy_sessions(sessions_json), 1)
            self.assertEqual(database.import_legacy_memory(memory_json), 1)
            self.assertEqual(database.import_legacy_sessions(sessions_json), 0)

            sessions = SessionStore(database)
            memory = MemoryStore(database)
            self.assertEqual(sessions.get("abc123")["turns"][0]["text"], "merhaba")
            self.assertEqual(memory.combined("gemini")["project"], "OS")

    def test_database_backup_is_valid(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = StateDatabase(root / "os-state.db")
            sessions = SessionStore(database)
            sessions.create("gemini")
            backup = database.backup_now(root / "backups", keep=2)
            self.assertTrue(backup.is_file())
            self.assertEqual(StateDatabase(backup).quick_check(), "ok")

    def test_concurrent_turn_writes_are_serialized(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = StateDatabase(root / "os-state.db")
            sessions = SessionStore(database)
            session_id = sessions.create("gemini")

            def writer(prefix: str) -> None:
                for index in range(20):
                    sessions.add_turn(session_id, "user", f"{prefix}-{index}")

            threads = [threading.Thread(target=writer, args=(f"t{index}",)) for index in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            record = sessions.get(session_id)
            self.assertEqual(len(record["turns"]), 80)
            self.assertEqual(database.quick_check(), "ok")


if __name__ == "__main__":
    unittest.main()
