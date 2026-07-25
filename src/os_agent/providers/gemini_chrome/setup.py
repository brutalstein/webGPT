from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime

from ...errors import ProviderError
from .config import GeminiChromeSettings
from .processes import (
    find_chrome_executable,
    list_profile_process_ids,
    remove_stale_lock_files,
    terminate_profile_processes,
    wait_profile_processes_to_close,
)


class ManualGeminiSetup:
    """Google girişini otomasyon dışındaki normal Chrome sürecinde yaptırır."""

    GEMINI_URL = "https://gemini.google.com/app"

    def __init__(self, settings: GeminiChromeSettings):
        self.settings = settings

    def run(self) -> None:
        chrome = find_chrome_executable()
        if chrome is None:
            raise ProviderError("Google Chrome bulunamadı. Önce Google Chrome'u kur.")

        profile = self.settings.profile_dir
        profile.mkdir(parents=True, exist_ok=True)
        self.settings.log_dir.mkdir(parents=True, exist_ok=True)

        stopped = terminate_profile_processes(profile)
        if stopped:
            print(f"[KURULUM] Önceki OS Chrome süreçleri kapatıldı: {len(stopped)}")
        remove_stale_lock_files(profile)

        command = [
            str(chrome),
            f"--user-data-dir={profile}",
            "--profile-directory=Default",
            "--no-first-run",
            "--no-default-browser-check",
            "--new-window",
            self.GEMINI_URL,
        ]

        print("\n[GEMINI HESAP KURULUMU]")
        print("Normal Google Chrome açılıyor; bu aşamada Playwright veya CDP kullanılmaz.")
        print(f"Beklenen hesap: {self.settings.expected_email}")
        print(f"Profil: {profile}")
        print("1. Google hesabına giriş yap.")
        print("2. Gemini'nin normal mesaj kutusunun açıldığını doğrula.")
        print("3. Gemini ayarlarında kişisel talimatlarının mevcut olduğunu kontrol et.")
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
                answer = input("Chrome hâlâ açık. Yalnızca OS profilindeki Chrome süreçleri kapatılsın mı? [E/h]: ").strip().casefold()
                if answer in {"", "e", "evet", "y", "yes"}:
                    terminate_profile_processes(profile)
                else:
                    raise ProviderError(
                        "Profil açık kaldığı için kurulum tamamlanmadı. Chrome penceresini kapatıp tekrar çalıştır."
                    )

        remove_stale_lock_files(profile)
        payload = {
            "completed_at": datetime.now().isoformat(timespec="seconds"),
            "expected_email": self.settings.expected_email,
            "profile_dir": str(profile),
            "setup_method": "normal_chrome_without_automation",
        }
        self.settings.app_data_dir.mkdir(parents=True, exist_ok=True)
        self.settings.setup_marker.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print("[BAŞARILI] Normal Chrome hesap kurulumu kaydedildi.")
        print("Artık Chrome penceresini kapatıp os.bat üzerinden arka planda devam edebilirsin.")
