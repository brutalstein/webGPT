from __future__ import annotations

import json
import shutil
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from ..errors import StorageError
from ..models import utc_now_iso

SCHEMA_VERSION = 1


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_load(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


class StateDatabase:
    """OS durumunu transaction, WAL ve düzenli yedeklerle saklayan SQLite katmanı."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(
                self.path,
                timeout=30,
                isolation_level=None,
                check_same_thread=False,
            )
        except sqlite3.Error as exc:
            raise StorageError(f"Durum veritabanı açılamadı: {self.path}: {exc}") from exc

        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self._connect()
            try:
                yield connection
            finally:
                connection.close()

    def _initialize(self) -> None:
        with self.transaction() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_opened_at TEXT NOT NULL,
                    provider_state_json TEXT NOT NULL DEFAULT '{}',
                    settings_snapshot_json TEXT NOT NULL DEFAULT '{}',
                    context_snapshot_json TEXT NOT NULL DEFAULT '{}',
                    archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1))
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_provider_updated
                    ON sessions(provider, archived, updated_at DESC);

                CREATE TABLE IF NOT EXISTS messages (
                    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE,
                    UNIQUE(session_id, sequence)
                );

                CREATE INDEX IF NOT EXISTS idx_messages_session_sequence
                    ON messages(session_id, sequence);

                CREATE TABLE IF NOT EXISTS context_entries (
                    scope TEXT NOT NULL CHECK (scope IN ('global', 'provider')),
                    provider TEXT NOT NULL DEFAULT '',
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(scope, provider, key)
                );

                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    category TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE SET NULL
                );

                CREATE INDEX IF NOT EXISTS idx_events_session_created
                    ON events(session_id, created_at DESC);
                """
            )
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def metadata_get(self, key: str) -> str | None:
        with self.read() as connection:
            row = connection.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row is not None else None

    def metadata_set(self, key: str, value: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO metadata(key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, utc_now_iso()),
            )

    def quick_check(self) -> str:
        try:
            with self.read() as connection:
                row = connection.execute("PRAGMA quick_check").fetchone()
        except sqlite3.Error as exc:
            raise StorageError(f"Veritabanı bütünlük kontrolü çalışmadı: {exc}") from exc
        return str(row[0]) if row is not None else "unknown"

    def checkpoint(self) -> None:
        try:
            with self.read() as connection:
                connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except sqlite3.Error:
            pass

    def backup_now(self, backup_dir: Path, *, keep: int = 10) -> Path:
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        destination = backup_dir / f"os-state-{stamp}.db"

        with self._lock:
            source = self._connect()
            target = sqlite3.connect(destination)
            try:
                source.backup(target)
                target.commit()
            except sqlite3.Error as exc:
                destination.unlink(missing_ok=True)
                raise StorageError(f"Veritabanı yedeği oluşturulamadı: {exc}") from exc
            finally:
                target.close()
                source.close()

        backups = sorted(backup_dir.glob("os-state-*.db"), key=lambda item: item.stat().st_mtime, reverse=True)
        for old in backups[max(1, keep):]:
            old.unlink(missing_ok=True)
        return destination

    def backup_if_due(self, backup_dir: Path, *, interval_hours: int = 24, keep: int = 10) -> Path | None:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return None
        backups = sorted(backup_dir.glob("os-state-*.db"), key=lambda item: item.stat().st_mtime, reverse=True)
        if backups:
            age_seconds = datetime.now(timezone.utc).timestamp() - backups[0].stat().st_mtime
            if age_seconds < max(1, interval_hours) * 3600:
                return None
        return self.backup_now(backup_dir, keep=keep)

    def import_legacy_sessions(self, path: Path) -> int:
        marker = f"legacy_sessions_imported:{path.resolve()}"
        if self.metadata_get(marker) == "1" or not path.exists():
            return 0

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return 0

        sessions = payload.get("sessions", {}) if isinstance(payload, dict) else {}
        if not isinstance(sessions, dict):
            return 0

        imported = 0
        with self.transaction() as connection:
            for session_id, item in sessions.items():
                if not isinstance(item, dict):
                    continue
                provider = str(item.get("provider", "gemini") or "gemini")
                title = str(item.get("title", "Yeni oturum") or "Yeni oturum")
                created_at = str(item.get("created_at", utc_now_iso()))
                updated_at = str(item.get("updated_at", created_at))
                provider_state = item.get("provider_state", {})
                connection.execute(
                    """
                    INSERT OR IGNORE INTO sessions(
                        session_id, provider, title, created_at, updated_at, last_opened_at,
                        provider_state_json, settings_snapshot_json, context_snapshot_json, archived
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, '{}', '{}', 0)
                    """,
                    (
                        str(session_id),
                        provider,
                        title,
                        created_at,
                        updated_at,
                        updated_at,
                        _json_dump(provider_state if isinstance(provider_state, dict) else {}),
                    ),
                )
                turns = item.get("turns", [])
                if isinstance(turns, list):
                    for sequence, turn in enumerate(turns):
                        if not isinstance(turn, dict):
                            continue
                        connection.execute(
                            """
                            INSERT OR IGNORE INTO messages(
                                session_id, sequence, role, content, created_at, metadata_json
                            ) VALUES (?, ?, ?, ?, ?, '{}')
                            """,
                            (
                                str(session_id),
                                sequence,
                                str(turn.get("role", "user")),
                                str(turn.get("text", "")),
                                str(turn.get("created_at", updated_at)),
                            ),
                        )
                imported += 1

            connection.execute(
                """
                INSERT INTO metadata(key, value, updated_at) VALUES (?, '1', ?)
                ON CONFLICT(key) DO UPDATE SET value = '1', updated_at = excluded.updated_at
                """,
                (marker, utc_now_iso()),
            )
        return imported

    def import_legacy_memory(self, path: Path) -> int:
        marker = f"legacy_memory_imported:{path.resolve()}"
        if self.metadata_get(marker) == "1" or not path.exists():
            return 0

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return 0
        if not isinstance(payload, dict):
            return 0

        imported = 0
        with self.transaction() as connection:
            global_entries = payload.get("global", {})
            if isinstance(global_entries, dict):
                for key, item in global_entries.items():
                    value = item.get("value") if isinstance(item, dict) else item
                    if value is None:
                        continue
                    connection.execute(
                        """
                        INSERT INTO context_entries(scope, provider, key, value, updated_at)
                        VALUES ('global', '', ?, ?, ?)
                        ON CONFLICT(scope, provider, key) DO UPDATE SET
                            value = excluded.value, updated_at = excluded.updated_at
                        """,
                        (str(key), str(value), utc_now_iso()),
                    )
                    imported += 1

            providers = payload.get("providers", {})
            if isinstance(providers, dict):
                for provider, entries in providers.items():
                    if not isinstance(entries, dict):
                        continue
                    for key, item in entries.items():
                        value = item.get("value") if isinstance(item, dict) else item
                        if value is None:
                            continue
                        connection.execute(
                            """
                            INSERT INTO context_entries(scope, provider, key, value, updated_at)
                            VALUES ('provider', ?, ?, ?, ?)
                            ON CONFLICT(scope, provider, key) DO UPDATE SET
                                value = excluded.value, updated_at = excluded.updated_at
                            """,
                            (str(provider), str(key), str(value), utc_now_iso()),
                        )
                        imported += 1

            connection.execute(
                """
                INSERT INTO metadata(key, value, updated_at) VALUES (?, '1', ?)
                ON CONFLICT(key) DO UPDATE SET value = '1', updated_at = excluded.updated_at
                """,
                (marker, utc_now_iso()),
            )
        return imported

    def copy_legacy_files(self, files: list[Path], backup_dir: Path) -> list[Path]:
        backup_dir.mkdir(parents=True, exist_ok=True)
        copied: list[Path] = []
        for source in files:
            if not source.is_file():
                continue
            destination = backup_dir / f"legacy-{source.name}"
            try:
                shutil.copy2(source, destination)
                copied.append(destination)
            except OSError:
                pass
        return copied


__all__ = ["StateDatabase", "_json_dump", "_json_load"]
