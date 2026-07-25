from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from playwright.sync_api import BrowserContext, Page, Playwright, Error as PlaywrightError

from ..config import AppConfig, ProviderSettings
from ..errors import ProviderError


class PersistentBrowser:
    """OS provider'ları için yalnızca Google Chrome kullanan profil yöneticisi."""

    def __init__(self, app_config: AppConfig, settings: ProviderSettings, playwright: Playwright):
        self.app_config = app_config
        self.settings = settings
        self.playwright = playwright
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.headless = False
        self.browser_name = "Google Chrome"

    @property
    def profile_dir(self) -> Path:
        return self.app_config.data_dir / "browser-profiles" / self.settings.name

    def launch(self, *, headless: bool, url: str | None = None) -> Page:
        self.close()
        self._terminate_stale_processes()
        self._remove_stale_locks()
        self.profile_dir.mkdir(parents=True, exist_ok=True)

        chrome = self._find_chrome()
        if chrome is None:
            raise ProviderError(
                "Google Chrome bulunamadı. Bu OS sürümü yalnızca kurulu Google Chrome ile çalışır."
            )

        options = dict(
            executable_path=str(chrome),
            user_data_dir=str(self.profile_dir),
            headless=headless,
            chromium_sandbox=True,
            locale=self.app_config.language,
            viewport={"width": 1440, "height": 900},
            accept_downloads=True,
            timeout=int(self.settings.get("page_timeout_seconds", 60)) * 1000,
            args=[
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-notifications",
            ],
        )

        mode = "arka plan" if headless else "görünür"
        print(f"  -> Google Chrome ({mode}) başlatılıyor...")
        try:
            self.context = self.playwright.chromium.launch_persistent_context(**options)
        except PlaywrightError as exc:
            raise ProviderError(f"Google Chrome başlatılamadı: {exc}") from exc

        timeout_ms = int(self.settings.get("page_timeout_seconds", 60)) * 1000
        self.context.set_default_timeout(timeout_ms)
        self.context.set_default_navigation_timeout(timeout_ms)
        pages = [page for page in self.context.pages if not page.is_closed()]
        self.page = pages[0] if pages else self.context.new_page()
        self.headless = headless

        if url:
            self.page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        return self.page

    def require_page(self) -> Page:
        if self.page is None or self.page.is_closed():
            raise ProviderError("Aktif Google Chrome sayfası bulunamadı.")
        return self.page

    def close(self) -> None:
        if self.context is not None:
            try:
                self.context.close()
            except Exception:
                pass
        self.context = None
        self.page = None

    def reset_profile(self) -> None:
        self.close()
        self._terminate_stale_processes()
        shutil.rmtree(self.profile_dir, ignore_errors=True)

    @staticmethod
    def _find_chrome() -> Path | None:
        candidates = [
            Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        ]
        return next((item for item in candidates if item.is_file()), None)

    def _terminate_stale_processes(self) -> None:
        if os.name != "nt":
            return
        needle = str(self.profile_dir).replace("'", "''")
        script = rf"""
$needle = '{needle}'
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {{
        $_.Name -eq 'chrome.exe' -and
        $_.CommandLine -like "*$needle*"
    }} |
    ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}
"""
        try:
            subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
                check=False,
            )
            time.sleep(0.4)
        except (OSError, subprocess.SubprocessError):
            pass

    def _remove_stale_locks(self) -> None:
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile"):
            path = self.profile_dir / name
            try:
                if path.is_file() or path.is_symlink():
                    path.unlink(missing_ok=True)
                elif path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
            except OSError:
                pass
