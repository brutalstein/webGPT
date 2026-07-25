from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any


class ToolAuditLog:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    @staticmethod
    def _redact(arguments: dict[str, Any]) -> dict[str, Any]:
        hidden = {"content", "new_text", "old_text"}
        result: dict[str, Any] = {}
        for key, value in arguments.items():
            if key in hidden and isinstance(value, str):
                result[key] = f"<redacted:{len(value)} chars>"
            else:
                result[key] = value
        return result

    def write(
        self,
        *,
        session_id: str,
        call_id: str,
        tool: str,
        arguments: dict[str, Any],
        ok: bool,
        duration_ms: int,
        error: str | None = None,
    ) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "session_id": session_id,
            "call_id": call_id,
            "tool": tool,
            "arguments": self._redact(arguments),
            "ok": ok,
            "duration_ms": duration_ms,
            "error": error,
        }
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()
