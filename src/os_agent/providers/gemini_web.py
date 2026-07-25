from __future__ import annotations

import os

from playwright.sync_api import Error as PlaywrightError, sync_playwright

from ..config import AppConfig, ProviderSettings
from ..core.provider import Provider
from ..errors import ProviderError
from ..models import ProviderResponse
from .gemini_chrome.browser import GeminiBrowserController
from .gemini_chrome.client import GeminiClient
from .gemini_chrome.config import GeminiChromeSettings
from .gemini_chrome.diagnostics import GeminiDoctor
from .gemini_chrome.setup import ManualGeminiSetup
from .gemini_chrome.utils import save_screenshot


class GeminiWebProvider(Provider):
    """Normal Chrome hesabını koruyan, doğrudan Chrome+CDP tabanlı Gemini provider."""

    name = "gemini"
    mode = "chrome_cdp_web"

    def __init__(self, app_config: AppConfig, settings: ProviderSettings):
        self.app_config = app_config
        self.settings = settings
        self.chrome_settings = GeminiChromeSettings.from_settings(app_config, settings)
        self._playwright_manager = None
        self._playwright = None
        self.browser: GeminiBrowserController | None = None
        self.client: GeminiClient | None = None
        self._started = False
        self._actual_mode = "kapalı"
        self._headed = True

    @property
    def doctor(self) -> GeminiDoctor:
        return GeminiDoctor(self.chrome_settings)

    def setup(self) -> None:
        # Google'ın otomasyon altındaki giriş sayfalarını engellemesini önlemek
        # için giriş sırasında Playwright/CDP kesinlikle başlatılmaz.
        ManualGeminiSetup(self.chrome_settings).run()

    def start(self) -> None:
        if self._started and self.client is not None:
            return

        profile = self.chrome_settings.profile_dir
        cookie_candidates = (
            profile / "Default" / "Network" / "Cookies",
            profile / "Default" / "Cookies",
        )
        if not self.chrome_settings.setup_marker.exists() and not any(path.exists() for path in cookie_candidates):
            raise ProviderError(
                "Gemini hesabı henüz normal Chrome ile kurulmamış. Önce setup_gemini.bat çalıştır; "
                "Google hesabına normal Chrome'da giriş yap; pencereyi tamamen kapat."
            )

        self._headed = os.environ.get("OS_GEMINI_HEADED", "1") != "0"
        forced_mode = os.environ.get("OS_GEMINI_MODE")

        self._playwright_manager = sync_playwright()
        self._playwright = self._playwright_manager.start()
        self.browser = GeminiBrowserController(self.chrome_settings, self._playwright)

        try:
            session = self.browser.launch(headed=self._headed, forced_mode=forced_mode)
            self._actual_mode = session.mode
            client = GeminiClient(self.chrome_settings, session.page)
            client.open()
            client.wait_until_ready(
                headed=self._headed,
                timeout_seconds=self.chrome_settings.page_timeout_seconds if not self._headed else 600,
            )

            if self.chrome_settings.start_new_chat:
                client.start_new_chat()

            if not client.ensure_model_selected():
                if self.chrome_settings.strict_model_check:
                    raise ProviderError(f"{self.chrome_settings.preferred_model} modeli seçilemedi.")
                print(
                    f"[UYARI] {self.chrome_settings.preferred_model} otomatik seçilemedi; "
                    "hesapta açık olan model kullanılacak."
                )

            self.client = client
            self._started = True
            print(
                f"[GEMINI] Hazır — hesap profili: {self.chrome_settings.expected_email}, "
                f"mod: {self._actual_mode}, model: {client.current_model_text()}"
            )
            print("[GEMINI] Script kişisel talimatları değiştirmez; hesaptaki mevcut talimatlar geçerlidir.")
        except Exception as exc:
            page = None
            if self.browser is not None:
                try:
                    page = self.browser.require_page()
                except Exception:
                    page = None
            screenshot = save_screenshot(page, self.chrome_settings.log_dir, "startup_error")
            detail = f" Ekran görüntüsü: {screenshot}" if screenshot else ""
            self.close()
            if isinstance(exc, ProviderError):
                raise ProviderError(str(exc) + detail) from exc
            if isinstance(exc, PlaywrightError):
                raise ProviderError(f"Chrome/Playwright hatası: {exc}.{detail}") from exc
            raise

    def send(self, prompt: str, session_id: str) -> ProviderResponse:
        self.start()
        assert self.client is not None
        try:
            text = self.client.send_prompt_and_get_response(prompt)
        except ProviderError:
            raise
        except PlaywrightError as exc:
            raise ProviderError(f"Gemini sekmesi yenilenmiş veya kapanmış olabilir: {exc}") from exc
        return ProviderResponse(
            text=text,
            provider=self.name,
            conversation_id=session_id,
            metadata={
                "mode": self._actual_mode,
                "account": self.chrome_settings.expected_email,
            },
        )

    def status(self) -> dict[str, str]:
        model = self.client.current_model_text() if self.client is not None else "Bilinmiyor"
        return {
            "provider": self.name,
            "mode": self._actual_mode,
            "expected_account": self.chrome_settings.expected_email,
            "preferred_model": self.chrome_settings.preferred_model,
            "current_model": model,
            "browser": "Google Chrome",
            "browser_profile": str(self.chrome_settings.profile_dir),
            "login_method": "normal Chrome / otomasyonsuz",
            "unsafe_no_sandbox": "hayır",
            "prompt_passthrough": "evet" if not self.app_config.inject_local_memory else "hayır",
            "started": "evet" if self._started else "hayır",
        }

    def close(self) -> None:
        if self.browser is not None:
            try:
                self.browser.close()
            except Exception:
                pass
        if self._playwright_manager is not None:
            try:
                self._playwright_manager.stop()
            except Exception:
                pass
        self.browser = None
        self.client = None
        self._playwright = None
        self._playwright_manager = None
        self._started = False
        self._actual_mode = "kapalı"
