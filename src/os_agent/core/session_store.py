from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from ..models import utc_now_iso
from .storage import StateDatabase, _json_dump, _json_load


class SessionStore:
    """Konuşmaları, mesajları ve provider durumunu SQLite üzerinde saklar."""

    def __init__(self, database: StateDatabase | Path, legacy_json_path: Path | None = None):
        if isinstance(database, StateDatabase):
            self.database = database
        else:
            source = Path(database)
            if source.suffix.casefold() == ".json":
                legacy_json_path = legacy_json_path or source
                source = source.with_name("os-state.db")
            self.database = StateDatabase(source)
        if legacy_json_path is not None:
            self.database.import_legacy_sessions(legacy_json_path)

    @staticmethod
    def _session_from_row(row: Any) -> dict[str, Any]:
        return {
            "session_id": str(row["session_id"]),
            "provider": str(row["provider"]),
            "title": str(row["title"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "last_opened_at": str(row["last_opened_at"]),
            "provider_state": _json_load(row["provider_state_json"], {}),
            "settings_snapshot": _json_load(row["settings_snapshot_json"], {}),
            "context_snapshot": _json_load(row["context_snapshot_json"], {}),
            "archived": bool(row["archived"]),
            "message_count": int(row["message_count"]) if "message_count" in row.keys() else 0,
        }

    def create(
        self,
        provider: str,
        title: str = "Yeni oturum",
        *,
        settings_snapshot: dict[str, Any] | None = None,
        context_snapshot: dict[str, Any] | None = None,
    ) -> str:
        session_id = uuid.uuid4().hex[:12]
        now = utc_now_iso()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO sessions(
                    session_id, provider, title, created_at, updated_at, last_opened_at,
                    provider_state_json, settings_snapshot_json, context_snapshot_json, archived
                ) VALUES (?, ?, ?, ?, ?, ?, '{}', ?, ?, 0)
                """,
                (
                    session_id,
                    provider.casefold().strip(),
                    title.strip() or "Yeni oturum",
                    now,
                    now,
                    now,
                    _json_dump(settings_snapshot or {}),
                    _json_dump(context_snapshot or {}),
                ),
            )
            connection.execute(
                "INSERT INTO events(session_id, category, payload_json, created_at) VALUES (?, 'session_created', '{}', ?)",
                (session_id, now),
            )
        return session_id

    def get(self, session_id: str) -> dict[str, Any] | None:
        with self.database.read() as connection:
            row = connection.execute(
                """
                SELECT s.*, COUNT(m.message_id) AS message_count
                FROM sessions s
                LEFT JOIN messages m ON m.session_id = s.session_id
                WHERE s.session_id = ?
                GROUP BY s.session_id
                """,
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            turns = connection.execute(
                """
                SELECT role, content, created_at, metadata_json
                FROM messages
                WHERE session_id = ?
                ORDER BY sequence ASC
                """,
                (session_id,),
            ).fetchall()

        record = self._session_from_row(row)
        record["turns"] = [
            {
                "role": str(turn["role"]),
                "text": str(turn["content"]),
                "created_at": str(turn["created_at"]),
                "metadata": _json_load(turn["metadata_json"], {}),
            }
            for turn in turns
        ]
        return record

    def latest_for_provider(self, provider: str) -> dict[str, Any] | None:
        rows = self.list_recent(limit=1, provider=provider, include_turns=True)
        return rows[0] if rows else None

    def add_turn(
        self,
        session_id: str,
        role: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = utc_now_iso()
        with self.database.transaction() as connection:
            session = connection.execute(
                "SELECT title FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if session is None:
                raise KeyError(f"Oturum bulunamadı: {session_id}")
            sequence_row = connection.execute(
                "SELECT COALESCE(MAX(sequence), -1) + 1 AS next_sequence FROM messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            sequence = int(sequence_row["next_sequence"])
            connection.execute(
                """
                INSERT INTO messages(session_id, sequence, role, content, created_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_id, sequence, role, text, now, _json_dump(metadata or {})),
            )
            title = str(session["title"])
            if role == "user" and title == "Yeni oturum":
                single_line = " ".join(text.split())
                title = single_line[:80] or "Yeni oturum"
            connection.execute(
                "UPDATE sessions SET title = ?, updated_at = ? WHERE session_id = ?",
                (title, now, session_id),
            )

    def update_provider_state(self, session_id: str, state: dict[str, Any]) -> None:
        if not isinstance(state, dict):
            raise TypeError("Provider durumu dict olmalı.")
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE sessions SET provider_state_json = ?, updated_at = ? WHERE session_id = ?",
                (_json_dump(state), utc_now_iso(), session_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Oturum bulunamadı: {session_id}")

    def update_snapshots(
        self,
        session_id: str,
        *,
        settings_snapshot: dict[str, Any],
        context_snapshot: dict[str, Any],
    ) -> None:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE sessions
                SET settings_snapshot_json = ?, context_snapshot_json = ?, updated_at = ?
                WHERE session_id = ?
                """,
                (
                    _json_dump(settings_snapshot),
                    _json_dump(context_snapshot),
                    utc_now_iso(),
                    session_id,
                ),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Oturum bulunamadı: {session_id}")

    def touch_opened(self, session_id: str) -> None:
        now = utc_now_iso()
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE sessions SET last_opened_at = ? WHERE session_id = ?",
                (now, session_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Oturum bulunamadı: {session_id}")

    def record_event(self, session_id: str | None, category: str, payload: dict[str, Any] | None = None) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO events(session_id, category, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (session_id, category, _json_dump(payload or {}), utc_now_iso()),
            )

    def list_recent(
        self,
        limit: int = 20,
        *,
        provider: str | None = None,
        search: str | None = None,
        include_turns: bool = False,
    ) -> list[dict[str, Any]]:
        clauses = ["s.archived = 0"]
        parameters: list[Any] = []
        if provider:
            clauses.append("LOWER(s.provider) = ?")
            parameters.append(provider.casefold().strip())
        if search:
            query = f"%{search.strip()}%"
            clauses.append(
                "(s.title LIKE ? OR EXISTS (SELECT 1 FROM messages mx WHERE mx.session_id = s.session_id AND mx.content LIKE ?))"
            )
            parameters.extend((query, query))
        parameters.append(max(1, limit))

        sql = f"""
            SELECT s.*, COUNT(m.message_id) AS message_count
            FROM sessions s
            LEFT JOIN messages m ON m.session_id = s.session_id
            WHERE {' AND '.join(clauses)}
            GROUP BY s.session_id
            ORDER BY s.updated_at DESC, s.last_opened_at DESC, s.rowid DESC
            LIMIT ?
        """
        with self.database.read() as connection:
            rows = connection.execute(sql, parameters).fetchall()

        result = [self._session_from_row(row) for row in rows]
        if include_turns:
            for item in result:
                full = self.get(item["session_id"])
                item["turns"] = full.get("turns", []) if full else []
        return result

    def archive(self, session_id: str) -> None:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE sessions SET archived = 1, updated_at = ? WHERE session_id = ?",
                (utc_now_iso(), session_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Oturum bulunamadı: {session_id}")

    def delete(self, session_id: str) -> bool:
        """Konuşmayı ve ON DELETE CASCADE bağlı mesajlarını kalıcı olarak siler."""
        with self.database.transaction() as connection:
            cursor = connection.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        return cursor.rowcount > 0

    def recent_turns(self, session_id: str, limit: int = 6) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            rows = connection.execute(
                """
                SELECT role, content, created_at, metadata_json
                FROM messages
                WHERE session_id = ?
                ORDER BY sequence DESC
                LIMIT ?
                """,
                (session_id, max(1, limit)),
            ).fetchall()
        return [
            {
                "role": str(row["role"]),
                "text": str(row["content"]),
                "created_at": str(row["created_at"]),
                "metadata": _json_load(row["metadata_json"], {}),
            }
            for row in reversed(rows)
        ]
