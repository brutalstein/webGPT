from __future__ import annotations

import json
import threading
from pathlib import Path

from ..models import utc_now_iso


class MemoryStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict:
        if not self.path.exists():
            return {"global": {}, "providers": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("global", {})
                data.setdefault("providers", {})
                return data
        except (OSError, json.JSONDecodeError):
            pass
        return {"global": {}, "providers": {}}

    def _save(self, data: dict) -> None:
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)

    def set(self, key: str, value: str, provider: str | None = None) -> None:
        key = key.strip()
        value = value.strip()
        if not key or not value:
            raise ValueError("Bellek anahtarı ve değeri boş olamaz.")
        with self._lock:
            data = self._load()
            target = data["global"] if provider is None else data["providers"].setdefault(provider, {})
            target[key] = {"value": value, "updated_at": utc_now_iso()}
            self._save(data)

    def delete(self, key: str, provider: str | None = None) -> bool:
        with self._lock:
            data = self._load()
            target = data["global"] if provider is None else data["providers"].setdefault(provider, {})
            removed = target.pop(key, None) is not None
            if removed:
                self._save(data)
            return removed

    def combined(self, provider: str) -> dict[str, str]:
        with self._lock:
            data = self._load()
        result = {key: item["value"] for key, item in data["global"].items()}
        for key, item in data["providers"].get(provider, {}).items():
            result[key] = item["value"]
        return result

    def render_context(self, provider: str, max_chars: int) -> str:
        entries = self.combined(provider)
        if not entries:
            return ""
        lines = [f"- {key}: {value}" for key, value in sorted(entries.items())]
        text = "\n".join(lines)
        return text[:max_chars]
