from __future__ import annotations

import base64
import ctypes
import os
from ctypes import wintypes
from pathlib import Path

from ...errors import ProviderError


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


class ApiSecretStore:
    """API anahtarını ortam değişkeninden veya Windows DPAPI kasasından okur."""

    def __init__(self, path: Path, env_name: str = "OPENAI_API_KEY"):
        self.path = path
        self.env_name = env_name

    def get(self) -> str | None:
        environment_value = os.environ.get(self.env_name, "").strip()
        if environment_value:
            return environment_value
        if not self.path.exists():
            return None
        if os.name != "nt":
            return None
        try:
            encrypted = base64.b64decode(self.path.read_bytes(), validate=True)
            clear = self._unprotect(encrypted)
            value = clear.decode("utf-8").strip()
            return value or None
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            raise ProviderError(f"OpenAI API anahtarı Windows kasasından okunamadı: {exc}") from exc

    def set(self, value: str) -> None:
        secret = value.strip()
        if not secret:
            raise ProviderError("OpenAI API anahtarı boş olamaz.")
        if os.name != "nt":
            raise ProviderError(
                "Bu işletim sisteminde kalıcı güvenli anahtar kasası desteklenmiyor. "
                f"{self.env_name} ortam değişkenini kullan."
            )
        encrypted = self._protect(secret.encode("utf-8"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_bytes(base64.b64encode(encrypted))
        os.chmod(temp, 0o600)
        temp.replace(self.path)

    def delete(self) -> bool:
        try:
            self.path.unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise ProviderError(f"OpenAI API anahtarı silinemedi: {exc}") from exc

    def configured(self) -> bool:
        return bool(os.environ.get(self.env_name, "").strip()) or self.path.exists()

    @staticmethod
    def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
        buffer = ctypes.create_string_buffer(data)
        blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        return blob, buffer

    @staticmethod
    def _windows_functions():
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        blob_pointer = ctypes.POINTER(_DataBlob)
        crypt32.CryptProtectData.argtypes = [
            blob_pointer,
            wintypes.LPCWSTR,
            blob_pointer,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            blob_pointer,
        ]
        crypt32.CryptProtectData.restype = wintypes.BOOL
        crypt32.CryptUnprotectData.argtypes = [
            blob_pointer,
            ctypes.POINTER(wintypes.LPWSTR),
            blob_pointer,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            blob_pointer,
        ]
        crypt32.CryptUnprotectData.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
        kernel32.LocalFree.restype = wintypes.HLOCAL
        return crypt32, kernel32

    @classmethod
    def _protect(cls, clear: bytes) -> bytes:
        crypt32, kernel32 = cls._windows_functions()
        input_blob, input_buffer = cls._blob(clear)
        output_blob = _DataBlob()
        flags = 0x1  # CRYPTPROTECT_UI_FORBIDDEN
        success = crypt32.CryptProtectData(
            ctypes.byref(input_blob),
            "OS OpenAI API Key",
            None,
            None,
            None,
            flags,
            ctypes.byref(output_blob),
        )
        _ = input_buffer
        if not success:
            raise ProviderError(f"Windows DPAPI şifreleme hatası: {ctypes.WinError(ctypes.get_last_error())}")
        try:
            return ctypes.string_at(output_blob.pbData, output_blob.cbData)
        finally:
            kernel32.LocalFree(output_blob.pbData)

    @classmethod
    def _unprotect(cls, encrypted: bytes) -> bytes:
        crypt32, kernel32 = cls._windows_functions()
        input_blob, input_buffer = cls._blob(encrypted)
        output_blob = _DataBlob()
        flags = 0x1  # CRYPTPROTECT_UI_FORBIDDEN
        success = crypt32.CryptUnprotectData(
            ctypes.byref(input_blob),
            None,
            None,
            None,
            None,
            flags,
            ctypes.byref(output_blob),
        )
        _ = input_buffer
        if not success:
            raise ProviderError(f"Windows DPAPI çözme hatası: {ctypes.WinError(ctypes.get_last_error())}")
        try:
            return ctypes.string_at(output_blob.pbData, output_blob.cbData)
        finally:
            kernel32.LocalFree(output_blob.pbData)
