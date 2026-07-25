from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

import pyperclip

from ..config import ProviderSettings
from ..errors import ClipboardBridgeError, ProviderError
from .gemini_chrome.processes import list_profile_process_ids


@dataclass(frozen=True, slots=True)
class ChatGPTCompanionSettings:
    """ChatGPT kullanıcı kontrollü companion çalışma ayarları."""

    background_idle: bool
    restore_for_interaction: bool
    minimize_after_exchange: bool
    restore_clipboard_after_capture: bool
    window_wait_seconds: int
    clipboard_retry_count: int

    @classmethod
    def from_provider(cls, settings: ProviderSettings) -> "ChatGPTCompanionSettings":
        return cls(
            background_idle=bool(settings.get("background_idle", True)),
            restore_for_interaction=bool(settings.get("restore_for_interaction", True)),
            minimize_after_exchange=bool(settings.get("minimize_after_exchange", True)),
            restore_clipboard_after_capture=bool(settings.get("restore_clipboard_after_capture", False)),
            window_wait_seconds=max(2, int(settings.get("window_wait_seconds", 15))),
            clipboard_retry_count=max(1, int(settings.get("clipboard_retry_count", 3))),
        )


class ClipboardExchange:
    """Windows panosu üzerinden kontrollü prompt/yanıt alışverişi."""

    @staticmethod
    def read() -> str:
        try:
            value = pyperclip.paste()
        except pyperclip.PyperclipException as exc:
            raise ProviderError(f"Windows panosu okunamadı: {exc}") from exc
        return value if isinstance(value, str) else ""

    @staticmethod
    def write(value: str) -> None:
        try:
            pyperclip.copy(value)
        except pyperclip.PyperclipException as exc:
            raise ClipboardBridgeError(f"Windows panosuna yazılamadı: {exc}") from exc

    @staticmethod
    def sequence_number() -> int | None:
        if os.name != "nt":
            return None
        try:
            return int(ctypes.windll.user32.GetClipboardSequenceNumber())
        except (AttributeError, OSError):
            return None

    @staticmethod
    def is_response_candidate(
        value: str,
        *,
        prompt: str,
        previous: str,
        clipboard_changed: bool | None = None,
    ) -> bool:
        candidate = value.strip()
        if not candidate:
            return False
        if candidate == prompt.strip():
            return False
        if clipboard_changed is False:
            return False
        if clipboard_changed is None and previous.strip() and candidate == previous.strip():
            return False
        return True


class ChatGPTWindowController:
    """Yalnızca OS ChatGPT profiline ait Chrome penceresini yönetir.

    Tarayıcı içeriğine erişmez. Windows top-level pencere yönetimi ile yalnızca
    minimize/restore/focus işlemleri uygulanır. Windows dışındaki sistemlerde
    güvenli no-op davranışı kullanılır.
    """

    SW_MINIMIZE = 6
    SW_RESTORE = 9

    def __init__(self, profile_dir: Path):
        self.profile_dir = Path(profile_dir)
        self._last_handle: int | None = None

    @property
    def supported(self) -> bool:
        return os.name == "nt"

    def wait_for_window(self, timeout_seconds: int) -> bool:
        if not self.supported:
            return False
        deadline = time.monotonic() + max(1, timeout_seconds)
        while time.monotonic() < deadline:
            handle = self._find_window()
            if handle is not None:
                self._last_handle = handle
                return True
            time.sleep(0.25)
        return False

    def minimize(self) -> bool:
        handle = self._resolve_handle()
        if handle is None:
            return False
        try:
            return bool(ctypes.windll.user32.ShowWindow(handle, self.SW_MINIMIZE))
        except (AttributeError, OSError):
            return False

    def restore_and_focus(self) -> bool:
        handle = self._resolve_handle()
        if handle is None:
            return False
        try:
            user32 = ctypes.windll.user32
            user32.ShowWindow(handle, self.SW_RESTORE)
            user32.BringWindowToTop(handle)
            user32.SetForegroundWindow(handle)
            return True
        except (AttributeError, OSError):
            return False

    def _resolve_handle(self) -> int | None:
        if not self.supported:
            return None
        if self._last_handle is not None:
            try:
                if ctypes.windll.user32.IsWindow(self._last_handle):
                    return self._last_handle
            except (AttributeError, OSError):
                pass
        self._last_handle = self._find_window()
        return self._last_handle

    def _find_window(self) -> int | None:
        if not self.supported:
            return None

        process_ids = set(list_profile_process_ids(self.profile_dir))
        if not process_ids:
            return None

        matches: list[tuple[int, str]] = []
        user32 = ctypes.windll.user32
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @callback_type
        def callback(handle: int, _lparam: int) -> bool:
            if not user32.IsWindowVisible(handle):
                return True
            process_id = wintypes.DWORD()
            user32.GetWindowThreadProcessId(handle, ctypes.byref(process_id))
            if int(process_id.value) not in process_ids:
                return True
            length = user32.GetWindowTextLengthW(handle)
            buffer = ctypes.create_unicode_buffer(max(1, length + 1))
            user32.GetWindowTextW(handle, buffer, len(buffer))
            matches.append((int(handle), buffer.value))
            return True

        try:
            user32.EnumWindows(callback, 0)
        except (AttributeError, OSError):
            return None

        if not matches:
            return None
        matches.sort(key=lambda item: self._window_rank(item[1]))
        return matches[0][0]

    @staticmethod
    def _window_rank(title: str) -> tuple[int, str]:
        folded = title.casefold()
        if "chatgpt" in folded:
            rank = 0
        elif "chrome" in folded:
            rank = 1
        else:
            rank = 2
        return rank, folded
