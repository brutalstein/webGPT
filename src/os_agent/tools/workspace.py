from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any

from ..errors import WorkspaceError


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    root: str | None
    selected_at: str | None
    source: str


class WorkspaceManager:
    """Seçili çalışma alanını kalıcı tutar ve bütün yolları bu köke hapseder."""

    def __init__(self, state_path: Path, backup_root: Path, settings: dict[str, Any]):
        self.state_path = state_path
        self.backup_root = backup_root
        self.settings = settings
        self._lock = RLock()
        self._root: Path | None = None
        self._selected_at: str | None = None
        self._source = "unset"
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.backup_root.mkdir(parents=True, exist_ok=True)
        self._load()
        if self._root is None and bool(settings.get("default_to_process_cwd", True)):
            self.select(Path.cwd(), source="process_cwd")

    @property
    def root(self) -> Path | None:
        return self._root

    @property
    def active(self) -> bool:
        return self._root is not None and self._root.is_dir()

    def snapshot(self) -> WorkspaceSnapshot:
        return WorkspaceSnapshot(
            root=str(self._root) if self._root else None,
            selected_at=self._selected_at,
            source=self._source,
        )

    def select(self, path: str | Path, *, source: str = "user") -> Path:
        candidate = Path(path).expanduser()
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise WorkspaceError(f"Çalışma alanı çözümlenemedi: {candidate}: {exc}") from exc
        if not resolved.is_dir():
            raise WorkspaceError(f"Çalışma alanı klasör olmalı: {resolved}")
        if not os.access(resolved, os.R_OK):
            raise WorkspaceError(f"Çalışma alanı okunamıyor: {resolved}")

        with self._lock:
            self._root = resolved
            self._selected_at = datetime.now().isoformat(timespec="seconds")
            self._source = source
            self._save()
        return resolved

    def clear(self) -> None:
        with self._lock:
            self._root = None
            self._selected_at = None
            self._source = "unset"
            self._save()

    def require_root(self) -> Path:
        if not self.active:
            raise WorkspaceError(
                "Aktif çalışma alanı yok. os.bat --select-workspace veya "
                "os.bat --workspace <KLASÖR> ile bir klasör seç."
            )
        assert self._root is not None
        return self._root

    def resolve(
        self,
        relative_path: str | Path,
        *,
        must_exist: bool = False,
        for_write: bool = False,
    ) -> Path:
        root = self.require_root().resolve(strict=True)
        supplied = Path(str(relative_path or ".")).expanduser()
        candidate = supplied if supplied.is_absolute() else root / supplied
        try:
            resolved = candidate.resolve(strict=must_exist)
        except (OSError, RuntimeError) as exc:
            raise WorkspaceError(f"Yol çözümlenemedi: {relative_path}: {exc}") from exc

        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise WorkspaceError(
                f"Çalışma alanı dışına erişim engellendi: {relative_path}"
            ) from exc

        if for_write:
            relative = resolved.relative_to(root)
            protected = {
                str(item).casefold()
                for item in self.settings.get("protected_path_parts", [".git"])
            }
            if any(part.casefold() in protected for part in relative.parts):
                raise WorkspaceError(f"Korunan yola yazma engellendi: {relative}")
        return resolved

    def relative(self, path: Path) -> str:
        return path.resolve(strict=False).relative_to(self.require_root()).as_posix() or "."

    def backup_file(self, path: Path) -> Path | None:
        if not bool(self.settings.get("backup_writes", True)) or not path.is_file():
            return None
        root = self.require_root()
        relative = path.resolve(strict=True).relative_to(root)
        digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:12]
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        target = self.backup_root / digest / stamp / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        return target

    @staticmethod
    def atomic_write(path: Path, content: str, *, encoding: str = "utf-8") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding=encoding, newline="") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise

    def describe(self) -> dict[str, Any]:
        snapshot = self.snapshot()
        return {
            "selected": self.active,
            "root": snapshot.root,
            "current_directory": snapshot.root,
            "selected_at": snapshot.selected_at,
            "source": snapshot.source,
        }

    def _load(self) -> None:
        if not self.state_path.exists():
            return
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            root_value = payload.get("root")
            if root_value:
                root = Path(str(root_value)).expanduser().resolve(strict=True)
                if root.is_dir():
                    self._root = root
                    self._selected_at = str(payload.get("selected_at") or "") or None
                    self._source = str(payload.get("source") or "persisted")
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError):
            self._root = None

    def _save(self) -> None:
        payload = {
            "version": 1,
            "root": str(self._root) if self._root else None,
            "selected_at": self._selected_at,
            "source": self._source,
        }
        self.atomic_write(self.state_path, json.dumps(payload, ensure_ascii=False, indent=2))
