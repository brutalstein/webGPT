from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ConfigurationError


@dataclass(frozen=True, slots=True)
class ProviderSettings:
    name: str
    kind: str
    enabled: bool
    expected_email: str
    preferred_browser: str
    preferred_model: str
    raw: dict[str, Any] = field(repr=False)

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)

    def snapshot(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "enabled": self.enabled,
            "expected_email": self.expected_email,
            "preferred_browser": self.preferred_browser,
            "preferred_model": self.preferred_model,
            "options": dict(self.raw),
        }


@dataclass(frozen=True, slots=True)
class AppConfig:
    app_name: str
    default_provider: str
    language: str
    inject_local_memory: bool
    memory_context_max_chars: int
    providers: dict[str, ProviderSettings]
    local_tools: dict[str, Any]
    storage: dict[str, Any]
    cli: dict[str, Any]

    @property
    def data_dir(self) -> Path:
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / self.app_name
        return Path.home() / f".{self.app_name.casefold()}"

    @property
    def state_dir(self) -> Path:
        return self.data_dir / "state"

    @property
    def database_path(self) -> Path:
        filename = str(self.storage.get("database_file", "os-state.db")).strip() or "os-state.db"
        return self.state_dir / Path(filename).name

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def sessions_path(self) -> Path:
        """Eski JSON session dosyasının göç yolu."""
        return self.state_dir / "sessions.json"

    @property
    def memory_path(self) -> Path:
        """Eski JSON context dosyasının göç yolu."""
        return self.state_dir / "memory.json"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    def provider(self, name: str) -> ProviderSettings:
        key = name.casefold().strip()
        try:
            settings = self.providers[key]
        except KeyError as exc:
            raise ConfigurationError(f"Bilinmeyen provider: {name}") from exc
        if not settings.enabled:
            raise ConfigurationError(f"Provider kapalı: {name}")
        return settings

    def snapshot(self, provider_name: str) -> dict[str, Any]:
        provider = self.provider(provider_name)
        return {
            "app_name": self.app_name,
            "language": self.language,
            "inject_local_memory": self.inject_local_memory,
            "memory_context_max_chars": self.memory_context_max_chars,
            "provider": provider.snapshot(),
            "storage": dict(self.storage),
            "local_tools": dict(self.local_tools),
        }


def _require_email(value: Any, field_name: str) -> str:
    email = str(value or "").strip()
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise ConfigurationError(f"Geçersiz {field_name}: {email!r}")
    return email


def load_config(path: Path) -> AppConfig:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigurationError(f"Ayar dosyası bulunamadı: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Ayar dosyası okunamadı: {exc}") from exc

    providers_raw = raw.get("providers")
    if not isinstance(providers_raw, dict) or not providers_raw:
        raise ConfigurationError("config.json içinde providers nesnesi bulunmalı.")

    providers: dict[str, ProviderSettings] = {}
    for name, item in providers_raw.items():
        if not isinstance(item, dict):
            raise ConfigurationError(f"Provider ayarı nesne olmalı: {name}")
        key = str(name).casefold().strip()
        providers[key] = ProviderSettings(
            name=key,
            kind=str(item.get("kind", "")).strip(),
            enabled=bool(item.get("enabled", True)),
            expected_email=_require_email(item.get("expected_email"), f"{name}.expected_email"),
            preferred_browser=str(item.get("preferred_browser", "chrome")).strip().casefold(),
            preferred_model=str(item.get("preferred_model", "Hesap varsayılanı")).strip(),
            raw=dict(item),
        )

    default_provider = str(raw.get("default_provider", "")).casefold().strip()
    if default_provider not in providers:
        raise ConfigurationError("default_provider, providers içinde tanımlı değil.")
    if not providers[default_provider].enabled:
        raise ConfigurationError("default_provider etkin olmalı.")

    app_name = str(raw.get("app_name", "OS")).strip() or "OS"
    config = AppConfig(
        app_name=app_name,
        default_provider=default_provider,
        language=str(raw.get("language", "tr-TR")),
        inject_local_memory=bool(raw.get("inject_local_memory", True)),
        memory_context_max_chars=max(500, int(raw.get("memory_context_max_chars", 6000))),
        providers=providers,
        local_tools=dict(raw.get("local_tools", {})),
        storage=dict(raw.get("storage", {})),
        cli=dict(raw.get("cli", {})),
    )
    config.state_dir.mkdir(parents=True, exist_ok=True)
    config.logs_dir.mkdir(parents=True, exist_ok=True)
    config.backups_dir.mkdir(parents=True, exist_ok=True)
    return config
