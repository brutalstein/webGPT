from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..models import ProviderResponse


class Provider(ABC):
    name: str
    mode: str

    @abstractmethod
    def setup(self) -> None:
        """Hesap ve tarayıcı kurulumunu görünür biçimde açar."""

    @abstractmethod
    def start(self) -> None:
        """Provider'ı mesaj kabul edecek duruma getirir."""

    def resume_session(self, session_id: str, state: dict[str, Any]) -> None:
        """Kalıcı provider durumunu yükler. Durumsuz provider'lar bunu yok sayabilir."""

    def new_session(self, session_id: str) -> None:
        """Provider tarafında temiz bir konuşma başlatır."""
        self.resume_session(session_id, {})

    def session_state(self) -> dict[str, Any]:
        """Yerel session kaydına yazılacak provider durumunu döndürür."""
        return {}

    @abstractmethod
    def send(self, prompt: str, session_id: str) -> ProviderResponse:
        """Bir mesaj gönderir veya güvenli kullanıcı köprüsünü başlatır."""

    @abstractmethod
    def status(self) -> dict[str, str]:
        """Provider çalışma durumunu döndürür."""

    @abstractmethod
    def close(self) -> None:
        """Açık kaynakları kapatır."""
