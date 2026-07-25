from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from os_agent.config import load_config  # noqa: E402
from os_agent.providers.chatgpt_api import OpenAIResponsesProvider  # noqa: E402
from os_agent.providers.openai_api.http_client import OpenAIHttpClient  # noqa: E402
from os_agent.providers.openai_api.secrets import ApiSecretStore  # noqa: E402


class _FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self.payload = json.dumps(payload).encode("utf-8")
        self.status = status
        self.headers = Message()
        self.headers["x-request-id"] = "req_test"

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.payload


class OpenAIApiProviderTests(unittest.TestCase):
    def test_secret_store_prefers_environment_without_plaintext_file(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            path = Path(temp) / "secret.dpapi"
            store = ApiSecretStore(path)
            self.assertEqual(store.get(), "sk-test")
            self.assertFalse(path.exists())

    def test_extracts_text_from_raw_responses_payload(self):
        payload = {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": "Merhaba"},
                        {"type": "output_text", "text": "Ustam"},
                    ],
                }
            ]
        }
        self.assertEqual(OpenAIResponsesProvider._extract_text(payload), "Merhaba\nUstam")

    def test_http_client_sends_conversation_response_payload(self):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return _FakeResponse({"id": "resp_1", "output": []})

        client = OpenAIHttpClient("sk-test", max_retries=0)
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = client.create_response(
                conversation_id="conv_1",
                model="gpt-5.2",
                input_text="selam",
                instructions="Türkçe cevap ver",
                max_output_tokens=512,
                reasoning_effort="medium",
                store=True,
                metadata={"os_session_id": "abc"},
            )
        self.assertEqual(result.request_id, "req_test")
        self.assertTrue(captured["url"].endswith("/v1/responses"))
        self.assertEqual(captured["body"]["conversation"], "conv_1")
        self.assertEqual(captured["body"]["model"], "gpt-5.2")
        self.assertNotIn("sk-test", json.dumps(captured["body"]))

    def test_chatgpt_config_uses_responses_and_conversations_api(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"LOCALAPPDATA": temp}):
            config = load_config(ROOT / "config.json")
            settings = config.provider("chatgpt")
            self.assertEqual(settings.kind, "openai_responses_api")
            self.assertTrue(settings.get("store_remote_conversation"))
            self.assertEqual(settings.get("history_replay_turns"), 20)


if __name__ == "__main__":
    unittest.main()
