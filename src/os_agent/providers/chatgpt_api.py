from __future__ import annotations

import getpass
import os
from typing import Any

from ..config import AppConfig, ProviderSettings
from ..core.provider import Provider
from ..errors import ProviderError
from ..models import ProviderResponse
from .openai_api.http_client import OpenAIHttpClient
from .openai_api.secrets import ApiSecretStore


class OpenAIResponsesProvider(Provider):
    """Responses + Conversations API ile tamamen terminal tabanlı ChatGPT provider."""

    name = "chatgpt"
    mode = "openai_responses_api"

    def __init__(self, app_config: AppConfig, settings: ProviderSettings):
        self.app_config = app_config
        self.settings = settings
        credential_file = app_config.data_dir / "secrets" / "openai_api_key.dpapi"
        self.secret_store = ApiSecretStore(
            credential_file,
            env_name=str(settings.get("api_key_env", "OPENAI_API_KEY")),
        )
        self.client: OpenAIHttpClient | None = None
        self._started = False
        self._session_id: str | None = None
        self._conversation_id: str | None = None
        self._last_response_id: str | None = None
        self._request_id: str = ""
        self._resolved_model = ""
        self._last_usage: dict[str, Any] = {}
        self._local_turns: list[dict[str, Any]] = []

    def setup(self) -> None:
        print("\n[OPENAI API BAĞLANTISI]")
        print("ChatGPT web aboneliği ile API ayrı ürünlerdir. Bu bağlantı OPENAI_API_KEY kullanır.")
        print("Anahtar yalnızca Windows DPAPI ile şifrelenmiş kullanıcı kasasında tutulur.")
        existing = self.secret_store.get()
        prompt = "Yeni OpenAI API anahtarı"
        if existing:
            prompt += " (mevcut anahtarı korumak için boş bırak)"
        value = getpass.getpass(prompt + ": ").strip()
        if value:
            self.secret_store.set(value)
        elif not existing:
            raise ProviderError("OpenAI API anahtarı girilmedi.")

        self._started = False
        self.client = None
        self.start()
        print(f"[BAŞARILI] OpenAI API bağlantısı doğrulandı. Model: {self._resolved_model}")

    def start(self) -> None:
        if self._started and self.client is not None:
            return
        api_key = self.secret_store.get()
        if not api_key:
            raise ProviderError(
                "OpenAI API anahtarı kurulmamış. Ana menüden Kurulum ve bakım > "
                "OpenAI API bağlantısı seçeneğini çalıştır veya OPENAI_API_KEY tanımla."
            )
        self.client = OpenAIHttpClient(
            api_key,
            base_url=str(self.settings.get("api_base_url", "https://api.openai.com/v1")),
            timeout_seconds=int(self.settings.get("request_timeout_seconds", 180)),
            max_retries=int(self.settings.get("max_retries", 4)),
        )
        explicit_model = os.environ.get("OPENAI_MODEL", "").strip()
        preferred = explicit_model or self.settings.preferred_model
        if not preferred or preferred.casefold() == "hesap varsayılanı":
            raise ProviderError("ChatGPT API için config.json içinde geçerli bir model kimliği tanımlanmalı.")

        model_result = self.client.list_models()
        model_rows = model_result.data.get("data", [])
        available = {
            str(item.get("id", "")).strip()
            for item in model_rows
            if isinstance(item, dict) and item.get("id")
        }
        if preferred in available:
            self._resolved_model = preferred
        elif explicit_model:
            raise ProviderError(f"OPENAI_MODEL ile seçilen modele erişilemiyor: {explicit_model}")
        else:
            fallbacks = self.settings.get("model_fallbacks", [])
            candidates = [str(item).strip() for item in fallbacks] if isinstance(fallbacks, list) else []
            self._resolved_model = next((item for item in candidates if item in available), "")
            if not self._resolved_model:
                raise ProviderError(
                    f"Tercih edilen OpenAI modeline erişilemiyor: {preferred}. "
                    "config.json içindeki preferred_model değerini erişilebilir bir modelle değiştir."
                )
            print(
                f"[UYARI] {preferred} erişilebilir değil; bu oturumda {self._resolved_model} kullanılacak."
            )
        self._request_id = model_result.request_id
        self._started = True

    def resume_session(self, session_id: str, state: dict[str, Any]) -> None:
        self.start()
        self._session_id = session_id
        conversation_id = state.get("conversation_id")
        self._conversation_id = str(conversation_id) if isinstance(conversation_id, str) and conversation_id else None
        response_id = state.get("last_response_id")
        self._last_response_id = str(response_id) if isinstance(response_id, str) and response_id else None
        local_turns = state.get("_local_turns", [])
        self._local_turns = list(local_turns) if isinstance(local_turns, list) else []
        if not self._conversation_id:
            self._conversation_id = self._create_conversation(self._local_turns)

    def new_session(self, session_id: str) -> None:
        self.start()
        self._session_id = session_id
        self._local_turns = []
        self._conversation_id = self._create_conversation([])
        self._last_response_id = None
        self._last_usage = {}

    def _create_conversation(self, turns: list[dict[str, Any]]) -> str:
        assert self.client is not None
        items: list[dict[str, Any]] = []
        replay_limit = max(0, min(20, int(self.settings.get("history_replay_turns", 20))))
        for turn in turns[-replay_limit:]:
            if not isinstance(turn, dict):
                continue
            role = str(turn.get("role", "")).strip().casefold()
            text = str(turn.get("text", "")).strip()
            if role not in {"user", "assistant"} or not text:
                continue
            items.append({"type": "message", "role": role, "content": text})
        result = self.client.create_conversation(items)
        conversation_id = str(result.data.get("id", "")).strip()
        if not conversation_id:
            raise ProviderError("OpenAI API yeni konuşma kimliği döndürmedi.")
        self._request_id = result.request_id
        return conversation_id

    def session_state(self) -> dict[str, Any]:
        return {
            "conversation_id": self._conversation_id or "",
            "last_response_id": self._last_response_id or "",
            "remote_provider": self.name,
            "mode": self.mode,
            "model": self._resolved_model or self.settings.preferred_model,
            "request_id": self._request_id,
            "usage": dict(self._last_usage),
        }

    def send(self, prompt: str, session_id: str) -> ProviderResponse:
        self.start()
        if self._session_id != session_id:
            self.resume_session(session_id, {})
        if not self._conversation_id:
            self._conversation_id = self._create_conversation(self._local_turns)
        assert self.client is not None

        metadata = {"os_session_id": session_id, "provider": self.name}
        conversation_recreated = False
        try:
            result = self._create_response(prompt, metadata)
        except ProviderError as exc:
            if "konuşması veya kaynağı bulunamadı" not in str(exc).casefold():
                raise
            # Uzak conversation silinmiş veya erişilemez olmuşsa SQLite geçmişiyle yeniden kur.
            self._conversation_id = self._create_conversation(self._local_turns)
            conversation_recreated = True
            result = self._create_response(prompt, metadata)
        text = self._extract_text(result.data)
        response_id = str(result.data.get("id", "")).strip()
        if response_id:
            self._last_response_id = response_id
        self._request_id = result.request_id
        usage = result.data.get("usage", {})
        self._last_usage = dict(usage) if isinstance(usage, dict) else {}

        response = ProviderResponse(
            text=text,
            provider=self.name,
            conversation_id=self._conversation_id,
            metadata={
                "mode": self.mode,
                "model": self._resolved_model,
                "conversation_id": self._conversation_id,
                "response_id": self._last_response_id or "",
                "request_id": self._request_id,
                "usage": dict(self._last_usage),
                "conversation_recreated": conversation_recreated,
            },
        )

        self._local_turns.extend(
            [
                {"role": "user", "text": prompt},
                {"role": "assistant", "text": text},
            ]
        )
        return response

    def _create_response(self, prompt: str, metadata: dict[str, str]):
        assert self.client is not None
        assert self._conversation_id is not None
        return self.client.create_response(
            conversation_id=self._conversation_id,
            model=self._resolved_model,
            input_text=prompt,
            instructions=str(self.settings.get("developer_instructions", "")).strip() or None,
            max_output_tokens=int(self.settings.get("max_output_tokens", 4096)),
            reasoning_effort=self._reasoning_effort(),
            store=bool(self.settings.get("store_remote_conversation", True)),
            metadata=metadata,
        )

    def _reasoning_effort(self) -> str | None:
        value = str(self.settings.get("reasoning_effort", "")).strip().casefold()
        return value or None

    @staticmethod
    def _extract_text(payload: dict[str, Any]) -> str:
        convenience = payload.get("output_text")
        if isinstance(convenience, str) and convenience.strip():
            return convenience.strip()

        parts: list[str] = []
        refusals: list[str] = []
        output = payload.get("output", [])
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict):
                    continue
                content = item.get("content", [])
                if not isinstance(content, list):
                    continue
                for piece in content:
                    if not isinstance(piece, dict):
                        continue
                    piece_type = str(piece.get("type", ""))
                    text = piece.get("text")
                    if piece_type == "output_text" and isinstance(text, str) and text.strip():
                        parts.append(text.strip())
                    refusal = piece.get("refusal")
                    if isinstance(refusal, str) and refusal.strip():
                        refusals.append(refusal.strip())
        if parts:
            return "\n".join(parts)
        if refusals:
            return "\n".join(refusals)
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            raise ProviderError(f"OpenAI yanıt üretimi başarısız: {error['message']}")
        raise ProviderError("OpenAI API yanıtında gösterilebilir metin bulunamadı.")

    def status(self) -> dict[str, str]:
        return {
            "provider": self.name,
            "mode": self.mode,
            "model": self._resolved_model or self.settings.preferred_model,
            "credential": "hazır" if self.secret_store.configured() else "kurulmamış",
            "conversation": self._conversation_id or "henüz oluşmadı",
            "browser": "kullanılmıyor",
            "local_context": "açık" if bool(
                self.settings.get("inject_local_memory", self.app_config.inject_local_memory)
            ) else "kapalı",
        }

    def close(self) -> None:
        self.client = None
        self._started = False
        self._session_id = None
        self._conversation_id = None
        self._last_response_id = None
        self._request_id = ""
        self._last_usage = {}
        self._local_turns = []
