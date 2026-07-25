from __future__ import annotations

from collections.abc import Callable

from ..config import AppConfig, ProviderSettings
from ..errors import ProviderUnavailableError
from .provider import Provider

ProviderFactory = Callable[[AppConfig, ProviderSettings], Provider]


class ProviderRegistry:
    def __init__(self, config: AppConfig):
        self.config = config
        self._factories: dict[str, ProviderFactory] = {}
        self._instances: dict[str, Provider] = {}

    def register(self, kind: str, factory: ProviderFactory) -> None:
        self._factories[kind] = factory

    def names(self) -> list[str]:
        return sorted(name for name, item in self.config.providers.items() if item.enabled)

    def get(self, name: str) -> Provider:
        settings = self.config.provider(name)
        if settings.name in self._instances:
            return self._instances[settings.name]
        factory = self._factories.get(settings.kind)
        if factory is None:
            raise ProviderUnavailableError(
                f"{settings.name} için provider fabrikası bulunamadı: {settings.kind}"
            )
        instance = factory(self.config, settings)
        self._instances[settings.name] = instance
        return instance

    def close_all(self) -> None:
        for provider in self._instances.values():
            try:
                provider.close()
            except Exception:
                pass
        self._instances.clear()
