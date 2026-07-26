from __future__ import annotations

import ctypes
import os
import re
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Mapping

from ..errors import CapabilityExecutionError
from .models import ProcessResult

_SECRET_NAME = re.compile(r"(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH|COOKIE)", re.IGNORECASE)
_ALLOWED_ENV = {
    "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP",
    "HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "LANG", "LC_ALL",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
}


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _WindowsJob:
    """Çocuk süreç ağacını timeout/close sırasında birlikte sonlandıran Job Object."""

    JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
    JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

    def __init__(self, process_handle: int, memory_limit_bytes: int):
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        self._kernel32 = kernel32
        self._handle = kernel32.CreateJobObjectW(None, None)
        if not self._handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW başarısız")
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = self.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if memory_limit_bytes > 0:
            info.BasicLimitInformation.LimitFlags |= (
                self.JOB_OBJECT_LIMIT_PROCESS_MEMORY | self.JOB_OBJECT_LIMIT_JOB_MEMORY
            )
            info.ProcessMemoryLimit = memory_limit_bytes
            info.JobMemoryLimit = memory_limit_bytes
        ok = kernel32.SetInformationJobObject(
            ctypes.c_void_p(self._handle),
            self.JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            self.close()
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject başarısız")
        ok = kernel32.AssignProcessToJobObject(ctypes.c_void_p(self._handle), ctypes.c_void_p(process_handle))
        if not ok:
            self.close()
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject başarısız")

    def close(self) -> None:
        if getattr(self, "_handle", None):
            self._kernel32.CloseHandle(ctypes.c_void_p(self._handle))
            self._handle = None


class CapabilityProcessRunner:
    def __init__(self, settings: dict):
        self.settings = settings
        self._lock = threading.RLock()
        self._active: dict[int, tuple[subprocess.Popen[bytes], _WindowsJob | None]] = {}

    def cancel_all(self) -> None:
        with self._lock:
            active = list(self._active.values())
        for process, job in active:
            self._terminate(process, job)

    @staticmethod
    def sanitized_environment(
        overrides: Mapping[str, str] | None = None,
        *,
        allow_network: bool,
    ) -> dict[str, str]:
        result: dict[str, str] = {}
        for key, value in os.environ.items():
            if key.upper() not in _ALLOWED_ENV or _SECRET_NAME.search(key):
                continue
            result[key] = value
        result.update(
            {
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                "PIP_NO_INPUT": "1",
                "GIT_TERMINAL_PROMPT": "0",
            }
        )
        if not allow_network:
            # Normal HTTP istemcilerini loopback kara deliğine yönlendirir. Bu bir kernel
            # network sandbox değildir; capability süreci yine OS izinleriyle çalışır.
            result.update(
                {
                    "HTTP_PROXY": "http://127.0.0.1:9",
                    "HTTPS_PROXY": "http://127.0.0.1:9",
                    "ALL_PROXY": "http://127.0.0.1:9",
                    "NO_PROXY": "127.0.0.1,localhost",
                }
            )
        for key, value in dict(overrides or {}).items():
            if _SECRET_NAME.search(key):
                continue
            result[str(key)] = str(value)
        return result

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes], job: _WindowsJob | None) -> None:
        if job is not None:
            job.close()
        try:
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass

    def run(
        self,
        command: list[str],
        *,
        cwd: Path,
        env_overrides: Mapping[str, str] | None = None,
        timeout_seconds: int | None = None,
        allow_network: bool = False,
        memory_limit_mb: int | None = None,
    ) -> ProcessResult:
        if not command or not all(isinstance(item, str) and item for item in command):
            raise CapabilityExecutionError("Capability komutu boş olmayan argümanlardan oluşmalı.")
        executable = Path(command[0])
        if not executable.is_absolute() or not executable.is_file():
            raise CapabilityExecutionError("Capability yalnızca doğrulanmış mutlak executable yoluyla çalıştırılabilir.")
        cwd = cwd.resolve(strict=True)
        timeout = max(1, int(timeout_seconds or self.settings.get("run_timeout_seconds", 300)))
        memory_mb = max(0, int(memory_limit_mb or self.settings.get("process_memory_limit_mb", 1536)))
        output_chars = max(2000, int(self.settings.get("max_output_chars", 60000)))
        env = self.sanitized_environment(env_overrides, allow_network=allow_network)
        started = time.monotonic()
        creationflags = 0
        popen_options: dict = {}
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            popen_options["start_new_session"] = True

        with tempfile.SpooledTemporaryFile(max_size=1_048_576) as stdout_file, tempfile.SpooledTemporaryFile(max_size=1_048_576) as stderr_file:
            try:
                process = subprocess.Popen(
                    command,
                    cwd=str(cwd),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    shell=False,
                    creationflags=creationflags,
                    **popen_options,
                )
            except OSError as exc:
                raise CapabilityExecutionError(f"Capability süreci başlatılamadı: {exc}") from exc

            job: _WindowsJob | None = None
            if os.name == "nt":
                try:
                    job = _WindowsJob(int(process._handle), memory_mb * 1024 * 1024)  # type: ignore[attr-defined]
                except OSError:
                    # Job Object kurulamazsa timeout yine ana süreci öldürür; durum sonuçta raporlanır.
                    job = None
            with self._lock:
                self._active[process.pid] = (process, job)
            timed_out = False
            try:
                returncode = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                self._terminate(process, job)
                try:
                    returncode = process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    returncode = -9
            finally:
                with self._lock:
                    self._active.pop(process.pid, None)
                if job is not None:
                    job.close()

            stdout_file.seek(0)
            stderr_file.seek(0)
            stdout = stdout_file.read().decode("utf-8", errors="replace")
            stderr = stderr_file.read().decode("utf-8", errors="replace")
        if len(stdout) > output_chars:
            stdout = "... <çıktının başı kırpıldı>\n" + stdout[-output_chars:]
        if len(stderr) > output_chars:
            stderr = "... <stderr başı kırpıldı>\n" + stderr[-output_chars:]
        return ProcessResult(
            command=tuple(command),
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            duration_ms=int((time.monotonic() - started) * 1000),
            timed_out=timed_out,
        )
