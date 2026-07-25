from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

from ..errors import ToolPolicyError
from .models import ToolDefinition, ToolRisk


class ToolPolicy:
    """Araç görünürlüğü, onay gereksinimi ve komut güvenliği politikası."""

    def __init__(self, settings: dict[str, Any]):
        self.settings = settings
        self.allowed_tools = {str(item) for item in settings.get("allowed_tools", [])}
        self.allowed_executables = {
            Path(str(item)).name.casefold() for item in settings.get("allowed_executables", [])
        }
        self.blocked_patterns = [
            re.compile(str(item), re.IGNORECASE)
            for item in settings.get("blocked_command_patterns", [])
        ]
        self.sensitive_globs = [str(item) for item in settings.get("sensitive_file_globs", [])]

    def tool_allowed(self, name: str) -> bool:
        return not self.allowed_tools or name in self.allowed_tools

    def require_tool(self, definition: ToolDefinition) -> None:
        if not self.tool_allowed(definition.name):
            raise ToolPolicyError(f"Araç politika tarafından kapalı: {definition.name}")

    def requires_confirmation(self, definition: ToolDefinition, arguments: dict[str, Any]) -> bool:
        if not bool(self.settings.get("require_confirmation", True)):
            return False
        if definition.risk in {ToolRisk.WRITE, ToolRisk.EXECUTE}:
            return True
        path = arguments.get("path")
        return bool(path and self.is_sensitive_path(str(path)))

    def is_sensitive_path(self, path: str) -> bool:
        normalized = path.replace("\\", "/").casefold()
        return any(fnmatch.fnmatch(normalized, pattern.casefold()) for pattern in self.sensitive_globs)

    def validate_command(self, command: list[str]) -> None:
        if not command or not all(isinstance(item, str) and item for item in command):
            raise ToolPolicyError("Komut, boş olmayan metinlerden oluşan bir liste olmalı.")
        raw_executable = command[0].strip()
        if any(separator in raw_executable for separator in ("/", "\\", ":")):
            raise ToolPolicyError("Program yolu doğrudan verilemez; yalnızca allowlist program adı kullanılabilir.")
        executable = Path(raw_executable).name.casefold()
        if self.allowed_executables and executable not in self.allowed_executables:
            raise ToolPolicyError(f"Çalıştırılmasına izin verilmeyen program: {command[0]}")
        joined = " ".join(command)
        if any(pattern.search(joined) for pattern in self.blocked_patterns):
            raise ToolPolicyError(f"Yıkıcı veya riskli komut politika tarafından engellendi: {joined}")
