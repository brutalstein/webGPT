from __future__ import annotations

import time
from typing import Any

from playwright.sync_api import Error as PlaywrightError, sync_playwright

from ..browser.manager import PersistentBrowser
from ..config import AppConfig, ProviderSettings
from ..errors import ClipboardBridgeError, ProviderError
from ..models import ProviderResponse
from ..core.provider import Provider
from .chatgpt_companion import (
    ChatGPTCompanionSettings,
    ChatGPTWindowController,
    ClipboardExchange,
)
from .chatgpt_setup import ManualChatGPTSetup


class ChatGPTManualWebProvider(Provider):
    """ChatGPT için kalıcı, kullanıcı kontrollü arka plan companion provider.

    Chrome ayrı ve kalıcı bir profilde açık tutulur. Boşta iken pencere minimize
    edilir. Terminalden yazılan prompt panoya aktarılır; kullanıcı mesajı gönderip
    tamamlanan yanıtı panoya kopyalar. ChatGPT DOM'undan veri veya çıktı otomatik
    olarak kazınmaz.
    """

    name = "chatgpt"
    mode = "background_companion"

    def __init__(self, app_config: AppConfig, settings: ProviderSettings):
        self.app_config = app_config
        self.settings = settings
        self.companion_settings = ChatGPTCompanionSettings.from_provider(settings)
        self._playwright_manager = None
        self._playwright = None
        self.browser: PersistentBrowser | None = None
        self._started = False
        self._session_id: str | None = None
        self.manual_setup = ManualChatGPTSetup(app_config, settings)
        self.window = ChatGPTWindowController(self.manual_setup.profile_dir)
        self.clipboard = ClipboardExchange()

    def _ensure_runtime(self) -> None:
        if self._playwright is not None:
            return
        self._playwright_manager = sync_playwright()
        self._playwright = self._playwright_manager.start()
        self.browser = PersistentBrowser(self.app_config, self.settings, self._playwright)

    def setup(self) -> None:
        self.close()
        self.manual_setup.run()

    def start(self) -> None:
        if self._started and self.browser is not None and self.browser.page is not None:
            return

        profile = self.manual_setup.profile_dir
        cookie_candidates = (
            profile / "Default" / "Network" / "Cookies",
            profile / "Default" / "Cookies",
        )
        if not self.manual_setup.marker.exists() and not any(path.exists() for path in cookie_candidates):
            raise ProviderError(
                "ChatGPT hesabı henüz kurulmamış. Ana menüden Kurulum ve bakım > "
                "ChatGPT hesabı kurulumu seçeneğini çalıştır."
            )

        self._ensure_runtime()
        assert self.browser is not None
        page = self.browser.launch(
            headless=False,
            url=str(self.settings.get("start_url", "https://chatgpt.com/")),
        )
        self._started = True

        self.window.wait_for_window(self.companion_settings.window_wait_seconds)
        if self.companion_settings.background_idle:
            self.window.minimize()
        else:
            page.bring_to_front()

        print(f"[CHATGPT] Arka plan companion hazır. Beklenen hesap: {self.settings.expected_email}")
        print("[CHATGPT] Chrome boşta minimize edilir; kullanıcı etkileşiminde tekrar öne getirilir.")
        print("[CHATGPT] Web çıktısı otomatik kazınmaz; yanıt kopyalama kullanıcı kontrollüdür.")
        if bool(self.settings.get("inject_local_memory", self.app_config.inject_local_memory)):
            print("[CHATGPT] OS kalıcı bağlamı gönderilecek prompta otomatik eklenir.")

    @staticmethod
    def _is_chatgpt_url(url: object) -> bool:
        return isinstance(url, str) and (
            url.startswith("https://chatgpt.com/") or url.startswith("https://chat.openai.com/")
        )

    def _navigate(self, target: str) -> None:
        assert self.browser is not None
        page = self.browser.require_page()
        if page.url != target:
            try:
                page.goto(
                    target,
                    wait_until="domcontentloaded",
                    timeout=int(self.settings.get("page_timeout_seconds", 60)) * 1_000,
                )
            except PlaywrightError as exc:
                raise ProviderError(f"Kayıtlı ChatGPT konuşması açılamadı: {exc}") from exc
        if self.companion_settings.background_idle:
            self.window.minimize()

    def resume_session(self, session_id: str, state: dict[str, Any]) -> None:
        self.start()
        remote_url = state.get("remote_url")
        target = str(remote_url) if self._is_chatgpt_url(remote_url) else str(
            self.settings.get("start_url", "https://chatgpt.com/")
        )
        self._navigate(target)
        self._session_id = session_id

    def new_session(self, session_id: str) -> None:
        self.start()
        self._navigate(str(self.settings.get("start_url", "https://chatgpt.com/")))
        self._session_id = session_id

    def session_state(self) -> dict[str, Any]:
        remote_url = ""
        if self.browser is not None:
            try:
                page = self.browser.require_page()
                if self._is_chatgpt_url(page.url):
                    remote_url = page.url
            except ProviderError:
                pass
        return {
            "remote_url": remote_url,
            "remote_provider": self.name,
            "mode": self.mode,
            "model": self.settings.preferred_model,
            "background_idle": self.companion_settings.background_idle,
            "output_capture": "user_controlled_clipboard",
        }

    def send(self, prompt: str, session_id: str) -> ProviderResponse:
        self.start()
        assert self.browser is not None
        page = self.browser.require_page()

        previous_clipboard = self.clipboard.read()
        self.clipboard.write(prompt)
        prompt_clipboard_sequence = self.clipboard.sequence_number()
        response = ""

        try:
            focused = True
            if self.companion_settings.restore_for_interaction:
                focused = self.window.restore_and_focus()
            page.bring_to_front()
            if self.companion_settings.restore_for_interaction and not focused:
                print("[UYARI] Windows pencereyi otomatik öne getiremedi; görev çubuğundan ChatGPT'yi aç.")

            print("\n[CHATGPT COMPANION]")
            print("Prompt panoya kopyalandı ve ChatGPT penceresi öne getirildi.")
            print("1. Mesaj kutusuna Ctrl+V yap ve mesajı gönder.")
            print("2. Yanıt tamamlanınca yalnızca yanıt metnini seçip Ctrl+C yap.")

            for attempt in range(1, self.companion_settings.clipboard_retry_count + 1):
                command = input(
                    f"3. Yanıt panodayken terminale dönüp Enter'a bas "
                    f"[{attempt}/{self.companion_settings.clipboard_retry_count}] "
                    "(iptal: q): "
                ).strip().casefold()
                if command in {"q", "quit", "iptal", "cancel"}:
                    raise ClipboardBridgeError("ChatGPT companion alışverişi kullanıcı tarafından iptal edildi.")

                time.sleep(0.2)
                candidate = self.clipboard.read()
                current_sequence = self.clipboard.sequence_number()
                clipboard_changed = (
                    None
                    if prompt_clipboard_sequence is None or current_sequence is None
                    else current_sequence != prompt_clipboard_sequence
                )
                if self.clipboard.is_response_candidate(
                    candidate,
                    prompt=prompt,
                    previous=previous_clipboard,
                    clipboard_changed=clipboard_changed,
                ):
                    response = candidate.strip()
                    break
                print("[UYARI] Panoda yeni bir ChatGPT yanıtı bulunamadı; yanıtı tekrar seçip Ctrl+C yap.")

            if not response:
                raise ClipboardBridgeError(
                    "Panoda geçerli ChatGPT yanıtı bulunamadı. Yanıtı seçip Ctrl+C yaptıktan sonra tekrar dene."
                )

            state = self.session_state()
            return ProviderResponse(
                text=response,
                provider=self.name,
                conversation_id=session_id,
                metadata={
                    "mode": self.mode,
                    "account": self.settings.expected_email,
                    "remote_url": state.get("remote_url", ""),
                    "output_capture": "user_controlled_clipboard",
                    "background_idle": self.companion_settings.background_idle,
                },
            )
        finally:
            if response and self.companion_settings.restore_clipboard_after_capture:
                self.clipboard.write(previous_clipboard)
            if self.companion_settings.minimize_after_exchange:
                self.window.minimize()

    def status(self) -> dict[str, str]:
        remote_url = str(self.session_state().get("remote_url", "")) if self._started else ""
        return {
            "provider": self.name,
            "mode": self.mode,
            "expected_account": self.settings.expected_email,
            "preferred_model": self.settings.preferred_model,
            "remote_conversation": remote_url or "henüz oluşmadı",
            "started": "evet" if self._started else "hayır",
            "browser_state": "boşta minimize" if self.companion_settings.background_idle else "görünür",
            "output_capture": "kullanıcı kontrollü pano",
            "account_verification": "kullanıcı kontrollü",
            "browser_profile": str(self.manual_setup.profile_dir),
            "local_context": "açık" if bool(
                self.settings.get("inject_local_memory", self.app_config.inject_local_memory)
            ) else "kapalı",
        }

    def close(self) -> None:
        if self.browser is not None:
            self.browser.close()
        if self._playwright_manager is not None:
            try:
                self._playwright_manager.stop()
            except Exception:
                pass
        self.browser = None
        self._playwright = None
        self._playwright_manager = None
        self._started = False
        self._session_id = None
