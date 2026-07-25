from __future__ import annotations

from abc import ABC, abstractmethod

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

    @abstractmethod
    def send(self, prompt: str, session_id: str) -> ProviderResponse:
        """Bir mesaj gönderir veya güvenli kullanıcı köprüsünü başlatır."""

    @abstractmethod
    def status(self) -> dict[str, str]:
        """Provider çalışma durumunu döndürür."""

    @abstractmethod
    def close(self) -> None:
        """Açık kaynakları kapatır."""
