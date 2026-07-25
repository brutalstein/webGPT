from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any

from ..models import utc_now_iso


class SessionStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict:
        if not self.path.exists():
            return {"sessions": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("sessions"), dict):
                for session in data["sessions"].values():
                    if isinstance(session, dict):
                        session.setdefault("turns", [])
                        session.setdefault("provider_state", {})
                return data
        except (OSError, json.JSONDecodeError):
            pass
        return {"sessions": {}}

    def _save(self, data: dict) -> None:
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)

    def create(self, provider: str, title: str = "Yeni oturum") -> str:
        with self._lock:
            data = self._load()
            session_id = uuid.uuid4().hex[:12]
            now = utc_now_iso()
            data["sessions"][session_id] = {
                "session_id": session_id,
                "provider": provider,
                "title": title,
                "created_at": now,
                "updated_at": now,
                "turns": [],
                "provider_state": {},
            }
            self._save(data)
            return session_id

    def get(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            session = self._load()["sessions"].get(session_id)
            if not isinstance(session, dict):
                return None
            return json.loads(json.dumps(session, ensure_ascii=False))

    def latest_for_provider(self, provider: str) -> dict[str, Any] | None:
        provider_key = provider.casefold().strip()
        with self._lock:
            sessions = [
                item
                for item in self._load()["sessions"].values()
                if isinstance(item, dict) and str(item.get("provider", "")).casefold() == provider_key
            ]
        if not sessions:
            return None
        sessions.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
        return json.loads(json.dumps(sessions[0], ensure_ascii=False))

    def add_turn(self, session_id: str, role: str, text: str) -> None:
        with self._lock:
            data = self._load()
            session = data["sessions"].get(session_id)
            if session is None:
                raise KeyError(f"Oturum bulunamadı: {session_id}")
            now = utc_now_iso()
            session["turns"].append({"role": role, "text": text, "created_at": now})
            if role == "user" and session.get("title") == "Yeni oturum":
                single_line = " ".join(text.split())
                session["title"] = single_line[:72] or "Yeni oturum"
            session["updated_at"] = now
            self._save(data)

    def update_provider_state(self, session_id: str, state: dict[str, Any]) -> None:
        if not isinstance(state, dict):
            raise TypeError("Provider durumu dict olmalı.")
        with self._lock:
            data = self._load()
            session = data["sessions"].get(session_id)
            if session is None:
                raise KeyError(f"Oturum bulunamadı: {session_id}")
            session["provider_state"] = json.loads(json.dumps(state, ensure_ascii=False))
            session["updated_at"] = utc_now_iso()
            self._save(data)

    def list_recent(self, limit: int = 10) -> list[dict]:
        with self._lock:
            sessions = list(self._load()["sessions"].values())
        sessions.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
        return sessions[:limit]
