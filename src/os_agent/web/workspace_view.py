from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..errors import WorkspaceError
from ..tools import LocalToolRuntime


class WorkspaceViewService:
    """Web dosya gezgini için salt okunur ve sandbox'lı görünüm."""

    def __init__(self, runtime: LocalToolRuntime, settings: dict[str, Any]):
        self.runtime = runtime
        self.settings = settings

    def status(self) -> dict[str, Any]:
        return self.runtime.status()

    def list_tree(
        self,
        path: str = ".",
        *,
        depth: int | None = None,
        max_entries: int | None = None,
    ) -> dict[str, Any]:
        workspace = self.runtime.workspace
        target = workspace.resolve(path, must_exist=True)
        if not target.is_dir():
            raise WorkspaceError(f"Klasör bekleniyordu: {workspace.relative(target)}")

        depth_limit = min(6, max(0, int(depth if depth is not None else self.settings.get("workspace_tree_depth", 3))))
        entry_limit = min(3000, max(10, int(max_entries if max_entries is not None else self.settings.get("workspace_tree_entries", 600))))
        ignored = {
            str(item).casefold()
            for item in self.runtime.settings.get("ignored_directories", [])
        }
        records: list[dict[str, Any]] = []
        base_parts = len(target.parts)

        for current, directories, files in os.walk(target, followlinks=False):
            current_path = Path(current)
            relative_depth = len(current_path.parts) - base_parts
            directories[:] = sorted(
                [name for name in directories if name.casefold() not in ignored],
                key=str.casefold,
            )
            files = sorted(files, key=str.casefold)

            safe_directories: list[str] = []
            for name in directories:
                child = current_path / name
                try:
                    safe = workspace.resolve(child, must_exist=True)
                except WorkspaceError:
                    continue
                records.append(
                    {
                        "path": workspace.relative(safe),
                        "name": safe.name,
                        "type": "directory",
                        "size": None,
                        "symlink": safe.is_symlink(),
                    }
                )
                if not safe.is_symlink():
                    safe_directories.append(name)
                if len(records) >= entry_limit:
                    break
            directories[:] = safe_directories
            if len(records) >= entry_limit:
                break

            for name in files:
                child = current_path / name
                try:
                    safe = workspace.resolve(child, must_exist=True)
                    stat = safe.stat()
                except (OSError, WorkspaceError):
                    continue
                records.append(
                    {
                        "path": workspace.relative(safe),
                        "name": safe.name,
                        "type": "file",
                        "size": stat.st_size,
                        "modified_at": stat.st_mtime,
                        "symlink": safe.is_symlink(),
                    }
                )
                if len(records) >= entry_limit:
                    break
            if len(records) >= entry_limit:
                break
            if relative_depth >= depth_limit:
                directories[:] = []

        return {
            "root": str(workspace.require_root()),
            "path": workspace.relative(target),
            "depth": depth_limit,
            "entries": records,
            "truncated": len(records) >= entry_limit,
        }

    def read_file(self, path: str) -> dict[str, Any]:
        workspace = self.runtime.workspace
        target = workspace.resolve(path, must_exist=True)
        if not target.is_file():
            raise WorkspaceError(f"Dosya bekleniyordu: {workspace.relative(target)}")
        max_bytes = max(4096, int(self.settings.get("max_file_preview_bytes", 524288)))
        stat = target.stat()
        if stat.st_size > max_bytes:
            raise WorkspaceError(f"Dosya önizleme sınırından büyük: {stat.st_size} > {max_bytes} bayt")
        data = target.read_bytes()
        if b"\x00" in data[:4096]:
            raise WorkspaceError("İkili dosya web önizlemesinde açılamaz.")
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkspaceError("Dosya UTF-8 metni değil.") from exc
        return {
            "path": workspace.relative(target),
            "name": target.name,
            "content": content,
            "size": stat.st_size,
            "modified_at": stat.st_mtime,
            "language": self._language_for(target.suffix.casefold()),
        }

    @staticmethod
    def _language_for(suffix: str) -> str:
        return {
            ".py": "python",
            ".js": "javascript",
            ".jsx": "jsx",
            ".ts": "typescript",
            ".tsx": "tsx",
            ".json": "json",
            ".md": "markdown",
            ".html": "html",
            ".css": "css",
            ".cpp": "cpp",
            ".cc": "cpp",
            ".c": "c",
            ".h": "c",
            ".hpp": "cpp",
            ".java": "java",
            ".sh": "shell",
            ".ps1": "powershell",
            ".yml": "yaml",
            ".yaml": "yaml",
            ".toml": "toml",
        }.get(suffix, "text")
