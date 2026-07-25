from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from os_agent.core.orchestrator import Orchestrator  # noqa: E402
from os_agent.core.session_store import SessionStore  # noqa: E402
from os_agent.core.storage import StateDatabase  # noqa: E402
from os_agent.models import ProviderResponse  # noqa: E402


class FakeSettings:
    raw = {"preferred_model": "3.1 Pro"}

    def get(self, key, default=None):
        return default


class FakeConfig:
    inject_local_memory = False
    memory_context_max_chars = 6000

    def provider(self, name):
        if name != "gemini":
            raise RuntimeError(name)
        return FakeSettings()

    def snapshot(self, provider_name):
        return {"provider": provider_name, "model": "3.1 Pro"}


class FakeMemory:
    def render_context(self, provider, max_chars):
        return ""

    def combined(self, provider):
        return {"project": "OS"}


class FakeProvider:
    def __init__(self, name="gemini"):
        self.name = name
        self.started = False
        self.resumed = []
        self.new_count = 0
        self.state = {}

    def start(self):
        self.started = True

    def resume_session(self, session_id, state):
        self.resumed.append((session_id, dict(state)))
        self.state = dict(state)

    def new_session(self, session_id):
        self.new_count += 1
        self.state = {"remote_url": f"https://example.test/{session_id}"}

    def session_state(self):
        return dict(self.state)

    def send(self, prompt, session_id):
        self.state = {"remote_url": f"https://example.test/{session_id}/active"}
        return ProviderResponse(
            text="cevap",
            provider=self.name,
            conversation_id=session_id,
            metadata={"model": "3.1 Pro"},
        )

    def close(self):
        self.started = False


class FakeRegistry:
    def __init__(self, provider):
        self.provider = provider

    def get(self, name):
        return self.provider


class OrchestratorSessionTests(unittest.TestCase):
    def make_runtime(self, temp: str):
        database = StateDatabase(Path(temp) / "os-state.db")
        sessions = SessionStore(database)
        provider = FakeProvider()
        orchestrator = Orchestrator(FakeConfig(), FakeRegistry(provider), sessions, FakeMemory(), "gemini")
        return sessions, provider, orchestrator

    def test_selected_session_is_resumed_and_state_is_bound(self):
        with tempfile.TemporaryDirectory() as temp:
            sessions, provider, orchestrator = self.make_runtime(temp)
            session_id = sessions.create("gemini")
            sessions.update_provider_state(session_id, {"remote_url": "https://example.test/old"})
            orchestrator.resume_session(session_id)
            self.assertEqual(orchestrator.session_id, session_id)
            self.assertEqual(provider.resumed[-1][1]["remote_url"], "https://example.test/old")

    def test_new_session_calls_provider_once(self):
        with tempfile.TemporaryDirectory() as temp:
            sessions, provider, orchestrator = self.make_runtime(temp)
            new_id = orchestrator.new_session()
            self.assertEqual(provider.new_count, 1)
            self.assertEqual(sessions.get(new_id)["provider_state"]["remote_url"], f"https://example.test/{new_id}")

    def test_send_persists_remote_state_messages_and_snapshots(self):
        with tempfile.TemporaryDirectory() as temp:
            sessions, provider, orchestrator = self.make_runtime(temp)
            orchestrator.new_session()
            orchestrator.send("selam")
            record = sessions.get(orchestrator.session_id)
            self.assertEqual(record["turns"][0]["text"], "selam")
            self.assertEqual(record["turns"][1]["text"], "cevap")
            self.assertTrue(record["provider_state"]["remote_url"].endswith("/active"))
            self.assertEqual(record["settings_snapshot"]["model"], "3.1 Pro")
            self.assertEqual(record["context_snapshot"]["project"], "OS")


if __name__ == "__main__":
    unittest.main()
