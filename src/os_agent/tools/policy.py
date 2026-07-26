from __future__ import annotations

import fnmatch
import json
import os
import re
import threading
from pathlib import Path
from typing import Any

from ..errors import ToolPolicyError
from .models import ToolDefinition, ToolRisk


class ToolPolicy:
    """Araç görünürlüğü, onay gereksinimi ve komut güvenliği politikası."""

    _SAFE_EXECUTABLES = {
        "pytest", "pytest.exe", "ctest", "ctest.exe", "ninja", "ninja.exe",
        "ruff", "ruff.exe", "mypy", "mypy.exe", "pyright", "pyright.exe",
    }
    _SAFE_GIT = {"status", "diff", "log", "show", "rev-parse", "ls-files", "grep"}
    _SAFE_PYTHON_MODULES = {"unittest", "pytest", "compileall"}
    _SAFE_NPM_SCRIPTS = {"test", "build", "lint", "check", "typecheck"}

    def __init__(self, settings: dict[str, Any], state_path: Path | None = None):
        self.settings = settings
        self.state_path = state_path
        self._lock = threading.RLock()
        self.execution_profile = str(settings.get("execution_profile", "ask")).casefold()
        self._load_state()
        self.allowed_tools = {str(item) for item in settings.get("allowed_tools", [])}
        self.allowed_executables = {
            Path(str(item)).name.casefold() for item in settings.get("allowed_executables", [])
        }
        self.blocked_patterns = [
            re.compile(str(item), re.IGNORECASE)
            for item in settings.get("blocked_command_patterns", [])
        ]
        self.sensitive_globs = [str(item) for item in settings.get("sensitive_file_globs", [])]

    def _load_state(self) -> None:
        if self.state_path is None or not self.state_path.is_file():
            return
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            profile = str(payload.get("execution_profile", "ask")).casefold()
            if profile in {"ask", "safe_auto"}:
                self.execution_profile = profile
        except (OSError, json.JSONDecodeError):
            self.execution_profile = "ask"

    def set_execution_profile(self, profile: str) -> dict[str, Any]:
        profile = str(profile).strip().casefold()
        if profile not in {"ask", "safe_auto"}:
            raise ToolPolicyError("Geçersiz terminal onay profili.")
        with self._lock:
            self.execution_profile = profile
            if self.state_path is not None:
                self.state_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.state_path.with_suffix(".tmp")
                temporary.write_text(
                    json.dumps({"schema_version": 1, "execution_profile": profile}, indent=2),
                    encoding="utf-8",
                )
                os.replace(temporary, self.state_path)
        return self.status()

    def status(self) -> dict[str, Any]:
        return {
            "execution_profile": self.execution_profile,
            "safe_auto_enabled": self.execution_profile == "safe_auto",
            "destructive_commands_always_blocked": True,
            "writes_still_require_confirmation": True,
        }

    def tool_allowed(self, name: str) -> bool:
        return not self.allowed_tools or name in self.allowed_tools

    def require_tool(self, definition: ToolDefinition) -> None:
        if not self.tool_allowed(definition.name):
            raise ToolPolicyError(f"Araç politika tarafından kapalı: {definition.name}")

    @staticmethod
    def _option_value(command: list[str], name: str) -> str | None:
        try:
            index = command.index(name)
        except ValueError:
            return None
        return command[index + 1] if index + 1 < len(command) else None

    def command_is_low_risk(self, command: Any) -> bool:
        if not isinstance(command, list) or not command or not all(isinstance(item, str) for item in command):
            return False
        try:
            self.validate_command(command)
        except ToolPolicyError:
            return False
        executable = Path(command[0]).name.casefold()
        args = command[1:]
        if executable in self._SAFE_EXECUTABLES:
            return True
        if executable in {"git", "git.exe"}:
            verb = next((item.casefold() for item in args if item and not item.startswith("-")), "")
            return verb in self._SAFE_GIT
        if executable in {"python", "python.exe", "python3", "py", "py.exe"}:
            module = self._option_value(args, "-m")
            return bool(module and module.casefold() in self._SAFE_PYTHON_MODULES)
        if executable in {"npm", "npm.cmd"}:
            if not args:
                return False
            if args[0].casefold() == "test":
                return True
            return len(args) >= 2 and args[0].casefold() == "run" and args[1].casefold() in self._SAFE_NPM_SCRIPTS
        if executable in {"node", "node.exe"}:
            return args in (["--version"], ["-v"])
        if executable in {"cmake", "cmake.exe"}:
            return "--build" in args
        return False

    def requires_confirmation(self, definition: ToolDefinition, arguments: dict[str, Any]) -> bool:
        if not bool(self.settings.get("require_confirmation", True)):
            return False
        if definition.risk is ToolRisk.WRITE:
            return True
        if definition.risk is ToolRisk.EXECUTE:
            if (
                definition.name == "run_command"
                and self.execution_profile == "safe_auto"
                and self.command_is_low_risk(arguments.get("command"))
            ):
                return False
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
