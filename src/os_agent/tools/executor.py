from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..errors import OSErrorBase


@dataclass(slots=True)
class CommandResult:
    command: str
    return_code: int
    stdout: str
    stderr: str


class LocalCommandExecutor:
    """
    Yerel komut katmanı için güvenli başlangıç iskeleti.

    Varsayılan olarak kapalıdır. Açıldığında yalnızca allowlist içindeki komutları
    çalıştırır ve ayara göre kullanıcı onayı ister. Modelle otomatik bağlanmamıştır.
    """

    def __init__(
        self,
        *,
        enabled: bool,
        allowed_commands: list[str],
        require_confirmation: bool = True,
    ):
        self.enabled = enabled
        self.allowed_commands = tuple(item.casefold().strip() for item in allowed_commands if item.strip())
        self.require_confirmation = require_confirmation

    def run(self, command: str, cwd: Path | None = None) -> CommandResult:
        if not self.enabled:
            raise OSErrorBase("Yerel komut çalıştırma config.json içinde kapalı.")

        parts = shlex.split(command, posix=False)
        if not parts:
            raise OSErrorBase("Boş komut çalıştırılamaz.")
        executable = parts[0].casefold()
        if executable not in self.allowed_commands:
            raise OSErrorBase(f"Komut allowlist içinde değil: {parts[0]}")

        if self.require_confirmation:
            answer = input(f"Yerelde çalıştırılsın mı? {command} [e/H]: ").strip().casefold()
            if answer not in {"e", "evet", "y", "yes"}:
                raise OSErrorBase("Komut kullanıcı tarafından iptal edildi.")

        completed = subprocess.run(
            parts,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return CommandResult(
            command=command,
            return_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
