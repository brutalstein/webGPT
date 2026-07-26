from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .models import CapabilityRecord


class CapabilityStore:
    """Thread'ler arasında connection paylaşmadan WAL tabanlı capability registry."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._schema_lock = threading.Lock()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _ensure_schema(self) -> None:
        with self._schema_lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS capabilities (
                    name TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS workspace_capabilities (
                    capability TEXT NOT NULL,
                    workspace_key TEXT NOT NULL,
                    workspace_root TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_generation INTEGER NOT NULL DEFAULT 0,
                    output_root TEXT,
                    graph_path TEXT,
                    last_error TEXT,
                    built_at TEXT,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (capability, workspace_key),
                    FOREIGN KEY (capability) REFERENCES capabilities(name) ON DELETE CASCADE
                );
                """
            )

    @staticmethod
    def _record_from_payload(payload: dict[str, Any]) -> CapabilityRecord:
        return CapabilityRecord(
            name=str(payload["name"]),
            kind=str(payload.get("kind", "python_cli")),
            version=str(payload.get("version", "unknown")),
            commit=str(payload.get("commit", "")),
            source=dict(payload.get("source", {})),
            install_root=Path(str(payload["install_root"])),
            python_executable=Path(str(payload["python_executable"])),
            module=str(payload.get("module", "")),
            scripts={str(k): str(v) for k, v in dict(payload.get("scripts", {})).items()},
            adapter=str(payload["adapter"]) if payload.get("adapter") else None,
            trusted_adapter=bool(payload.get("trusted_adapter", False)),
            enabled=bool(payload.get("enabled", True)),
            auto_start=bool(payload.get("auto_start", False)),
            auto_query=bool(payload.get("auto_query", False)),
            installed_at=str(payload.get("installed_at", "")),
            status=str(payload.get("status", "ready")),
            last_error=str(payload["last_error"]) if payload.get("last_error") else None,
            metadata=dict(payload.get("metadata", {})),
        )

    def upsert(self, record: CapabilityRecord) -> None:
        payload = record.to_wire()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO capabilities(name, payload_json, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(name) DO UPDATE SET
                    payload_json=excluded.payload_json,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (record.name, json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
            )

    def get(self, name: str) -> CapabilityRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM capabilities WHERE name=?", (name.casefold().strip(),)
            ).fetchone()
        if row is None:
            return None
        try:
            return self._record_from_payload(json.loads(str(row["payload_json"])))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def list(self) -> list[CapabilityRecord]:
        with self._connect() as connection:
            rows = connection.execute("SELECT payload_json FROM capabilities ORDER BY name").fetchall()
        result: list[CapabilityRecord] = []
        for row in rows:
            try:
                result.append(self._record_from_payload(json.loads(str(row["payload_json"]))))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
        return result

    def delete(self, name: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM capabilities WHERE name=?", (name.casefold().strip(),))

    def workspace_state(self, capability: str, workspace_key: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM workspace_capabilities WHERE capability=? AND workspace_key=?",
                (capability, workspace_key),
            ).fetchone()
        return dict(row) if row is not None else None

    def upsert_workspace_state(self, capability: str, workspace_key: str, **values: Any) -> None:
        current = self.workspace_state(capability, workspace_key) or {}
        payload = {
            "workspace_root": str(values.get("workspace_root", current.get("workspace_root", ""))),
            "status": str(values.get("status", current.get("status", "pending"))),
            "source_generation": int(values.get("source_generation", current.get("source_generation", 0) or 0)),
            "output_root": values.get("output_root", current.get("output_root")),
            "graph_path": values.get("graph_path", current.get("graph_path")),
            "last_error": values.get("last_error", current.get("last_error")),
            "built_at": values.get("built_at", current.get("built_at")),
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO workspace_capabilities(
                    capability, workspace_key, workspace_root, status, source_generation,
                    output_root, graph_path, last_error, built_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(capability, workspace_key) DO UPDATE SET
                    workspace_root=excluded.workspace_root,
                    status=excluded.status,
                    source_generation=excluded.source_generation,
                    output_root=excluded.output_root,
                    graph_path=excluded.graph_path,
                    last_error=excluded.last_error,
                    built_at=excluded.built_at,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    capability,
                    workspace_key,
                    payload["workspace_root"],
                    payload["status"],
                    payload["source_generation"],
                    payload["output_root"],
                    payload["graph_path"],
                    payload["last_error"],
                    payload["built_at"],
                ),
            )

    def list_workspace_states(self, capability: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as connection:
            if capability:
                rows = connection.execute(
                    "SELECT * FROM workspace_capabilities WHERE capability=? ORDER BY updated_at DESC",
                    (capability,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM workspace_capabilities ORDER BY updated_at DESC"
                ).fetchall()
        return [dict(row) for row in rows]

    def quick_check(self) -> str:
        with self._connect() as connection:
            row = connection.execute("PRAGMA quick_check").fetchone()
        return str(row[0]) if row else "unknown"

    def checkpoint(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
