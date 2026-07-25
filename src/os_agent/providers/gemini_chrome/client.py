from __future__ import annotations

import os
import re
import threading
import time
from typing import Optional

from playwright.sync_api import Error as PlaywrightError, Locator, Page, TimeoutError as PlaywrightTimeoutError

from ...errors import ProviderError
from .config import GeminiChromeSettings
from .selectors import (
    INPUT_SELECTORS,
    NEW_CHAT_NAMES,
    RESPONSE_SELECTORS,
    SEND_BUTTON_SELECTORS,
    STOP_BUTTON_SELECTORS,
)
from .utils import body_text, click_by_names, first_visible, locator_text, wait_for_visible


class GeminiClient:
    URL = "https://gemini.google.com/app"

    BLOCKED_SIGNIN_PATTERNS = (
        "oturumunuz açılamadı",
        "bu tarayıcı veya uygulama güvenli olmayabilir",
        "couldn't sign you in",
        "this browser or app may not be secure",
    )
    TEMPORARY_ERROR_PATTERNS = (
        "bir şeyler ters gitti",
        "something went wrong",
        "tekrar deneyin",
        "try again",
    )

    def __init__(self, settings: GeminiChromeSettings, page: Page):
        self.settings = settings
        self.page = page
        self.is_thinking = False
        self.animation_thread: Optional[threading.Thread] = None

    def open(self) -> None:
        print("  -> Gemini açılıyor...")
        try:
            if not self.page.url.startswith("https://gemini.google.com"):
                self.page.goto(
                    self.URL,
                    wait_until="domcontentloaded",
                    timeout=self.settings.page_timeout_seconds * 1_000,
                )
            else:
                self.page.wait_for_load_state("domcontentloaded", timeout=self.settings.page_timeout_seconds * 1_000)
        except PlaywrightTimeoutError:
            if not self.page.url.startswith("https://gemini.google.com"):
                raise ProviderError("Gemini sayfası zamanında açılamadı.")
        except PlaywrightError as exc:
            raise ProviderError(f"Gemini sayfası açılamadı: {exc}") from exc

    def wait_until_ready(self, *, headed: bool, timeout_seconds: int) -> Locator:
        deadline = time.monotonic() + timeout_seconds
        notice_printed = False

        while time.monotonic() < deadline:
            if self.page.is_closed():
                raise ProviderError("Gemini sekmesi kapatıldı.")

            input_box = wait_for_visible(self.page, INPUT_SELECTORS, timeout_ms=1_000)
            if input_box is not None:
                return input_box

            state = self.detect_page_state()
            if state == "blocked_signin":
                raise ProviderError(
                    "Google, otomasyon altında giriş sayfasını engelledi. Bu pencerede giriş yapma. "
                    "os.bat içindeki Kurulum ve bakım menüsünden Google hesabı ve Gemini kurulumu seç; "
                    "giriş tamamlanınca Chrome'u kapat."
                )
            if state == "login_required":
                raise ProviderError(
                    "Gemini oturum çerezi yok veya süresi dolmuş. Otomasyon sayfasında giriş yapma; "
                    "os.bat içindeki Kurulum ve bakım menüsünden hesabı yeniden aç."
                )
            if state == "temporary_error" and headed:
                self._click_retry_if_present()
            if state in {"consent_or_interstitial", "unknown"} and headed and not notice_printed:
                print("[GEMINI] Ara ekran/izin sayfası varsa aynı Chrome penceresinde tamamla; ajan bekliyor...")
                notice_printed = True
            if not headed and state != "ready":
                raise ProviderError(
                    "Gemini arka planda hazır değil. Kurulum ve bakım menüsünden hesap kurulumunu tamamla; "
                    "gerekirse os.bat --visible ile hata ayıklama yap."
                )
            time.sleep(1.0)

        raise ProviderError("Gemini mesaj kutusu bekleme süresi içinde bulunamadı.")

    def detect_page_state(self) -> str:
        url = self.page.url.casefold()
        text = body_text(self.page, timeout_ms=2_000).casefold()

        if any(pattern in text for pattern in self.BLOCKED_SIGNIN_PATTERNS):
            return "blocked_signin"
        if "accounts.google.com" in url or "/signin" in url:
            return "login_required"
        if any(pattern in text for pattern in self.TEMPORARY_ERROR_PATTERNS):
            return "temporary_error"
        if any(
            phrase in text
            for phrase in (
                "gemini uygulamalarını kullanmadan önce",
                "before you use gemini apps",
                "gemini'a hoş geldiniz",
                "welcome to gemini",
                "kabul ediyorum",
                "i agree",
                "başlayın",
                "get started",
            )
        ):
            return "consent_or_interstitial"
        if wait_for_visible(self.page, INPUT_SELECTORS, timeout_ms=250) is not None:
            return "ready"
        return "unknown"

    def _click_retry_if_present(self) -> None:
        for name in ("Tekrar dene", "Try again", "Yeniden dene"):
            try:
                button = first_visible(self.page.get_by_role("button", name=re.compile(name, re.IGNORECASE)))
                if button is not None:
                    button.click()
                    time.sleep(1.0)
                    return
            except PlaywrightError:
                pass

    def start_new_chat(self) -> bool:
        clicked = click_by_names(self.page, NEW_CHAT_NAMES, timeout_ms=5_000)
        if clicked:
            time.sleep(0.8)
        return clicked

    def ensure_model_selected(self) -> bool:
        target = self.settings.preferred_model.strip()
        if not target or target.casefold() in {"hesap varsayılanı", "default"}:
            return True
        if self._visible_model_button_contains(target):
            return True

        button = self._find_model_button()
        if button is None:
            return False
        try:
            button.click()
            time.sleep(0.5)
        except PlaywrightError:
            return False

        patterns = [target]
        if target.casefold() == "3.1 pro":
            patterns.extend(("Gemini 3.1 Pro", "3.1 Pro", "Pro"))

        for text in patterns:
            regex = re.compile(re.escape(text), re.IGNORECASE)
            for role in ("menuitem", "option", "button"):
                try:
                    candidate = first_visible(self.page.get_by_role(role, name=regex))
                    if candidate is not None:
                        candidate.click()
                        time.sleep(0.8)
                        return True
                except PlaywrightError:
                    pass
            try:
                candidate = first_visible(self.page.get_by_text(regex))
                if candidate is not None:
                    candidate.click()
                    time.sleep(0.8)
                    return True
            except PlaywrightError:
                pass

        try:
            self.page.keyboard.press("Escape")
        except PlaywrightError:
            pass
        return False

    def current_model_text(self) -> str:
        button = self._find_model_button()
        return locator_text(button).strip() if button is not None else "Bilinmiyor"

    def send_prompt_and_get_response(self, prompt: str) -> str:
        prompt = prompt.strip()
        if not prompt:
            return ""

        self._wait_until_previous_generation_finishes(timeout_s=30)
        if not self.ensure_model_selected() and self.settings.strict_model_check:
            raise ProviderError(f"{self.settings.preferred_model} seçilemedi; mesaj gönderilmedi.")

        input_box = wait_for_visible(
            self.page,
            INPUT_SELECTORS,
            timeout_ms=self.settings.page_timeout_seconds * 1_000,
        )
        if input_box is None:
            state = self.detect_page_state()
            raise ProviderError(f"Gemini mesaj kutusu bulunamadı. Sayfa durumu: {state}")

        baselines = self._capture_response_baselines()
        self._write_prompt(input_box, prompt)
        self._send_prompt(input_box)

        self._start_animation()
        try:
            response = self._wait_for_new_response(baselines, timeout_s=60)
            return self._wait_for_response_to_stabilize(
                response,
                timeout_s=self.settings.response_timeout_seconds,
            )
        finally:
            self._stop_animation()

    def _find_model_button(self) -> Optional[Locator]:
        selectors = (
            'button[aria-label*="model" i]',
            'button[data-test-id*="model" i]',
            'button:has-text("3.1 Pro")',
            'button:has-text("Pro")',
            'button:has-text("Flash")',
        )
        for selector in selectors:
            item = first_visible(self.page.locator(selector))
            if item is not None:
                return item
        return None

    def _visible_model_button_contains(self, target: str) -> bool:
        button = self._find_model_button()
        if button is None:
            return False
        text = locator_text(button).casefold()
        target_folded = target.casefold()
        return target_folded in text or (target_folded == "3.1 pro" and "pro" in text)

    def _write_prompt(self, input_box: Locator, prompt: str) -> None:
        input_box.scroll_into_view_if_needed()
        input_box.click()
        try:
            input_box.fill("")
            input_box.fill(prompt)
            return
        except PlaywrightError:
            pass
        self.page.keyboard.press("Control+A")
        self.page.keyboard.press("Backspace")
        self.page.keyboard.insert_text(prompt)

    def _send_prompt(self, input_box: Locator) -> None:
        input_box.press("Enter")
        time.sleep(0.8)
        if locator_text(input_box).strip():
            for selector in SEND_BUTTON_SELECTORS:
                button = first_visible(self.page.locator(selector))
                if button is not None and button.is_enabled():
                    button.click()
                    return
            raise ProviderError("Gemini gönder düğmesi bulunamadı.")

    def _capture_response_baselines(self) -> dict[str, tuple[int, str]]:
        result: dict[str, tuple[int, str]] = {}
        for selector in RESPONSE_SELECTORS:
            locator = self.page.locator(selector)
            count = locator.count()
            last_text = locator_text(locator.nth(count - 1)).strip() if count else ""
            result[selector] = (count, last_text)
        return result

    def _wait_for_new_response(self, baselines: dict[str, tuple[int, str]], timeout_s: int) -> Locator:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.page.is_closed():
                raise ProviderError("Yanıt beklenirken Gemini sekmesi kapandı.")
            for selector in RESPONSE_SELECTORS:
                locator = self.page.locator(selector)
                count = locator.count()
                old_count, old_text = baselines[selector]
                if count > old_count:
                    return locator.nth(count - 1)
                if count:
                    candidate = locator.nth(count - 1)
                    current = locator_text(candidate).strip()
                    if current and current != old_text:
                        return candidate
            state = self.detect_page_state()
            if state in {"blocked_signin", "login_required"}:
                raise ProviderError("Gemini oturumu mesaj gönderilirken sona erdi. Kurulum ve bakım menüsünden Google hesabı kurulumunu yenile.")
            time.sleep(0.25)
        raise ProviderError("Yeni Gemini yanıtı 60 saniye içinde başlamadı.")

    def _wait_for_response_to_stabilize(self, response: Locator, timeout_s: int) -> str:
        deadline = time.monotonic() + timeout_s
        last_text = ""
        unchanged_since: Optional[float] = None

        while time.monotonic() < deadline:
            current = locator_text(response).strip()
            now = time.monotonic()
            if current != last_text:
                last_text = current
                unchanged_since = now
            elif current and unchanged_since is not None:
                if now - unchanged_since >= self.settings.stable_seconds and not self._is_generating():
                    return self._clean_response(current)
            time.sleep(0.5)

        if last_text:
            print("\n[UYARI] Yanıt süresi doldu; alınabilen son metin gösteriliyor.")
            return self._clean_response(last_text)
        raise ProviderError("Gemini yanıt süresi doldu.")

    def _wait_until_previous_generation_finishes(self, timeout_s: int) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if not self._is_generating():
                return
            time.sleep(0.5)
        raise ProviderError("Önceki Gemini yanıtı hâlâ oluşturuluyor.")

    def _is_generating(self) -> bool:
        for selector in STOP_BUTTON_SELECTORS:
            if first_visible(self.page.locator(selector)) is not None:
                return True
        return False

    @staticmethod
    def _clean_response(text: str) -> str:
        cleaned = text.strip()
        for prefix in ("Gemini şunu dedi:", "Gemini said:"):
            if cleaned.casefold().startswith(prefix.casefold()):
                cleaned = cleaned[len(prefix):].lstrip()
        return cleaned

    def _start_animation(self) -> None:
        if os.environ.get("OS_CLI_OWNS_SPINNER") == "1":
            self.animation_thread = None
            return
        self.is_thinking = True
        self.animation_thread = threading.Thread(target=self._thinking_animation, daemon=True)
        self.animation_thread.start()

    def _stop_animation(self) -> None:
        self.is_thinking = False
        if self.animation_thread is not None:
            self.animation_thread.join(timeout=1.0)
            print("\r" + " " * 38 + "\r", end="", flush=True)
        self.animation_thread = None

    def _thinking_animation(self) -> None:
        chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        index = 0
        while self.is_thinking:
            print(f"\rGemini düşünüyor {chars[index % len(chars)]}  ", end="", flush=True)
            index += 1
            time.sleep(0.1)
