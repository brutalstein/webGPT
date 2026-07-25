from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Iterable


CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def find_chrome_executable() -> Path | None:
    candidates = [
        Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _powershell(script: str, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        creationflags=CREATE_NO_WINDOW,
    )


def list_profile_process_ids(profile_dir: Path) -> list[int]:
    if os.name != "nt":
        return []
    needle = str(profile_dir).replace("'", "''")
    script = rf"""
$needle = '{needle}'
$items = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object {{
    $_.Name -eq 'chrome.exe' -and
    $_.CommandLine -and
    $_.CommandLine -like "*$needle*"
  }} |
  Select-Object -ExpandProperty ProcessId
$items | ConvertTo-Json -Compress
"""
    try:
        result = _powershell(script)
    except (OSError, subprocess.SubprocessError):
        return []
    raw = result.stdout.strip()
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(value, int):
        return [value]
    if isinstance(value, list):
        return [int(item) for item in value if isinstance(item, int)]
    return []


def terminate_profile_processes(profile_dir: Path, *, wait_seconds: float = 4.0) -> list[int]:
    pids = list_profile_process_ids(profile_dir)
    if not pids:
        return []

    if os.name == "nt":
        pid_list = ",".join(str(pid) for pid in pids)
        script = rf"""
$ids = @({pid_list})
foreach ($id in $ids) {{
  Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
}}
"""
        try:
            _powershell(script)
        except (OSError, subprocess.SubprocessError):
            pass

    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if not list_profile_process_ids(profile_dir):
            break
        time.sleep(0.25)
    return pids


def wait_profile_processes_to_close(profile_dir: Path, timeout_seconds: int) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not list_profile_process_ids(profile_dir):
            return True
        time.sleep(0.5)
    return not list_profile_process_ids(profile_dir)


def reserve_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def chrome_version(chrome_path: Path) -> str:
    if os.name != "nt":
        return "Bilinmiyor"
    escaped = str(chrome_path).replace("'", "''")
    script = rf"(Get-Item '{escaped}').VersionInfo.ProductVersion"
    try:
        result = _powershell(script)
    except (OSError, subprocess.SubprocessError):
        return "Bilinmiyor"
    return result.stdout.strip() or "Bilinmiyor"


def query_chrome_policies() -> list[str]:
    if os.name != "nt":
        return []
    script = r"""
$paths = @(
  'HKCU:\Software\Policies\Google\Chrome',
  'HKLM:\Software\Policies\Google\Chrome'
)
$out = @()
foreach ($path in $paths) {
  if (Test-Path $path) {
    $item = Get-ItemProperty $path
    foreach ($property in $item.PSObject.Properties) {
      if ($property.Name -notmatch '^PS') {
        $out += "$path :: $($property.Name)=$($property.Value)"
      }
    }
  }
}
$out | ConvertTo-Json -Compress
"""
    try:
        result = _powershell(script)
        raw = result.stdout.strip()
        if not raw:
            return []
        value = json.loads(raw)
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [str(item) for item in value]
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        pass
    return []


def query_antivirus_products() -> list[str]:
    if os.name != "nt":
        return []
    script = r"""
$items = Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntivirusProduct -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty displayName
$items | ConvertTo-Json -Compress
"""
    try:
        result = _powershell(script)
        raw = result.stdout.strip()
        if not raw:
            return []
        value = json.loads(raw)
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [str(item) for item in value]
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        pass
    return []


def remove_stale_lock_files(profile_dir: Path) -> list[Path]:
    """Yalnızca profile ait Chrome süreçleri tamamen kapalıysa kilitleri temizler."""
    if list_profile_process_ids(profile_dir):
        return []
    removed: list[Path] = []
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile"):
        path = profile_dir / name
        try:
            if path.is_symlink() or path.is_file():
                path.unlink(missing_ok=True)
                removed.append(path)
            elif path.is_dir():
                import shutil

                shutil.rmtree(path, ignore_errors=True)
                removed.append(path)
        except OSError:
            continue
    return removed


def safe_delete_directories(paths: Iterable[Path]) -> list[Path]:
    import shutil

    deleted: list[Path] = []
    for path in paths:
        try:
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)
                if not path.exists():
                    deleted.append(path)
        except OSError:
            continue
    return deleted
