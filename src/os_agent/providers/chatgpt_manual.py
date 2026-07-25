from __future__ import annotations

import time

import pyperclip
from playwright.sync_api import sync_playwright

from ..browser.manager import PersistentBrowser
from ..config import AppConfig, ProviderSettings
from ..errors import ClipboardBridgeError, ProviderError
from ..models import ProviderResponse
from ..core.provider import Provider


class ChatGPTManualWebProvider(Provider):
    """
    ChatGPT web hesabını ayrı kalıcı profilde açan kullanıcı kontrollü köprü.

    Bu provider ChatGPT DOM'undan cevap kazımaz. Prompt panoya kopyalanır;
    kullanıcı tarayıcıda gönderir ve tamamlanan cevabı kendisi panoya kopyalar.
    """

    name = "chatgpt"
    mode = "manual_web_bridge"

    def __init__(self, app_config: AppConfig, settings: ProviderSettings):
        self.app_config = app_config
        self.settings = settings
        self._playwright_manager = None
        self._playwright = None
        self.browser: PersistentBrowser | None = None
        self._started = False

    def _ensure_runtime(self) -> None:
        if self._playwright is not None:
            return
        self._playwright_manager = sync_playwright()
        self._playwright = self._playwright_manager.start()
        self.browser = PersistentBrowser(self.app_config, self.settings, self._playwright)

    def setup(self) -> None:
        self._ensure_runtime()
        assert self.browser is not None
        page = self.browser.launch(headless=False, url=str(self.settings.get("start_url", "https://chatgpt.com/")))
        page.bring_to_front()
        print("\n[CHATGPT HESAP KURULUMU]")
        print(f"Beklenen hesap: {self.settings.expected_email}")
        print("Açılan tarayıcıda bu hesabın etkin olduğunu elle doğrula.")
        print("Ayarlar > Kişiselleştirme bölümünde Özel Talimatlar ve Bellek seçeneklerini kontrol et.")
        print(f"Model seçimi: {self.settings.preferred_model}")
        input("Kurulum tamamlanınca bu terminalde Enter'a bas: ")
        self._started = True

    def start(self) -> None:
        if self._started and self.browser is not None and self.browser.page is not None:
            return
        self._ensure_runtime()
        assert self.browser is not None
        page = self.browser.launch(headless=False, url=str(self.settings.get("start_url", "https://chatgpt.com/")))
        page.bring_to_front()
        self._started = True
        print(f"[CHATGPT] Görünür web köprüsü açık. Beklenen hesap: {self.settings.expected_email}")
        print("[CHATGPT] Hesap, bellek ve model seçimi web arayüzünde kullanıcı tarafından yönetilir.")

    def send(self, prompt: str, session_id: str) -> ProviderResponse:
        self.start()
        assert self.browser is not None
        page = self.browser.require_page()
        page.bring_to_front()

        old_clipboard = self._safe_paste()
        try:
            pyperclip.copy(prompt)
        except pyperclip.PyperclipException as exc:
            raise ClipboardBridgeError(f"Prompt panoya kopyalanamadı: {exc}") from exc

        print("\n[CHATGPT MANUEL KÖPRÜ]")
        print("1. ChatGPT penceresinde mesaj kutusuna Ctrl+V yap ve mesajı gönder.")
        print("2. Yanıt tamamlanınca yalnızca yanıt metnini seçip Ctrl+C yap.")
        input("3. Yanıt panodayken terminale dönüp Enter'a bas: ")
        time.sleep(0.2)

        response = self._safe_paste().strip()
        if not response or response == prompt or response == old_clipboard:
            raise ClipboardBridgeError(
                "Panoda yeni bir ChatGPT yanıtı bulunamadı. Yanıtı seçip Ctrl+C yaptıktan sonra tekrar dene."
            )

        return ProviderResponse(
            text=response,
            provider=self.name,
            conversation_id=session_id,
            metadata={"mode": self.mode, "account": self.settings.expected_email},
        )

    def status(self) -> dict[str, str]:
        return {
            "provider": self.name,
            "mode": self.mode,
            "expected_account": self.settings.expected_email,
            "preferred_model": self.settings.preferred_model,
            "started": "evet" if self._started else "hayır",
            "account_verification": "kullanıcı kontrollü",
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

    @staticmethod
    def _safe_paste() -> str:
        try:
            value = pyperclip.paste()
            return value if isinstance(value, str) else ""
        except pyperclip.PyperclipException as exc:
            raise ProviderError(f"Windows panosu okunamadı: {exc}") from exc
