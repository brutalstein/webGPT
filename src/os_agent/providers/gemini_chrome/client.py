from __future__ import annotations

import os
import re
import threading
import time
from collections.abc import Callable
from typing import Any, Optional

from playwright.sync_api import Error as PlaywrightError, Locator, Page, TimeoutError as PlaywrightTimeoutError

from ...errors import ProviderError
from .config import GeminiChromeSettings
from .selector_health import SelectorHealthMonitor
from .utils import body_text, click_by_names, first_visible, locator_text


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
        self._page = page
        self.is_thinking = False
        self.animation_thread: Optional[threading.Thread] = None
        self._event_handler: Callable[[str, dict[str, Any]], None] | None = None
        self._cancel_event = threading.Event()
        self.last_cancelled = False
        self._selector_winners: dict[str, str] = {}
        self.selector_health = SelectorHealthMonitor(
            page,
            settings.selector_registry,
            settings.selector_health,
            settings.log_dir,
            self._emit,
        )

    def set_event_handler(self, handler: Callable[[str, dict[str, Any]], None] | None) -> None:
        self._event_handler = handler

    def request_cancel(self) -> None:
        self._cancel_event.set()

    @property
    def page(self) -> Page:
        return self._page

    @page.setter
    def page(self, page: Page) -> None:
        self._page = page
        if hasattr(self, "_selector_winners"):
            self._selector_winners.clear()
        if hasattr(self, "selector_health"):
            self.selector_health = SelectorHealthMonitor(
                page,
                self.settings.selector_registry,
                self.settings.selector_health,
                self.settings.log_dir,
                self._emit,
            )

    def attach_page(self, page: Page) -> None:
        """Yeni Playwright page nesnesini selector cache'ini taşımadan bağlar."""
        self.page = page

    def _emit(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        if self._event_handler is None:
            return
        try:
            self._event_handler(event_type, payload or {})
        except Exception:
            pass

    def _ordered_selectors(self, group: str) -> tuple[str, ...]:
        chain = self.settings.selector_registry.chain(group)
        return chain.ordered(self._selector_winners.get(group))

    def _find_by_chain(self, group: str) -> Optional[Locator]:
        chain = self.settings.selector_registry.chain(group)
        for selector in chain.ordered(self._selector_winners.get(group)):
            try:
                item = first_visible(self.page.locator(selector))
            except PlaywrightError:
                item = None
            if item is None:
                continue
            self._selector_winners[group] = selector
            self.selector_health.observe(
                group,
                selector,
                strategy="css",
                index=chain.candidates.index(selector),
            )
            return item
        return None

    @staticmethod
    def _label_pattern(label: str, *, exact: bool = False) -> re.Pattern[str]:
        escaped = re.escape(label.strip())
        return re.compile(rf"^{escaped}$" if exact else escaped, re.IGNORECASE)

    def _find_named_role(
        self,
        role: str,
        labels: tuple[str, ...],
        *,
        exact: bool = False,
    ) -> Optional[Locator]:
        for label in labels:
            if not label.strip():
                continue
            try:
                item = first_visible(
                    self.page.get_by_role(role, name=self._label_pattern(label, exact=exact))
                )
            except PlaywrightError:
                item = None
            if item is not None:
                return item
        return None

    @staticmethod
    def _is_editable_candidate(item: Locator) -> bool:
        try:
            if item.is_editable():
                return True
        except PlaywrightError:
            pass
        try:
            return bool(
                item.evaluate(
                    "node => Boolean(node && (node.isContentEditable || "
                    "node.tagName === 'TEXTAREA' || "
                    "(node.tagName === 'INPUT' && !['button','checkbox','radio','submit'].includes("
                    "String(node.type || '').toLowerCase()))))"
                )
            )
        except PlaywrightError:
            return False

    def _first_editable(self, locator: Locator, limit: int = 12) -> Optional[Locator]:
        try:
            count = min(locator.count(), limit)
        except PlaywrightError:
            return None
        for index in range(count):
            item = locator.nth(index)
            try:
                if item.is_visible() and self._is_editable_candidate(item):
                    return item
            except PlaywrightError:
                continue
        return None

    def _find_input_box_once(self) -> Optional[Locator]:
        item = self._find_by_chain("input")
        if item is not None:
            return item

        for label in self.settings.ui_labels.input:
            pattern = self._label_pattern(label)
            semantic_locators = (
                self.page.get_by_role("textbox", name=pattern),
                self.page.get_by_label(pattern),
                self.page.get_by_placeholder(pattern),
            )
            for locator in semantic_locators:
                item = self._first_editable(locator)
                if item is not None:
                    self.selector_health.observe(
                        "input",
                        f"semantic:{label}",
                        strategy="accessible-name",
                    )
                    return item

        item = self._first_editable(self.page.get_by_role("textbox"))
        if item is not None:
            self.selector_health.observe(
                "input",
                "semantic:role=textbox",
                strategy="accessible-role",
            )
        return item

    def _wait_for_input_box(self, timeout_ms: int) -> Optional[Locator]:
        deadline = time.monotonic() + max(0, timeout_ms) / 1_000
        while time.monotonic() < deadline:
            if self.page.is_closed():
                return None
            item = self._find_input_box_once()
            if item is not None:
                return item
            time.sleep(0.15)
        return None

    def _find_action_button(self, group: str, labels: tuple[str, ...]) -> Optional[Locator]:
        item = self._find_by_chain(group)
        if item is not None:
            return item
        item = self._find_named_role("button", labels)
        if item is not None:
            self.selector_health.observe(
                group,
                "semantic:role=button",
                strategy="accessible-name",
            )
        return item

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

            input_box = self._wait_for_input_box(timeout_ms=1_000)
            if input_box is not None:
                self.selector_health.install(force=False)
                self.selector_health.maybe_probe("ready", force=True)
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
        if self._wait_for_input_box(timeout_ms=250) is not None:
            return "ready"
        return "unknown"

    def _click_retry_if_present(self) -> None:
        for name in self.settings.ui_labels.retry:
            try:
                button = first_visible(self.page.get_by_role("button", name=re.compile(name, re.IGNORECASE)))
                if button is not None:
                    button.click()
                    time.sleep(1.0)
                    return
            except PlaywrightError:
                pass

    def start_new_chat(self) -> bool:
        clicked = click_by_names(self.page, self.settings.ui_labels.new_chat, timeout_ms=5_000)
        if clicked:
            time.sleep(0.8)
        return clicked

    def ensure_model_selected(self) -> bool:
        target = self.settings.preferred_model.strip()
        policy = self.settings.model_policy
        if policy.is_default(target):
            return True
        if self._visible_model_button_contains(target):
            return True

        button = self._find_model_button()
        if button is None:
            self.selector_health.record_failure("model_button", "Model seçici bulunamadı")
            return False
        try:
            button.click()
            time.sleep(0.5)
        except PlaywrightError:
            self.selector_health.record_failure("model_button", "Model seçici açılamadı")
            return False

        selected = False
        for text in policy.labels_for(target):
            regex = self._label_pattern(text)
            for role in policy.option_roles:
                try:
                    candidate = first_visible(self.page.get_by_role(role, name=regex))
                except PlaywrightError:
                    candidate = None
                if candidate is None:
                    continue
                try:
                    candidate.click()
                    selected = True
                    break
                except PlaywrightError:
                    continue
            if selected:
                break
            try:
                candidate = first_visible(self.page.get_by_text(regex, exact=False))
            except PlaywrightError:
                candidate = None
            if candidate is not None:
                try:
                    candidate.click()
                    selected = True
                    break
                except PlaywrightError:
                    pass

        if selected:
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if self._visible_model_button_contains(target):
                    self.selector_health.maybe_probe("model-selected", force=True)
                    return True
                time.sleep(0.2)

        try:
            self.page.keyboard.press("Escape")
        except PlaywrightError:
            pass
        self.selector_health.record_failure(
            "model_button",
            f"Yapılandırılmış model etiketleri eşleşmedi: {target}",
        )
        return False

    def current_model_text(self) -> str:
        button = self._find_model_button()
        return locator_text(button).strip() if button is not None else "Bilinmiyor"

    def send_prompt_and_get_response(self, prompt: str) -> str:
        prompt = prompt.strip()
        if not prompt:
            return ""

        self._cancel_event.clear()
        self.last_cancelled = False
        self.selector_health.maybe_probe("before-prompt")
        self._wait_until_previous_generation_finishes(timeout_s=30)
        if not self.ensure_model_selected() and self.settings.strict_model_check:
            raise ProviderError(f"{self.settings.preferred_model} seçilemedi; mesaj gönderilmedi.")

        input_box = self._wait_for_input_box(
            timeout_ms=self.settings.page_timeout_seconds * 1_000,
        )
        if input_box is None:
            state = self.detect_page_state()
            self.selector_health.record_failure("input", f"Mesaj kutusu bulunamadı: {state}")
            self.selector_health.maybe_probe("input-missing", force=True)
            raise ProviderError(f"Gemini mesaj kutusu bulunamadı. Sayfa durumu: {state}")

        baselines = self._capture_response_baselines()
        self._write_prompt(input_box, prompt)
        self._send_prompt(input_box)
        self._emit("generation.phase", {"phase": "thinking"})

        self._start_animation()
        try:
            response = self._wait_for_new_response(baselines, timeout_s=60)
            self._emit("generation.phase", {"phase": "responding"})
            text = self._wait_for_response_to_stabilize(
                response,
                timeout_s=self.settings.response_timeout_seconds,
            )
            self._emit(
                "generation.cancelled" if self.last_cancelled else "generation.completed",
                {"text": text, "characters": len(text)},
            )
            return text
        finally:
            self._stop_animation()
            self.selector_health.maybe_probe("after-response")

    def _find_model_button(self) -> Optional[Locator]:
        item = self._find_by_chain("model_button")
        if item is not None:
            return item
        labels = (
            *self.settings.model_policy.button_names,
            *self.settings.model_policy.labels_for(self.settings.preferred_model),
            *self.settings.model_policy.all_labels(),
        )
        item = self._find_named_role("button", tuple(dict.fromkeys(labels)))
        if item is not None:
            self.selector_health.observe(
                "model_button",
                "semantic:role=button",
                strategy="accessible-name",
            )
        return item

    def _visible_model_button_contains(self, target: str) -> bool:
        button = self._find_model_button()
        if button is None:
            return False
        return self.settings.model_policy.matches(locator_text(button), target)

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
            button = self._find_action_button("send_button", self.settings.ui_labels.send)
            if button is not None:
                try:
                    if button.is_enabled():
                        button.click()
                        return
                except PlaywrightError:
                    pass
            self.selector_health.record_failure("send_button", "Gönder düğmesi bulunamadı")
            self.selector_health.maybe_probe("send-missing", force=True)
            raise ProviderError("Gemini gönder düğmesi bulunamadı.")

    def _capture_response_baselines(self) -> dict[str, tuple[int, str]]:
        result: dict[str, tuple[int, str]] = {}
        for selector in self.settings.selector_registry.candidates("response"):
            locator = self.page.locator(selector)
            count = locator.count()
            last_text = locator_text(locator.nth(count - 1)).strip() if count else ""
            result[selector] = (count, last_text)
        return result

    def _wait_for_new_response(self, baselines: dict[str, tuple[int, str]], timeout_s: int) -> Locator:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self._cancel_event.is_set():
                self._stop_generation_if_present()
                self.last_cancelled = True
                raise ProviderError("Gemini isteği kullanıcı tarafından durduruldu.")
            if self.page.is_closed():
                raise ProviderError("Yanıt beklenirken Gemini sekmesi kapandı.")
            for selector in self._ordered_selectors("response"):
                locator = self.page.locator(selector)
                count = locator.count()
                old_count, old_text = baselines.get(selector, (0, ""))
                if count > old_count:
                    self._selector_winners["response"] = selector
                    self.selector_health.observe(
                        "response", selector, strategy="css",
                        index=self.settings.selector_registry.chain("response").candidates.index(selector),
                    )
                    return locator.nth(count - 1)
                if count:
                    candidate = locator.nth(count - 1)
                    current = locator_text(candidate).strip()
                    if current and current != old_text:
                        self._selector_winners["response"] = selector
                        self.selector_health.observe(
                            "response", selector, strategy="css",
                            index=self.settings.selector_registry.chain("response").candidates.index(selector),
                        )
                        return candidate
            state = self.detect_page_state()
            if state in {"blocked_signin", "login_required"}:
                raise ProviderError("Gemini oturumu mesaj gönderilirken sona erdi. Kurulum ve bakım menüsünden Google hesabı kurulumunu yenile.")
            time.sleep(0.25)
        self.selector_health.record_failure("response", "Yeni yanıt elementi bulunamadı")
        self.selector_health.maybe_probe("response-missing", force=True)
        raise ProviderError("Yeni Gemini yanıtı 60 saniye içinde başlamadı.")

    def _wait_for_response_to_stabilize(self, response: Locator, timeout_s: int) -> str:
        deadline = time.monotonic() + timeout_s
        last_text = ""
        unchanged_since: Optional[float] = None

        while time.monotonic() < deadline:
            current = locator_text(response).strip()
            now = time.monotonic()
            if self._cancel_event.is_set():
                self._stop_generation_if_present()
                self.last_cancelled = True
                cleaned = self._clean_response(current or last_text)
                if cleaned:
                    self._emit("generation.snapshot", {"text": cleaned, "characters": len(cleaned)})
                    return cleaned
                raise ProviderError("Gemini isteği kullanıcı tarafından durduruldu.")
            if current != last_text:
                last_text = current
                unchanged_since = now
                cleaned = self._clean_response(current)
                if cleaned:
                    self._emit("generation.snapshot", {"text": cleaned, "characters": len(cleaned)})
            elif current and unchanged_since is not None:
                if now - unchanged_since >= self.settings.stable_seconds and not self._is_generating():
                    return self._clean_response(current)
            time.sleep(0.35)

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
        return self._find_action_button("stop_button", self.settings.ui_labels.stop) is not None

    def _stop_generation_if_present(self) -> None:
        button = self._find_action_button("stop_button", self.settings.ui_labels.stop)
        if button is None:
            return
        try:
            button.click(timeout=2_000)
        except PlaywrightError:
            pass

    def selector_health_status(self) -> dict[str, Any]:
        return self.selector_health.status()

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
