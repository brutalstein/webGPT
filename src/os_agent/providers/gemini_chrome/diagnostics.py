from __future__ import annotations

import json
import os
import shutil
import socket
from datetime import datetime
from pathlib import Path

from ...errors import ProviderError
from .config import GeminiChromeSettings
from .processes import (
    chrome_version,
    find_chrome_executable,
    list_profile_process_ids,
    query_antivirus_products,
    query_chrome_policies,
    remove_stale_lock_files,
    safe_delete_directories,
    terminate_profile_processes,
)


class GeminiDoctor:
    def __init__(self, settings: GeminiChromeSettings):
        self.settings = settings

    def run(self) -> Path:
        chrome = find_chrome_executable()
        profile = self.settings.profile_dir
        findings: list[tuple[str, str, str]] = []

        if chrome is None:
            findings.append(("HATA", "Chrome", "Google Chrome bulunamadı."))
            version = "Yok"
        else:
            version = chrome_version(chrome)
            findings.append(("OK", "Chrome", f"{chrome} | sürüm {version}"))

        custom_profile = "Google/Chrome/User Data" not in str(profile).replace("\\", "/")
        findings.append(
            (
                "OK" if custom_profile else "HATA",
                "Profil dizini",
                f"{profile} | {'özel otomasyon profili' if custom_profile else 'varsayılan Chrome profili kullanılmamalı'}",
            )
        )

        try:
            profile.mkdir(parents=True, exist_ok=True)
            probe = profile / ".os_write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            findings.append(("OK", "Profil yazma", "Dizin yazılabilir."))
        except OSError as exc:
            findings.append(("HATA", "Profil yazma", str(exc)))

        pids = list_profile_process_ids(profile)
        findings.append(
            (
                "UYARI" if pids else "OK",
                "Profil süreç kilidi",
                f"Açık Chrome PID'leri: {pids}" if pids else "Bu profile ait açık Chrome süreci yok.",
            )
        )

        lock_names = [name for name in ("SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile") if (profile / name).exists()]
        findings.append(
            (
                "UYARI" if lock_names and not pids else "OK",
                "Kilit dosyaları",
                ", ".join(lock_names) if lock_names else "Kilit dosyası yok.",
            )
        )

        marker_ok = self.settings.setup_marker.exists()
        findings.append(
            (
                "OK" if marker_ok else "UYARI",
                "Normal Chrome hesap kurulumu",
                "Kurulum işareti mevcut." if marker_ok else "OS içindeki Google hesabı kurulumu henüz tamamlanmamış olabilir.",
            )
        )

        local_state = profile / "Local State"
        cookie_candidates = [profile / "Default" / "Network" / "Cookies", profile / "Default" / "Cookies"]
        cookie_file = next((path for path in cookie_candidates if path.exists()), None)
        findings.append(("OK" if local_state.exists() else "UYARI", "Chrome Local State", str(local_state)))
        findings.append(("OK" if cookie_file else "UYARI", "Oturum çerez veritabanı", str(cookie_file or cookie_candidates[0])))

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1", 0))
                port = int(sock.getsockname()[1])
            findings.append(("OK", "IPv4 loopback", f"127.0.0.1 üzerinde geçici port ayrılabildi: {port}"))
        except OSError as exc:
            findings.append(("HATA", "IPv4 loopback", str(exc)))

        policies = query_chrome_policies()
        findings.append(
            (
                "UYARI" if policies else "OK",
                "Chrome kurum politikaları",
                " | ".join(policies) if policies else "HKCU/HKLM altında Chrome politikası bulunmadı.",
            )
        )

        antivirus = query_antivirus_products()
        findings.append(
            (
                "BİLGİ",
                "Antivirüs/Web koruma",
                ", ".join(antivirus) if antivirus else "Windows Security Center ürün bilgisi alınamadı.",
            )
        )

        proxy_vars = {key: value for key, value in os.environ.items() if key.upper() in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"} and value}
        findings.append(
            (
                "UYARI" if proxy_vars else "OK",
                "Proxy ortamı",
                json.dumps(proxy_vars, ensure_ascii=False) if proxy_vars else "Proxy ortam değişkeni yok.",
            )
        )

        try:
            usage = shutil.disk_usage(profile)
            free_gb = usage.free / (1024 ** 3)
            findings.append(("OK" if free_gb >= 1 else "UYARI", "Boş disk", f"{free_gb:.2f} GB"))
        except OSError as exc:
            findings.append(("UYARI", "Boş disk", str(exc)))

        findings.extend(
            [
                ("OK", "Sandbox", "Projede --no-sandbox kullanılmaz; persistent fallback sandbox'ı açık başlatır."),
                ("OK", "Giriş yöntemi", "Google girişi yalnızca OS Kurulum ve bakım menüsündeki otomasyonsuz kurulum aşamasında yapılır."),
                ("OK", "CDP güvenliği", "127.0.0.1 ve standart dışı user-data-dir kullanılır."),
                ("BİLGİ", "Kişisel talimatlar", "Script Gemini ayarlarını değiştirmez; doğru hesap oturumundaki talimatlar kullanılır."),
            ]
        )

        lines = [
            "OS GEMINI DOCTOR RAPORU",
            f"Tarih: {datetime.now().isoformat(timespec='seconds')}",
            f"Beklenen hesap: {self.settings.expected_email}",
            f"Tercih edilen model: {self.settings.preferred_model}",
            f"Chrome sürümü: {version}",
            "",
        ]
        for level, title, detail in findings:
            lines.append(f"[{level}] {title}: {detail}")

        lines.extend(
            [
                "",
                "ÖNERİLEN SIRA:",
                "1. os.bat → Kurulum ve bakım → Google hesabı ve Gemini kurulumu",
                "2. Gerekirse görünür doğrulama: os.bat --visible",
                "3. Kurulum ve bakım → Oturumu silmeden yumuşak onarım",
                "4. Son çare: profil yedekli sıfırlama işlemini sonraki bakım sürümünde uygula",
                "5. Kurumsal politika/antivirüs varsa ilgili web korumasını ve TLS denetimini kontrol et.",
            ]
        )

        self.settings.log_dir.mkdir(parents=True, exist_ok=True)
        report = self.settings.log_dir / f"doctor_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        report.write_text("\n".join(lines), encoding="utf-8")
        print("\n".join(lines))
        print(f"\n[RAPOR] {report}")
        return report

    def soft_repair(self) -> None:
        profile = self.settings.profile_dir
        stopped = terminate_profile_processes(profile)
        removed_locks = remove_stale_lock_files(profile)
        cache_paths = [
            profile / "Default" / "Cache",
            profile / "Default" / "Code Cache",
            profile / "Default" / "GPUCache",
            profile / "GrShaderCache",
            profile / "ShaderCache",
            profile / "Default" / "Service Worker" / "CacheStorage",
        ]
        deleted = safe_delete_directories(cache_paths)
        print(f"[ONARIM] Kapatılan OS Chrome süreci: {len(stopped)}")
        print(f"[ONARIM] Temizlenen kilit: {len(removed_locks)}")
        print(f"[ONARIM] Temizlenen önbellek dizini: {len(deleted)}")
        print("Çerezler, Google oturumu ve Gemini ayarları korunmuştur.")

    def backup_and_reset(self) -> Path | None:
        profile = self.settings.profile_dir
        terminate_profile_processes(profile)
        remove_stale_lock_files(profile)
        if not profile.exists():
            profile.mkdir(parents=True, exist_ok=True)
            print(f"[PROFİL] Zaten boştu: {profile}")
            return None

        self.settings.backup_dir.mkdir(parents=True, exist_ok=True)
        backup = self.settings.backup_dir / f"gemini-profile-{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        try:
            profile.rename(backup)
        except OSError as exc:
            raise ProviderError(f"Profil yedeklenip sıfırlanamadı: {exc}") from exc
        profile.mkdir(parents=True, exist_ok=True)
        try:
            self.settings.setup_marker.unlink(missing_ok=True)
        except OSError:
            pass
        print(f"[PROFİL] Eski profil yedeklendi: {backup}")
        print(f"[PROFİL] Yeni boş profil oluşturuldu: {profile}")
        return backup
