from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

from ..config import AppConfig, ProviderSettings
from ..errors import ProviderError
from .gemini_chrome.processes import (
    find_chrome_executable,
    list_profile_process_ids,
    remove_stale_lock_files,
    terminate_profile_processes,
    wait_profile_processes_to_close,
)


class ManualChatGPTSetup:
    """ChatGPT girişini otomasyon dışındaki normal Chrome sürecinde yaptırır."""

    def __init__(self, app_config: AppConfig, settings: ProviderSettings):
        self.app_config = app_config
        self.settings = settings

    @property
    def profile_dir(self) -> Path:
        return self.app_config.data_dir / "browser-profiles" / self.settings.name

    @property
    def marker(self) -> Path:
        return self.app_config.data_dir / "providers" / "chatgpt" / "manual_setup_complete.json"

    def run(self) -> None:
        chrome = find_chrome_executable()
        if chrome is None:
            raise ProviderError("Google Chrome bulunamadı. Önce Google Chrome'u kur.")

        profile = self.profile_dir
        profile.mkdir(parents=True, exist_ok=True)
        self.marker.parent.mkdir(parents=True, exist_ok=True)
        terminate_profile_processes(profile)
        remove_stale_lock_files(profile)

        command = [
            str(chrome),
            f"--user-data-dir={profile}",
            "--profile-directory=Default",
            "--no-first-run",
            "--no-default-browser-check",
            "--new-window",
            str(self.settings.get("start_url", "https://chatgpt.com/")),
        ]

        print("\n[CHATGPT HESAP KURULUMU]")
        print("Normal Google Chrome açılıyor; bu aşamada Playwright veya CDP kullanılmaz.")
        print(f"Beklenen hesap: {self.settings.expected_email}")
        print(f"Profil: {profile}")
        print("1. ChatGPT hesabına giriş yap.")
        print("2. Ayarlar > Kişiselleştirme altında Özel Talimatlar ve Bellek ayarlarını doğrula.")
        print("3. Kullanmak istediğin modeli seç.")
        print("4. Bu özel Chrome penceresini tamamen kapat.")

        try:
            subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
            )
        except OSError as exc:
            raise ProviderError(f"Normal Google Chrome başlatılamadı: {exc}") from exc

        input("\nGiriş ve kontroller tamamlanınca Chrome penceresini kapatıp Enter'a bas: ")
        if list_profile_process_ids(profile):
            print("[BEKLE] Bu profile ait Chrome süreçlerinin kapanması bekleniyor...")
            if not wait_profile_processes_to_close(profile, timeout_seconds=45):
                answer = input("Chrome hâlâ açık. Yalnızca OS ChatGPT profili kapatılsın mı? [E/h]: ").strip().casefold()
                if answer in {"", "e", "evet", "y", "yes"}:
                    terminate_profile_processes(profile)
                else:
                    raise ProviderError("ChatGPT profil penceresini kapatıp kurulumu tekrar çalıştır.")

        remove_stale_lock_files(profile)
        self.marker.write_text(
            json.dumps(
                {
                    "completed_at": datetime.now().isoformat(timespec="seconds"),
                    "expected_email": self.settings.expected_email,
                    "profile_dir": str(profile),
                    "setup_method": "normal_chrome_without_automation",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print("[BAŞARILI] ChatGPT hesap kurulumu kaydedildi.")
        print("Artık os.bat içinden ChatGPT konuşması açabilirsin.")
