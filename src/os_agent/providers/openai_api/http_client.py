from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.message import Message
from typing import Any

from ...errors import ProviderError


@dataclass(frozen=True, slots=True)
class ApiResult:
    data: dict[str, Any]
    request_id: str
    status: int


class OpenAIHttpClient:
    """OpenAI REST API için retry, timeout ve hata eşleme katmanı."""

    RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: int = 180,
        max_retries: int = 4,
    ):
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = max(10, int(timeout_seconds))
        self.max_retries = max(0, int(max_retries))
        if not self.api_key:
            raise ProviderError("OpenAI API anahtarı bulunamadı.")

    def list_models(self) -> ApiResult:
        return self.request("GET", "/models")

    def create_conversation(self, items: list[dict[str, Any]] | None = None) -> ApiResult:
        payload: dict[str, Any] = {}
        if items:
            payload["items"] = items[:20]
        return self.request("POST", "/conversations", payload)

    def create_response(
        self,
        *,
        conversation_id: str,
        model: str,
        input_text: str,
        instructions: str | None,
        max_output_tokens: int,
        reasoning_effort: str | None,
        store: bool,
        metadata: dict[str, str] | None = None,
    ) -> ApiResult:
        payload: dict[str, Any] = {
            "model": model,
            "conversation": conversation_id,
            "input": input_text,
            "store": bool(store),
            "max_output_tokens": max(128, int(max_output_tokens)),
        }
        if instructions:
            payload["instructions"] = instructions
        if reasoning_effort:
            payload["reasoning"] = {"effort": reasoning_effort}
        if metadata:
            payload["metadata"] = metadata
        return self.request("POST", "/responses", payload)

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> ApiResult:
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "OS-Terminal/0.5",
        }
        url = self.base_url + "/" + path.lstrip("/")
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            request = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    raw = response.read()
                    data = self._decode_json(raw)
                    return ApiResult(
                        data=data,
                        request_id=self._request_id(response.headers),
                        status=int(response.status),
                    )
            except urllib.error.HTTPError as exc:
                last_error = exc
                raw = exc.read()
                data = self._decode_json(raw, allow_empty=True)
                if exc.code in self.RETRYABLE_STATUS and attempt < self.max_retries:
                    self._sleep_before_retry(attempt, exc.headers)
                    continue
                raise self._provider_error(exc.code, data, self._request_id(exc.headers)) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    self._sleep_before_retry(attempt, None)
                    continue
                raise ProviderError(f"OpenAI API bağlantısı kurulamadı: {exc}") from exc

        raise ProviderError(f"OpenAI API isteği tamamlanamadı: {last_error}")

    @staticmethod
    def _decode_json(raw: bytes, *, allow_empty: bool = False) -> dict[str, Any]:
        if not raw:
            if allow_empty:
                return {}
            raise ProviderError("OpenAI API boş yanıt döndürdü.")
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderError("OpenAI API geçersiz JSON yanıtı döndürdü.") from exc
        if not isinstance(parsed, dict):
            raise ProviderError("OpenAI API yanıtı beklenen nesne biçiminde değil.")
        return parsed

    @staticmethod
    def _request_id(headers: Message | None) -> str:
        if headers is None:
            return ""
        return str(headers.get("x-request-id") or headers.get("request-id") or "")

    @classmethod
    def _provider_error(cls, status: int, payload: dict[str, Any], request_id: str) -> ProviderError:
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        message = str(error.get("message", "")).strip() if isinstance(error, dict) else ""
        code = str(error.get("code", "")).strip() if isinstance(error, dict) else ""
        suffix = f" İstek kimliği: {request_id}" if request_id else ""

        if status == 401:
            return ProviderError("OpenAI API anahtarı geçersiz veya iptal edilmiş." + suffix)
        if status == 403:
            return ProviderError("OpenAI API projesinin bu işlem veya modele erişim izni yok." + suffix)
        if status == 404:
            return ProviderError("OpenAI API konuşması veya kaynağı bulunamadı." + suffix)
        if status == 429:
            return ProviderError("OpenAI API hız veya kullanım sınırına ulaştı." + suffix)
        detail = message or code or f"HTTP {status}"
        return ProviderError(f"OpenAI API hatası: {detail}.{suffix}".rstrip("."))

    @staticmethod
    def _sleep_before_retry(attempt: int, headers: Message | None) -> None:
        retry_after = 0.0
        if headers is not None:
            try:
                retry_after = float(headers.get("retry-after", "0") or 0)
            except ValueError:
                retry_after = 0.0
        exponential = min(12.0, 0.75 * (2**attempt))
        delay = max(retry_after, exponential + random.uniform(0.0, 0.25))
        time.sleep(delay)
