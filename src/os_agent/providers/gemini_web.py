from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError, sync_playwright

from ..config import AppConfig, ProviderSettings
from ..core.provider import Provider
from ..errors import ProviderError
from ..models import ProviderResponse
from ..tools import LocalToolRuntime
from ..tools.approval import TerminalApprovalHandler
from .gemini_chrome.browser import GeminiBrowserController
from .gemini_chrome.client import GeminiClient
from .gemini_chrome.config import GeminiChromeSettings
from .gemini_chrome.diagnostics import GeminiDoctor
from .gemini_chrome.setup import ManualGeminiSetup
from .gemini_chrome.utils import save_screenshot


class GeminiWebProvider(Provider):
    """Normal Chrome hesabını koruyan, Chrome+CDP ve yerel araç tabanlı Gemini provider."""

    name = "gemini"
    mode = "chrome_cdp_web"

    def __init__(
        self,
        app_config: AppConfig,
        settings: ProviderSettings,
        *,
        tool_runtime: LocalToolRuntime | None = None,
    ):
        self.app_config = app_config
        self.settings = settings
        self.chrome_settings = GeminiChromeSettings.from_settings(app_config, settings)
        self.tool_runtime = tool_runtime or LocalToolRuntime(app_config)
        if self.tool_runtime.executor.approval_handler is None:
            self.tool_runtime.set_approval_handler(TerminalApprovalHandler())
        self._event_handler: Callable[[str, dict[str, Any]], None] | None = None
        self._playwright_manager = None
        self._playwright = None
        self.browser: GeminiBrowserController | None = None
        self.client: GeminiClient | None = None
        self._started = False
        self._actual_mode = "kapalı"
        self._headed = True
        self._session_id: str | None = None

    @property
    def doctor(self) -> GeminiDoctor:
        return GeminiDoctor(self.chrome_settings)

    def set_event_handler(self, handler: Callable[[str, dict[str, Any]], None] | None) -> None:
        self._event_handler = handler
        if self.client is not None:
            self.client.set_event_handler(handler)

    def cancel(self) -> None:
        if self.client is not None:
            self.client.request_cancel()

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
                "Gemini hesabı henüz kurulmamış. os.bat içindeki Kurulum ve bakım menüsünden "
                "Google hesabı ve Gemini kurulumu seç; giriş tamamlanınca Chrome'u tamamen kapat."
            )

        default_headed = "0" if self.chrome_settings.headless else "1"
        self._headed = os.environ.get("OS_GEMINI_HEADED", default_headed) != "0"
        forced_mode = os.environ.get("OS_GEMINI_MODE")

        self._playwright_manager = sync_playwright()
        self._playwright = self._playwright_manager.start()
        self.browser = GeminiBrowserController(self.chrome_settings, self._playwright)

        try:
            session = self.browser.launch(headed=self._headed, forced_mode=forced_mode)
            self._actual_mode = session.mode
            client = GeminiClient(self.chrome_settings, session.page)
            client.set_event_handler(self._event_handler)
            client.open()
            # Headless modda sayfa ilk saniyede hazır olmayabilir. `headed=True`
            # yalnızca bekleme davranışını seçer; tarayıcı penceresi açmaz.
            client.wait_until_ready(
                headed=True,
                timeout_seconds=600 if self._headed else self.chrome_settings.page_timeout_seconds,
            )

            if not client.ensure_model_selected():
                if self.chrome_settings.strict_model_check:
                    raise ProviderError(f"{self.chrome_settings.preferred_model} modeli seçilemedi.")
                print(
                    f"[UYARI] {self.chrome_settings.preferred_model} otomatik seçilemedi; "
                    "hesapta açık olan model kullanılacak."
                )

            self.client = client
            self._started = True
            workspace = self.tool_runtime.workspace.describe().get("root")
            print(
                f"[GEMINI] Hazır — hesap profili: {self.chrome_settings.expected_email}, "
                f"mod: {self._actual_mode}, görünür: {'evet' if self._headed else 'hayır'}, "
                f"model: {client.current_model_text()}"
            )
            print("[GEMINI] Script kişisel talimatları değiştirmez; hesaptaki mevcut talimatlar geçerlidir.")
            if self.tool_runtime.provider_enabled(self.name):
                print(f"[GEMINI] Yerel araç katmanı açık. Çalışma alanı: {workspace or 'seçilmedi'}")
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

    @staticmethod
    def _is_gemini_url(url: object) -> bool:
        return isinstance(url, str) and url.startswith("https://gemini.google.com/app")

    def resume_session(self, session_id: str, state: dict[str, Any]) -> None:
        self.start()
        assert self.client is not None
        assert self.browser is not None
        page = self.browser.require_page()
        remote_url = state.get("remote_url")

        if self._is_gemini_url(remote_url):
            if page.url != remote_url:
                try:
                    page.goto(
                        str(remote_url),
                        wait_until="domcontentloaded",
                        timeout=self.chrome_settings.page_timeout_seconds * 1_000,
                    )
                except PlaywrightTimeoutError:
                    if not page.url.startswith("https://gemini.google.com/app"):
                        raise ProviderError("Kayıtlı Gemini konuşması açılamadı.")
                except PlaywrightError as exc:
                    raise ProviderError(f"Kayıtlı Gemini konuşması açılamadı: {exc}") from exc
            self.client.page = page
            self.client.wait_until_ready(
                headed=True,
                timeout_seconds=self.chrome_settings.page_timeout_seconds,
            )
        else:
            self.client.start_new_chat()

        self._session_id = session_id

    def new_session(self, session_id: str) -> None:
        self.start()
        assert self.client is not None
        self.client.start_new_chat()
        self._session_id = session_id

    def session_state(self) -> dict[str, Any]:
        remote_url = ""
        if self.browser is not None:
            try:
                page = self.browser.require_page()
                if self._is_gemini_url(page.url):
                    remote_url = page.url
            except ProviderError:
                pass
        return {
            "remote_url": remote_url,
            "remote_provider": self.name,
            "model": self.client.current_model_text() if self.client is not None else "Bilinmiyor",
            "browser_mode": self._actual_mode,
            "local_tools": self.tool_runtime.enabled,
            "workspace": self.tool_runtime.workspace.describe(),
        }

    def _send_raw(self, prompt: str, session_id: str) -> ProviderResponse:
        self.start()
        assert self.client is not None
        try:
            text = self.client.send_prompt_and_get_response(prompt)
        except ProviderError:
            raise
        except PlaywrightError as exc:
            raise ProviderError(f"Gemini sekmesi yenilenmiş veya kapanmış olabilir: {exc}") from exc
        state = self.session_state()
        return ProviderResponse(
            text=text,
            provider=self.name,
            conversation_id=session_id,
            metadata={
                "mode": self._actual_mode,
                "account": self.chrome_settings.expected_email,
                "remote_url": state.get("remote_url", ""),
                "cancelled": bool(self.client.last_cancelled),
            },
        )

    def send(self, prompt: str, session_id: str) -> ProviderResponse:
        return self.tool_runtime.run(self.name, self._send_raw, prompt, session_id)

    def status(self) -> dict[str, str]:
        model = self.client.current_model_text() if self.client is not None else "Bilinmiyor"
        remote_url = str(self.session_state().get("remote_url", "")) if self._started else ""
        workspace = self.tool_runtime.workspace.describe().get("root")
        return {
            "provider": self.name,
            "mode": self._actual_mode,
            "expected_account": self.chrome_settings.expected_email,
            "preferred_model": self.chrome_settings.preferred_model,
            "current_model": model,
            "browser": "Google Chrome",
            "browser_visibility": "görünür" if self._headed else "arka plan",
            "browser_profile": str(self.chrome_settings.profile_dir),
            "remote_conversation": remote_url or "henüz oluşmadı",
            "login_method": "normal Chrome / otomasyonsuz",
            "unsafe_no_sandbox": "hayır",
            "prompt_passthrough": "evet" if not self.app_config.inject_local_memory else "hayır",
            "local_tools": "açık" if self.tool_runtime.provider_enabled(self.name) else "kapalı",
            "workspace": str(workspace or "seçilmedi"),
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
        self._session_id = None
