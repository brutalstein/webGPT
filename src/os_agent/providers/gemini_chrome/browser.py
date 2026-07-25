from __future__ import annotations

import os
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Error as PlaywrightError,
    Page,
    Playwright,
)

from ...errors import ProviderError
from .config import GeminiChromeSettings
from .processes import (
    find_chrome_executable,
    remove_stale_lock_files,
    reserve_free_port,
    terminate_profile_processes,
)


@dataclass(slots=True)
class BrowserSession:
    mode: str
    browser: Browser | None
    context: BrowserContext
    page: Page
    process: subprocess.Popen[bytes] | None
    port: int | None


class GeminiBrowserController:
    """Önce doğrudan Chrome+CDP, gerekirse güvenli Playwright fallback kullanır."""

    GEMINI_URL = "https://gemini.google.com/app"

    def __init__(self, settings: GeminiChromeSettings, playwright: Playwright):
        self.settings = settings
        self.playwright = playwright
        self.session: BrowserSession | None = None
        self.chrome_path = find_chrome_executable()
        if self.chrome_path is None:
            raise ProviderError("Google Chrome bulunamadı. Bu provider yalnızca kurulu Google Chrome'u kullanır.")

    def launch(self, *, headed: bool, forced_mode: str | None = None) -> BrowserSession:
        self.close()
        mode = (forced_mode or os.environ.get("OS_GEMINI_MODE") or self.settings.launch_mode).strip().casefold()
        failures: list[str] = []

        if mode in {"cdp", "auto"}:
            try:
                self.session = self._launch_cdp(headed=headed)
                return self.session
            except ProviderError as exc:
                failures.append(f"CDP: {exc}")
                if not self.settings.fallback_to_persistent and mode != "auto":
                    raise

        if mode in {"persistent", "auto", "cdp"} and self.settings.fallback_to_persistent:
            try:
                self.session = self._launch_persistent(headed=headed)
                return self.session
            except ProviderError as exc:
                failures.append(f"Persistent: {exc}")

        detail = " | ".join(failures) or f"Bilinmeyen başlatma modu: {mode}"
        raise ProviderError(f"Gemini Chrome başlatılamadı. {detail}")

    def _prepare_profile(self) -> Path:
        profile = self.settings.profile_dir
        profile.mkdir(parents=True, exist_ok=True)
        self.settings.log_dir.mkdir(parents=True, exist_ok=True)
        terminate_profile_processes(profile)
        remove_stale_lock_files(profile)
        return profile

    def _launch_cdp(self, *, headed: bool) -> BrowserSession:
        profile = self._prepare_profile()
        last_error = ""

        for attempt in range(1, self.settings.cdp_start_retries + 1):
            port = reserve_free_port()
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_path = self.settings.log_dir / f"chrome_cdp_{stamp}_a{attempt}.log"
            command = [
                str(self.chrome_path),
                f"--user-data-dir={profile}",
                "--profile-directory=Default",
                f"--remote-debugging-port={port}",
                "--remote-debugging-address=127.0.0.1",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-mode",
                "--new-window",
            ]
            if not headed:
                command.extend(["--headless=new", "--window-size=1440,900"])
            command.append(self.GEMINI_URL)

            print(
                f"  -> Google Chrome {'görünür' if headed else 'arka plan'} CDP modunda başlatılıyor "
                f"(deneme {attempt}/{self.settings.cdp_start_retries})..."
            )
            print(f"  -> Profil: {profile}")
            print("  -> Güvensiz --no-sandbox anahtarı kullanılmıyor.")

            try:
                log_file = log_path.open("wb")
                process = subprocess.Popen(
                    command,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
                )
                log_file.close()
            except OSError as exc:
                last_error = f"Chrome süreci oluşturulamadı: {exc}"
                continue

            endpoint = f"http://127.0.0.1:{port}"
            if not self._wait_for_cdp(endpoint, process):
                last_error = self._describe_start_failure(process, log_path)
                self._stop_process(process)
                terminate_profile_processes(profile)
                remove_stale_lock_files(profile)
                continue

            try:
                browser = self.playwright.chromium.connect_over_cdp(
                    endpoint,
                    timeout=self.settings.page_timeout_seconds * 1_000,
                )
                if not browser.contexts:
                    raise ProviderError("CDP bağlantısında tarayıcı context'i bulunamadı.")
                context = browser.contexts[0]
                context.set_default_timeout(self.settings.page_timeout_seconds * 1_000)
                context.set_default_navigation_timeout(self.settings.page_timeout_seconds * 1_000)
                page = self._choose_single_page(context)
                return BrowserSession(
                    mode="cdp",
                    browser=browser,
                    context=context,
                    page=page,
                    process=process,
                    port=port,
                )
            except (PlaywrightError, ProviderError) as exc:
                last_error = f"CDP bağlantısı kurulamadı: {exc}"
                self._stop_process(process)
                terminate_profile_processes(profile)
                remove_stale_lock_files(profile)

        raise ProviderError(last_error or "Chrome CDP portu açılamadı.")

    def _launch_persistent(self, *, headed: bool) -> BrowserSession:
        profile = self._prepare_profile()
        print("  -> CDP başarısız; Playwright persistent fallback deneniyor...")
        print("  -> Chromium sandbox açık; --no-sandbox kullanılmayacak.")
        try:
            context = self.playwright.chromium.launch_persistent_context(
                executable_path=str(self.chrome_path),
                user_data_dir=str(profile),
                headless=not headed,
                chromium_sandbox=True,
                locale=self.settings.language,
                viewport={"width": 1440, "height": 900},
                timeout=self.settings.page_timeout_seconds * 1_000,
                args=[
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-background-mode",
                ],
            )
        except PlaywrightError as exc:
            raise ProviderError(f"Playwright fallback Chrome'u başlatamadı: {exc}") from exc

        if context.is_closed():
            raise ProviderError("Playwright fallback context'i açıldıktan hemen sonra kapandı.")
        context.set_default_timeout(self.settings.page_timeout_seconds * 1_000)
        context.set_default_navigation_timeout(self.settings.page_timeout_seconds * 1_000)
        page = self._choose_single_page(context)
        return BrowserSession(
            mode="persistent",
            browser=None,
            context=context,
            page=page,
            process=None,
            port=None,
        )

    def _choose_single_page(self, context: BrowserContext) -> Page:
        pages = [page for page in context.pages if not page.is_closed()]
        if not pages:
            return context.new_page()

        chosen = next((page for page in pages if "gemini.google.com" in page.url), pages[0])
        for page in pages:
            if page is chosen or page.is_closed():
                continue
            try:
                page.close()
            except PlaywrightError:
                pass
        return chosen

    def _wait_for_cdp(self, endpoint: str, process: subprocess.Popen[bytes]) -> bool:
        deadline = time.monotonic() + self.settings.cdp_port_timeout_seconds
        url = endpoint + "/json/version"
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return False
            try:
                with urllib.request.urlopen(url, timeout=1.0) as response:
                    if response.status == 200:
                        return True
            except (urllib.error.URLError, TimeoutError, OSError):
                time.sleep(0.25)
        return False

    @staticmethod
    def _describe_start_failure(process: subprocess.Popen[bytes], log_path: Path) -> str:
        code = process.poll()
        tail = ""
        try:
            if log_path.exists():
                data = log_path.read_text(encoding="utf-8", errors="replace")
                tail = data[-1500:].strip()
        except OSError:
            pass
        if code is None:
            return f"CDP portu zamanında açılmadı. Log: {log_path}"
        if tail:
            return f"Chrome erken kapandı (kod {code}). Son log: {tail}"
        return f"Chrome erken kapandı (kod {code}). Log: {log_path}"

    @staticmethod
    def _stop_process(process: subprocess.Popen[bytes] | None) -> None:
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=3)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    def require_page(self) -> Page:
        if self.session is None:
            raise ProviderError("Aktif Gemini Chrome oturumu yok.")
        if self.session.page.is_closed():
            pages = [page for page in self.session.context.pages if not page.is_closed()]
            if not pages:
                raise ProviderError("Gemini sekmesi kapatılmış.")
            self.session.page = next(
                (page for page in pages if "gemini.google.com" in page.url),
                pages[0],
            )
        return self.session.page

    def close(self) -> None:
        session = self.session
        self.session = None
        if session is None:
            return
        try:
            if session.mode == "persistent":
                session.context.close()
            elif session.browser is not None:
                session.browser.close()
        except Exception:
            pass
        self._stop_process(session.process)
        terminate_profile_processes(self.settings.profile_dir)
        remove_stale_lock_files(self.settings.profile_dir)
