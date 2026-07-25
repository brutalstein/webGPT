from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

from ...errors import ToolError, ToolValidationError
from ..models import ToolDefinition, ToolPayload, ToolRisk
from ..policy import ToolPolicy
from ..registry import Tool, ToolContext, ToolRegistry


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def _run(
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: int,
    output_limit: int,
) -> tuple[int, str, str, int]:
    started = time.monotonic()
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            creationflags=creationflags,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"Komut {timeout_seconds} saniyede tamamlanmadı.") from exc
    except OSError as exc:
        raise ToolError(f"Komut başlatılamadı: {exc}") from exc
    duration = int((time.monotonic() - started) * 1000)
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    if len(stdout) > output_limit:
        stdout = stdout[:output_limit] + "\n... <stdout kırpıldı>"
    if len(stderr) > output_limit:
        stderr = stderr[:output_limit] + "\n... <stderr kırpıldı>"
    return completed.returncode, stdout, stderr, duration


class RunCommandTool(Tool):
    definition = ToolDefinition(
        name="run_command",
        title="Terminal komutu çalıştır",
        description=(
            "Seçili çalışma alanı içinde, kabuk kullanmadan, izin verilen programlardan birini "
            "argüman listesiyle çalıştırır. Örnek command: [\"python\", \"-m\", \"unittest\"]."
        ),
        input_schema=_schema(
            {
                "command": {"type": "array", "items": {"type": "string"}},
                "cwd": {"type": "string", "description": "Çalışma alanına göre alt klasör"},
                "timeout_seconds": {"type": "integer"},
            },
            ["command"],
        ),
        risk=ToolRisk.EXECUTE,
        idempotent=False,
    )

    def summarize(self, arguments: dict[str, Any]) -> str:
        command = arguments.get("command", [])
        return "Komut çalıştır: " + " ".join(str(item) for item in command)

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolPayload:
        command = arguments["command"]
        if not isinstance(command, list) or not command or not all(isinstance(item, str) for item in command):
            raise ToolValidationError("command boş olmayan bir metin listesi olmalı.")
        policy = ToolPolicy(context.settings)
        policy.validate_command(command)
        cwd = context.workspace.resolve(arguments.get("cwd", "."), must_exist=True)
        if not cwd.is_dir():
            raise ToolValidationError("cwd bir klasör olmalı.")
        default_timeout = max(1, int(context.settings.get("command_timeout_seconds", 120)))
        timeout = min(default_timeout, max(1, int(arguments.get("timeout_seconds", default_timeout))))
        output_limit = max(1000, int(context.settings.get("command_output_chars", 30000)))
        code, stdout, stderr, duration = _run(
            [str(item) for item in command],
            cwd=cwd,
            timeout_seconds=timeout,
            output_limit=output_limit,
        )
        content = (
            f"Komut: {' '.join(command)}\n"
            f"Dizin: {context.workspace.relative(cwd)}\n"
            f"Çıkış kodu: {code}\n"
            f"Süre: {duration} ms\n"
            f"--- stdout ---\n{stdout or '<boş>'}\n"
            f"--- stderr ---\n{stderr or '<boş>'}"
        )
        return ToolPayload(
            content=content,
            structured={
                "command": command,
                "cwd": context.workspace.relative(cwd),
                "exit_code": code,
                "stdout": stdout,
                "stderr": stderr,
                "duration_ms": duration,
            },
        )


class GitStatusTool(Tool):
    definition = ToolDefinition(
        name="git_status",
        title="Git durumunu oku",
        description="Seçili çalışma alanındaki Git branch ve değişiklik durumunu salt okunur biçimde döndürür.",
        input_schema=_schema({"path": {"type": "string"}}),
        risk=ToolRisk.READ,
        idempotent=True,
    )

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolPayload:
        cwd = context.workspace.resolve(arguments.get("path", "."), must_exist=True)
        if not cwd.is_dir():
            raise ToolValidationError("Git çalışma yolu klasör olmalı.")
        command = ["git", "status", "--short", "--branch"]
        ToolPolicy(context.settings).validate_command(command)
        output_limit = max(1000, int(context.settings.get("command_output_chars", 30000)))
        code, stdout, stderr, duration = _run(
            command,
            cwd=cwd,
            timeout_seconds=min(30, max(1, int(context.settings.get("command_timeout_seconds", 120)))),
            output_limit=output_limit,
        )
        if code != 0:
            raise ToolError(stderr.strip() or "Bu klasör bir Git deposu değil.")
        return ToolPayload(
            content=f"Git durumu ({context.workspace.relative(cwd)}):\n{stdout.strip() or 'Temiz çalışma ağacı'}",
            structured={
                "cwd": context.workspace.relative(cwd),
                "status": stdout,
                "duration_ms": duration,
            },
        )


def register_process_tools(registry: ToolRegistry) -> None:
    registry.register(RunCommandTool())
    registry.register(GitStatusTool())
