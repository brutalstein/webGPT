from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ...config import AppConfig, ProviderSettings


@dataclass(frozen=True, slots=True)
class GeminiChromeSettings:
    expected_email: str
    preferred_model: str
    launch_mode: str
    fallback_to_persistent: bool
    reuse_previous_profile: bool
    start_new_chat: bool
    strict_model_check: bool
    headless: bool
    page_timeout_seconds: int
    response_timeout_seconds: int
    stable_seconds: float
    cdp_start_retries: int
    cdp_port_timeout_seconds: int
    language: str
    app_data_dir: Path
    os_profile_dir: Path
    previous_profile_dir: Path

    @classmethod
    def from_settings(
        cls,
        app_config: AppConfig,
        settings: ProviderSettings,
    ) -> "GeminiChromeSettings":
        provider_dir = app_config.data_dir / "providers" / "gemini"
        return cls(
            expected_email=settings.expected_email,
            preferred_model=settings.preferred_model,
            launch_mode=str(settings.get("launch_mode", "cdp")).strip().casefold(),
            fallback_to_persistent=bool(settings.get("fallback_to_persistent", True)),
            reuse_previous_profile=bool(settings.get("reuse_previous_working_profile", True)),
            start_new_chat=bool(settings.get("start_new_chat_on_launch", True)),
            strict_model_check=bool(settings.get("strict_model_check", False)),
            headless=bool(settings.get("headless_after_setup", False)),
            page_timeout_seconds=max(20, int(settings.get("page_timeout_seconds", 60))),
            response_timeout_seconds=max(30, int(settings.get("response_timeout_seconds", 360))),
            stable_seconds=max(2.0, float(settings.get("stable_seconds", 5.0))),
            cdp_start_retries=max(1, int(settings.get("cdp_start_retries", 3))),
            cdp_port_timeout_seconds=max(5, int(settings.get("cdp_port_timeout_seconds", 20))),
            language=app_config.language,
            app_data_dir=provider_dir,
            os_profile_dir=app_config.data_dir / "browser-profiles" / "gemini-chrome",
            previous_profile_dir=Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
            / "GeminiTerminalAgent"
            / "chrome-profile",
        )

    @property
    def profile_dir(self) -> Path:
        if self.reuse_previous_profile and self.previous_profile_dir.exists():
            return self.previous_profile_dir
        return self.os_profile_dir

    @property
    def log_dir(self) -> Path:
        return self.app_data_dir / "logs"

    @property
    def setup_marker(self) -> Path:
        return self.app_data_dir / "manual_setup_complete.json"

    @property
    def backup_dir(self) -> Path:
        return self.app_data_dir / "profile-backups"
