from __future__ import annotations

from pathlib import Path

from ..models import utc_now_iso
from .storage import StateDatabase


class MemoryStore:
    """Global ve provider bağlamını aynı SQLite durum veritabanında saklar."""

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
            self.database.import_legacy_memory(legacy_json_path)

    def set(self, key: str, value: str, provider: str | None = None) -> None:
        key = key.strip()
        value = value.strip()
        if not key or not value:
            raise ValueError("Bellek anahtarı ve değeri boş olamaz.")
        scope = "provider" if provider else "global"
        provider_key = provider.casefold().strip() if provider else ""
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO context_entries(scope, provider, key, value, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(scope, provider, key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (scope, provider_key, key, value, utc_now_iso()),
            )

    def delete(self, key: str, provider: str | None = None) -> bool:
        scope = "provider" if provider else "global"
        provider_key = provider.casefold().strip() if provider else ""
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM context_entries WHERE scope = ? AND provider = ? AND key = ?",
                (scope, provider_key, key.strip()),
            )
        return cursor.rowcount > 0

    def combined(self, provider: str) -> dict[str, str]:
        provider_key = provider.casefold().strip()
        with self.database.read() as connection:
            rows = connection.execute(
                """
                SELECT scope, key, value
                FROM context_entries
                WHERE (scope = 'global' AND provider = '')
                   OR (scope = 'provider' AND provider = ?)
                ORDER BY CASE scope WHEN 'global' THEN 0 ELSE 1 END, key
                """,
                (provider_key,),
            ).fetchall()
        result: dict[str, str] = {}
        for row in rows:
            result[str(row["key"])] = str(row["value"])
        return result

    def list_entries(self) -> list[dict[str, str]]:
        with self.database.read() as connection:
            rows = connection.execute(
                """
                SELECT scope, provider, key, value, updated_at
                FROM context_entries
                ORDER BY CASE scope WHEN 'global' THEN 0 ELSE 1 END, provider, key
                """
            ).fetchall()
        return [
            {
                "scope": str(row["scope"]),
                "provider": str(row["provider"]),
                "key": str(row["key"]),
                "value": str(row["value"]),
                "updated_at": str(row["updated_at"]),
            }
            for row in rows
        ]

    def render_context(self, provider: str, max_chars: int) -> str:
        entries = self.combined(provider)
        if not entries:
            return ""
        lines = [f"- {key}: {value}" for key, value in sorted(entries.items())]
        return "\n".join(lines)[:max_chars]
