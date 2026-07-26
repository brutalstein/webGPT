from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable


_QUERY_TOKEN_RE = re.compile(r"[\w.-]{2,}", re.UNICODE)


class ContextIndexStore:
    """Workspace başına SQLite WAL + FTS5 proje bilgi deposu.

    Her işlem kendi bağlantısını açar. Böylece watcher thread'i ile agent thread'i
    aynı sqlite3.Connection nesnesini paylaşmaz. FTS5 bulunmayan nadir Python
    derlemelerinde tablo tabanlı fallback çalışmaya devam eder.
    """

    SCHEMA_VERSION = 4

    def __init__(self, cache_root: Path, workspace_key: str):
        self.cache_root = cache_root
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.path = cache_root / f"{workspace_key}.context.sqlite3"
        self._schema_lock = threading.Lock()
        self._schema_ready = False
        self._fts_enabled: bool | None = None

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA temp_store=MEMORY")
        connection.execute("PRAGMA busy_timeout=10000")
        self._ensure_schema(connection)
        return connection

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS files (
                    path TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    language TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    analyzer_backend TEXT NOT NULL,
                    parse_errors INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL,
                    line_start INTEGER NOT NULL,
                    line_end INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    FOREIGN KEY(path) REFERENCES files(path) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path);
                CREATE TABLE IF NOT EXISTS symbols (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL,
                    name TEXT NOT NULL,
                    qualified_name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    line_start INTEGER NOT NULL,
                    line_end INTEGER NOT NULL,
                    signature TEXT NOT NULL,
                    FOREIGN KEY(path) REFERENCES files(path) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name COLLATE NOCASE);
                CREATE INDEX IF NOT EXISTS idx_symbols_path ON symbols(path);
                CREATE TABLE IF NOT EXISTS edges (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_path TEXT NOT NULL,
                    target TEXT NOT NULL,
                    target_path TEXT,
                    kind TEXT NOT NULL,
                    symbol TEXT,
                    line INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(source_path) REFERENCES files(path) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_path);
                CREATE INDEX IF NOT EXISTS idx_edges_target_path ON edges(target_path);
                CREATE INDEX IF NOT EXISTS idx_edges_symbol ON edges(symbol COLLATE NOCASE);
                CREATE TABLE IF NOT EXISTS session_files (
                    session_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    touched_at INTEGER NOT NULL,
                    PRIMARY KEY(session_id, path)
                );
                CREATE INDEX IF NOT EXISTS idx_session_files_touched ON session_files(session_id, touched_at DESC);
                """
            )
            current = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if current is None or int(current[0]) != self.SCHEMA_VERSION:
                connection.execute("DROP TABLE IF EXISTS chunks_fts")
                connection.execute("DROP TABLE IF EXISTS symbols_fts")
                connection.execute("DELETE FROM chunks")
                connection.execute("DELETE FROM symbols")
                connection.execute("DELETE FROM edges")
                connection.execute("DELETE FROM files")
                connection.execute("DELETE FROM session_files")
                connection.execute(
                    "INSERT INTO meta(key,value) VALUES('schema_version',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(self.SCHEMA_VERSION),),
                )
            try:
                connection.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5("
                    "path UNINDEXED, line_start UNINDEXED, line_end UNINDEXED, text, "
                    "tokenize=\"unicode61 remove_diacritics 2\")"
                )
                connection.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS symbols_fts USING fts5("
                    "path UNINDEXED, name, qualified_name, kind UNINDEXED, signature, "
                    "tokenize=\"unicode61 remove_diacritics 2\")"
                )
                self._fts_enabled = True
            except sqlite3.DatabaseError:
                self._fts_enabled = False
            connection.commit()
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
            self._schema_ready = True

    @property
    def fts_enabled(self) -> bool:
        if self._fts_enabled is None:
            with closing(self._connect()):
                pass
        return bool(self._fts_enabled)

    @staticmethod
    def _fingerprint(record: dict[str, Any], analyzer_version: int) -> str:
        digest = hashlib.sha256()
        digest.update(str(record.get("path", "")).encode("utf-8", errors="replace"))
        digest.update(b"\0")
        digest.update(str(record.get("size", 0)).encode())
        digest.update(b"\0")
        digest.update(str(record.get("mtime_ns", 0)).encode())
        digest.update(b"\0")
        digest.update(str(analyzer_version).encode())
        digest.update(b"\0")
        for chunk in record.get("chunks", []):
            digest.update(str(chunk.get("line_start", 0)).encode())
            digest.update(str(chunk.get("text", "")).encode("utf-8", errors="replace"))
        return digest.hexdigest()

    def sync(self, records: list[dict[str, Any]], *, analyzer_version: int, meta: dict[str, Any] | None = None) -> dict[str, int]:
        desired = {str(record.get("path", "")): record for record in records if str(record.get("path", ""))}
        changed = 0
        removed = 0
        with closing(self._connect()) as connection, connection:
            existing = {
                str(row["path"]): str(row["fingerprint"])
                for row in connection.execute("SELECT path,fingerprint FROM files")
            }
            stale_paths = sorted(set(existing) - set(desired))
            for path in stale_paths:
                self._delete_path(connection, path)
                removed += 1

            for path, record in desired.items():
                fingerprint = self._fingerprint(record, analyzer_version)
                if existing.get(path) == fingerprint:
                    continue
                self._delete_path(connection, path)
                analysis = record.get("analysis", {}) if isinstance(record.get("analysis"), dict) else {}
                connection.execute(
                    "INSERT INTO files(path,fingerprint,language,size,mtime_ns,analyzer_backend,parse_errors) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (
                        path,
                        fingerprint,
                        str(record.get("language", "Text")),
                        int(record.get("size", 0)),
                        int(record.get("mtime_ns", 0)),
                        str(analysis.get("backend", "none")),
                        int(analysis.get("parse_errors", 0)),
                    ),
                )
                for chunk in record.get("chunks", []):
                    text = str(chunk.get("text", ""))
                    content_hash = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
                    cursor = connection.execute(
                        "INSERT INTO chunks(path,line_start,line_end,text,content_hash) VALUES(?,?,?,?,?)",
                        (
                            path,
                            int(chunk.get("line_start", 1)),
                            int(chunk.get("line_end", 1)),
                            text,
                            content_hash,
                        ),
                    )
                    if self.fts_enabled:
                        connection.execute(
                            "INSERT INTO chunks_fts(rowid,path,line_start,line_end,text) VALUES(?,?,?,?,?)",
                            (
                                int(cursor.lastrowid),
                                path,
                                int(chunk.get("line_start", 1)),
                                int(chunk.get("line_end", 1)),
                                text,
                            ),
                        )
                for symbol in analysis.get("symbols", []):
                    cursor = connection.execute(
                        "INSERT INTO symbols(path,name,qualified_name,kind,line_start,line_end,signature) VALUES(?,?,?,?,?,?,?)",
                        (
                            path,
                            str(symbol.get("name", "")),
                            str(symbol.get("qualified_name", symbol.get("name", ""))),
                            str(symbol.get("kind", "symbol")),
                            int(symbol.get("line_start", 1)),
                            int(symbol.get("line_end", 1)),
                            str(symbol.get("signature", "")),
                        ),
                    )
                    if self.fts_enabled:
                        connection.execute(
                            "INSERT INTO symbols_fts(rowid,path,name,qualified_name,kind,signature) VALUES(?,?,?,?,?,?)",
                            (
                                int(cursor.lastrowid),
                                path,
                                str(symbol.get("name", "")),
                                str(symbol.get("qualified_name", symbol.get("name", ""))),
                                str(symbol.get("kind", "symbol")),
                                str(symbol.get("signature", "")),
                            ),
                        )
                for imported in analysis.get("imports", []):
                    connection.execute(
                        "INSERT INTO edges(source_path,target,target_path,kind,symbol,line) VALUES(?,?,?,?,?,?)",
                        (
                            path,
                            str(imported.get("target", "")),
                            imported.get("target_path"),
                            "import",
                            None,
                            int(imported.get("line", 0)),
                        ),
                    )
                for reference in analysis.get("references", []):
                    connection.execute(
                        "INSERT INTO edges(source_path,target,target_path,kind,symbol,line) VALUES(?,?,?,?,?,?)",
                        (
                            path,
                            str(reference.get("name", "")),
                            reference.get("target_path"),
                            str(reference.get("kind", "reference")),
                            str(reference.get("name", "")),
                            int(reference.get("line", 0)),
                        ),
                    )
                changed += 1

            for key, value in (meta or {}).items():
                connection.execute(
                    "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(key), json.dumps(value, ensure_ascii=False, separators=(",", ":"))),
                )
        return {"changed": changed, "removed": removed, "files": len(desired)}

    def _delete_path(self, connection: sqlite3.Connection, path: str) -> None:
        if self.fts_enabled:
            connection.execute("DELETE FROM chunks_fts WHERE path=?", (path,))
            connection.execute("DELETE FROM symbols_fts WHERE path=?", (path,))
        connection.execute("DELETE FROM edges WHERE source_path=? OR target_path=?", (path, path))
        connection.execute("DELETE FROM chunks WHERE path=?", (path,))
        connection.execute("DELETE FROM symbols WHERE path=?", (path,))
        connection.execute("DELETE FROM files WHERE path=?", (path,))

    @staticmethod
    def _match_query(query: str) -> str:
        expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", query)
        candidates = list(_QUERY_TOKEN_RE.findall(query)) + list(
            _QUERY_TOKEN_RE.findall(expanded.replace("_", " ").replace("-", " "))
        )
        tokens: list[str] = []
        seen: set[str] = set()
        for token in candidates:
            token = token.strip(".-_")
            folded = token.casefold()
            if token and folded not in seen:
                seen.add(folded)
                tokens.append(token)
        return " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens[:24])

    def search_chunks(self, query: str, *, limit: int = 30) -> list[dict[str, Any]]:
        match = self._match_query(query)
        if not match:
            return []
        with closing(self._connect()) as connection, connection:
            if self.fts_enabled:
                try:
                    rows = connection.execute(
                        "SELECT path,line_start,line_end,text,rank FROM chunks_fts "
                        "WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?",
                        (match, max(1, min(200, int(limit)))),
                    ).fetchall()
                    return [
                        {
                            "path": str(row["path"]),
                            "line_start": int(row["line_start"]),
                            "line_end": int(row["line_end"]),
                            "text": str(row["text"]),
                            "fts_score": max(0.0, -float(row["rank"])),
                        }
                        for row in rows
                    ]
                except sqlite3.DatabaseError:
                    pass
            tokens = [item.casefold() for item in _QUERY_TOKEN_RE.findall(query)[:16]]
            if not tokens:
                return []
            clauses = " OR ".join("lower(text) LIKE ?" for _ in tokens)
            rows = connection.execute(
                f"SELECT path,line_start,line_end,text FROM chunks WHERE {clauses} LIMIT ?",
                tuple(f"%{token}%" for token in tokens) + (max(1, min(200, int(limit))),),
            ).fetchall()
            return [
                {
                    "path": str(row["path"]),
                    "line_start": int(row["line_start"]),
                    "line_end": int(row["line_end"]),
                    "text": str(row["text"]),
                    "fts_score": 0.1,
                }
                for row in rows
            ]

    def search_symbols(self, query: str, *, limit: int = 30) -> list[dict[str, Any]]:
        match = self._match_query(query)
        if not match:
            return []
        with closing(self._connect()) as connection, connection:
            if self.fts_enabled:
                try:
                    rows = connection.execute(
                        "SELECT s.path,s.name,s.qualified_name,s.kind,s.line_start,s.line_end,s.signature,f.rank "
                        "FROM symbols_fts AS f JOIN symbols AS s ON s.id=f.rowid "
                        "WHERE symbols_fts MATCH ? ORDER BY f.rank LIMIT ?",
                        (match, max(1, min(200, int(limit)))),
                    ).fetchall()
                    return [dict(row) | {"fts_score": max(0.0, -float(row["rank"]))} for row in rows]
                except sqlite3.DatabaseError:
                    pass
            tokens = [item.casefold() for item in _QUERY_TOKEN_RE.findall(query)[:16]]
            if not tokens:
                return []
            clauses = " OR ".join("lower(name) LIKE ? OR lower(qualified_name) LIKE ?" for _ in tokens)
            params: list[Any] = []
            for token in tokens:
                params.extend((f"%{token}%", f"%{token}%"))
            params.append(max(1, min(200, int(limit))))
            rows = connection.execute(
                f"SELECT path,name,qualified_name,kind,line_start,line_end,signature FROM symbols WHERE {clauses} LIMIT ?",
                tuple(params),
            ).fetchall()
            return [dict(row) | {"fts_score": 0.1} for row in rows]

    def related_paths(self, paths: Iterable[str], *, limit: int = 20) -> list[dict[str, Any]]:
        selected = sorted({str(item) for item in paths if str(item)})[:40]
        if not selected:
            return []
        placeholders = ",".join("?" for _ in selected)
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                f"""
                SELECT source_path,target,target_path,kind,symbol,line
                FROM edges
                WHERE source_path IN ({placeholders}) OR target_path IN ({placeholders})
                ORDER BY CASE kind WHEN 'import' THEN 0 ELSE 1 END, source_path
                LIMIT ?
                """,
                tuple(selected) + tuple(selected) + (max(1, min(200, int(limit))),),
            ).fetchall()
            return [dict(row) for row in rows]

    def symbol_impact(self, name_or_path: str, *, limit: int = 60) -> dict[str, Any]:
        needle = str(name_or_path).strip()
        if not needle:
            return {"query": needle, "definitions": [], "edges": [], "related_paths": []}
        with closing(self._connect()) as connection, connection:
            definitions = connection.execute(
                "SELECT path,name,qualified_name,kind,line_start,line_end,signature FROM symbols "
                "WHERE lower(name)=lower(?) OR lower(qualified_name)=lower(?) OR path=? "
                "ORDER BY path,line_start LIMIT ?",
                (needle, needle, needle, max(1, min(200, int(limit)))),
            ).fetchall()
            edges = connection.execute(
                "SELECT source_path,target,target_path,kind,symbol,line FROM edges "
                "WHERE lower(symbol)=lower(?) OR lower(target)=lower(?) OR source_path=? OR target_path=? "
                "ORDER BY kind,source_path,line LIMIT ?",
                (needle, needle, needle, needle, max(1, min(200, int(limit)))),
            ).fetchall()
        related = set()
        for row in definitions:
            related.add(str(row["path"]))
        for row in edges:
            related.add(str(row["source_path"]))
            if row["target_path"]:
                related.add(str(row["target_path"]))
        return {
            "query": needle,
            "definitions": [dict(row) for row in definitions],
            "edges": [dict(row) for row in edges],
            "related_paths": sorted(related),
        }

    def touch_session_paths(self, session_id: str, paths: Iterable[str], *, limit: int = 24) -> None:
        session_id = str(session_id).strip()
        normalized = sorted({str(path).replace("\\", "/").strip("/") for path in paths if str(path).strip()})
        if not session_id or not normalized:
            return
        stamp = time.time_ns()
        with closing(self._connect()) as connection, connection:
            for offset, path in enumerate(normalized):
                connection.execute(
                    "INSERT INTO session_files(session_id,path,touched_at) VALUES(?,?,?) "
                    "ON CONFLICT(session_id,path) DO UPDATE SET touched_at=excluded.touched_at",
                    (session_id, path, stamp + offset),
                )
            connection.execute(
                "DELETE FROM session_files WHERE session_id=? AND path NOT IN ("
                "SELECT path FROM session_files WHERE session_id=? ORDER BY touched_at DESC LIMIT ?)",
                (session_id, session_id, max(1, int(limit))),
            )

    def session_working_set(self, session_id: str, *, limit: int = 24) -> list[str]:
        session_id = str(session_id).strip()
        if not session_id:
            return []
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                "SELECT path FROM session_files WHERE session_id=? ORDER BY touched_at DESC LIMIT ?",
                (session_id, max(1, int(limit))),
            ).fetchall()
        return [str(row["path"]) for row in rows]

    def health(self, *, check_integrity: bool = False) -> dict[str, Any]:
        with closing(self._connect()) as connection, connection:
            counts = {}
            for table in ("files", "chunks", "symbols", "edges", "session_files"):
                counts[table] = int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            integrity = str(connection.execute("PRAGMA quick_check").fetchone()[0]) if check_integrity else "not_checked"
        return {
            "path": str(self.path),
            "fts5": self.fts_enabled,
            "integrity": integrity,
            **counts,
        }

    def close(self) -> None:
        # Bağlantılar işlem kapsamlıdır; burada yalnızca WAL checkpoint yapılır.
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except sqlite3.DatabaseError:
            pass
