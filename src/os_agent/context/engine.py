from __future__ import annotations

import fnmatch
import gzip
import hashlib
import json
import math
import os
import re
import subprocess
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..errors import WorkspaceError
from .analyzers import StructuralAnalyzer
from .store import ContextIndexStore
from .watcher import ProjectFileWatcher

if TYPE_CHECKING:
    from ..tools.models import ToolCall, ToolResult
    from ..tools.workspace import WorkspaceManager

_TOKEN_RE = re.compile(r"[\w.-]{2,}", re.UNICODE)
_FILE_HINT_RE = re.compile(r"(?:^|\s|[`'\"])([\w./\\-]+\.[A-Za-z0-9]{1,10})(?:$|\s|[`'\"])")
_SYMBOL_HINT_RE = re.compile(r"`([A-Za-z_$][\w$.:<>-]{1,180})`")
_TEXT_SUFFIXES = {
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx",
    ".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx",
    ".java", ".kt", ".kts", ".go", ".rs", ".swift", ".cs", ".fs",
    ".rb", ".php", ".scala", ".sh", ".bash", ".zsh", ".fish", ".ps1",
    ".json", ".jsonc", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".md", ".mdx", ".rst", ".txt", ".html", ".htm", ".css", ".scss", ".sass",
    ".less", ".sql", ".graphql", ".proto", ".xml", ".gradle", ".properties",
    ".cmake", ".dockerfile", ".gitignore", ".gitattributes",
}
_IMPORTANT_NAMES = {
    "readme", "readme.md", "readme.rst", "readme.txt", "agents.md", "gemini.md",
    "claude.md", "contributing.md", "architecture.md", "roadmap.md", "makefile",
    "dockerfile", "cmakelists.txt", "pyproject.toml", "package.json", "cargo.toml",
    "go.mod", "pom.xml", "build.gradle", "requirements.txt", "environment.yml",
    "compose.yaml", "compose.yml", "docker-compose.yml", "docker-compose.yaml",
}
_MANIFEST_HINTS = {
    "pyproject.toml": "Python/pyproject",
    "requirements.txt": "Python/pip",
    "setup.py": "Python/setuptools",
    "package.json": "Node.js",
    "pnpm-lock.yaml": "Node.js/pnpm",
    "yarn.lock": "Node.js/Yarn",
    "cargo.toml": "Rust/Cargo",
    "go.mod": "Go modules",
    "pom.xml": "Java/Maven",
    "build.gradle": "JVM/Gradle",
    "build.gradle.kts": "JVM/Gradle Kotlin",
    "cmakelists.txt": "CMake",
    "makefile": "Make",
    "dockerfile": "Docker",
    "compose.yaml": "Docker Compose",
    "docker-compose.yml": "Docker Compose",
}
_LANGUAGE_NAMES = {
    ".py": "Python", ".pyi": "Python", ".js": "JavaScript", ".jsx": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".c": "C", ".h": "C/C++",
    ".cc": "C++", ".cpp": "C++", ".cxx": "C++", ".hpp": "C++", ".hxx": "C++",
    ".rs": "Rust", ".go": "Go", ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin",
    ".cs": "C#", ".swift": "Swift", ".rb": "Ruby", ".php": "PHP", ".scala": "Scala",
    ".sh": "Shell", ".ps1": "PowerShell", ".html": "HTML", ".css": "CSS", ".scss": "SCSS",
    ".sql": "SQL", ".md": "Markdown",
}
_ARCHITECTURE_SIGNALS = {
    "mimari", "architecture", "genel yapı", "proje yapısı", "nasıl çalışıyor", "overview",
    "akış", "pipeline", "bağımlılık", "dependency", "modül", "module",
}
_DEBUG_SIGNALS = {
    "hata", "bug", "exception", "traceback", "fail", "başarısız", "neden", "crash", "debug",
}
_TEST_SIGNALS = {"test", "pytest", "unittest", "coverage", "regression", "doğrula"}
_CHANGE_SIGNALS = {"değiştir", "düzelt", "ekle", "sil", "refactor", "implement", "geliştir", "update"}


@dataclass(frozen=True, slots=True)
class ContextHit:
    path: str
    line_start: int
    line_end: int
    score: float
    text: str
    reason: str = "lexical"

    def to_wire(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "score": round(self.score, 4),
            "reason": self.reason,
            "text": self.text,
        }


@dataclass(frozen=True, slots=True)
class QueryPlan:
    intent: str
    terms: list[str]
    file_hints: list[str]
    symbol_hints: list[str]
    retrieval_hits: int
    symbol_hits: int
    graph_expansion: bool
    include_recent_changes: bool

    def to_wire(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "terms": self.terms,
            "file_hints": self.file_hints,
            "symbol_hints": self.symbol_hints,
            "retrieval_hits": self.retrieval_hits,
            "symbol_hits": self.symbol_hits,
            "graph_expansion": self.graph_expansion,
            "include_recent_changes": self.include_recent_changes,
        }


class ProjectContextEngine:
    """Sürekli güncel, artımlı ve yapısal proje zihin katmanı.

    Doğruluk sınırı workspace sandbox'ıdır. Native dosya olayları değişiklikleri arka
    planda indeksler; her prompt kirli bir indeks görürse bounded freshness barrier ile
    güncellemeyi bekler. SQLite FTS5 lexical retrieval, Tree-sitter sembol grafiği,
    session working-set ve change journal birlikte kullanılır.
    """

    SCHEMA_VERSION = 2

    def __init__(self, workspace: WorkspaceManager, cache_root: Path, settings: dict[str, Any]):
        self.workspace = workspace
        self.cache_root = cache_root
        self.settings = settings
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.analyzer = StructuralAnalyzer(settings)
        self._lock = threading.RLock()
        self._fresh_condition = threading.Condition(self._lock)
        self._refresh_lock = threading.Lock()
        self._dirty = True
        self._last_refresh_monotonic = 0.0
        self._index: dict[str, Any] | None = None
        self._activity_handler = None
        self._store: ContextIndexStore | None = None
        self._store_key: str | None = None

        self._generation = 0
        self._last_change_monotonic = 0.0
        self._pending_paths: set[str] = set()
        self._recent_changes: deque[dict[str, Any]] = deque(
            maxlen=max(20, int(settings.get("recent_changes", 80)))
        )
        self._working_sets: dict[str, deque[str]] = {}

        self._wake_event = threading.Event()
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._watcher: ProjectFileWatcher | None = None
        self._watch_root: str | None = None
        self._watcher_error: str | None = None

    def set_activity_handler(self, handler) -> None:
        self._activity_handler = handler

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._activity_handler is not None:
            self._activity_handler(event_type, payload)

    @property
    def enabled(self) -> bool:
        return bool(self.settings.get("enabled", True))

    @property
    def background_enabled(self) -> bool:
        return self.enabled and bool(self.settings.get("background_watch_enabled", True))

    def _workspace_key(self) -> str:
        root = str(self.workspace.require_root()).casefold().encode("utf-8", errors="replace")
        return hashlib.sha256(root).hexdigest()[:24]

    def _get_store(self) -> ContextIndexStore:
        key = self._workspace_key()
        with self._lock:
            if self._store is not None and self._store_key == key:
                return self._store
            old = self._store
            self._store = ContextIndexStore(self.cache_root, key)
            self._store_key = key
        if old is not None:
            old.close()
        return self._store

    def _cache_path(self) -> Path:
        return self.cache_root / f"{self._workspace_key()}.json.gz"

    def _load_cache(self) -> dict[str, Any] | None:
        path = self._cache_path()
        if not path.exists():
            return None
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                payload = json.load(handle)
            if payload.get("schema_version") != self.SCHEMA_VERSION:
                return None
            if payload.get("root") != str(self.workspace.require_root()):
                return None
            return payload
        except (OSError, ValueError, json.JSONDecodeError):
            return None

    def _save_cache(self, payload: dict[str, Any]) -> None:
        path = self._cache_path()
        temp = path.with_suffix(path.suffix + ".tmp")
        with gzip.open(temp, "wt", encoding="utf-8", compresslevel=5) as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        os.replace(temp, path)
        keep = max(1, int(self.settings.get("cache_keep", 20)))
        try:
            caches = sorted(
                self.cache_root.glob("*.json.gz"),
                key=lambda item: item.stat().st_mtime_ns,
                reverse=True,
            )
            for stale in caches[keep:]:
                stale.unlink(missing_ok=True)
        except OSError:
            pass

    def start(self) -> None:
        if not self.background_enabled or not self.workspace.active:
            return
        with self._lock:
            if self._worker is None or not self._worker.is_alive():
                self._stop_event.clear()
                self._worker = threading.Thread(
                    target=self._background_loop,
                    name="os-project-context",
                    daemon=True,
                )
                self._worker.start()
        self._restart_watcher()
        self.mark_dirty(reason="startup")

    def _restart_watcher(self) -> None:
        if not self.background_enabled or not self.workspace.active:
            return
        root = str(self.workspace.require_root())
        with self._lock:
            if self._watcher is not None and self._watch_root == root:
                return
            previous = self._watcher
            self._watcher = None
            self._watch_root = None
        if previous is not None:
            previous.stop()
        watcher = ProjectFileWatcher(
            Path(root),
            self._watch_event,
            ignored_directories=self.settings.get("ignored_directories", []),
            excluded_prefixes=self.settings.get("excluded_path_prefixes", [".agents/skills", ".os/skills"]),
        )
        started = watcher.start()
        with self._lock:
            self._watcher = watcher if started else None
            self._watch_root = root if started else None
            self._watcher_error = watcher.error
        self._emit(
            "context.watcher.started" if started else "context.watcher.failed",
            {"root": root, "backend": watcher.backend, "error": watcher.error},
        )

    def _watch_event(self, paths: set[str], event_type: str) -> None:
        force = event_type.startswith("directory-") or any(
            Path(path).name.casefold() in {".gitignore", ".gitmodules", "pyproject.toml", "package.json"}
            for path in paths
        )
        self.mark_dirty(paths=paths, reason=event_type, force=force)

    def workspace_changed(self) -> None:
        with self._lock:
            self._index = None
            self._dirty = True
            self._pending_paths.clear()
            self._generation += 1
            store = self._store
            self._store = None
            self._store_key = None
            self._working_sets.clear()
        if store is not None:
            store.close()
        if self.background_enabled:
            self.start()
        else:
            self.mark_dirty(reason="workspace-changed", force=True)

    def mark_dirty(
        self,
        paths: set[str] | list[str] | tuple[str, ...] | None = None,
        *,
        reason: str = "external",
        force: bool = False,
    ) -> None:
        max_pending = max(32, int(self.settings.get("max_pending_paths", 2048)))
        normalized: set[str] = set()
        for path in paths or ():
            value = str(path).replace("\\", "/").strip("/")
            if value:
                normalized.add(value)
        now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        with self._fresh_condition:
            self._dirty = True
            self._last_change_monotonic = time.monotonic()
            if force:
                self._pending_paths.clear()
                self._pending_paths.add("*")
            elif "*" not in self._pending_paths:
                self._pending_paths.update(normalized)
                if len(self._pending_paths) > max_pending:
                    self._pending_paths = {"*"}
            self._recent_changes.append(
                {
                    "timestamp": now,
                    "reason": reason,
                    "paths": sorted(normalized)[:40],
                    "force": force,
                }
            )
            self._fresh_condition.notify_all()
        self._wake_event.set()

    def _background_loop(self) -> None:
        verification = max(5.0, float(self.settings.get("verification_interval_seconds", 30.0)))
        debounce = max(0.05, float(self.settings.get("watch_debounce_ms", 450)) / 1000.0)
        next_verification = time.monotonic() + verification
        while not self._stop_event.is_set():
            timeout = max(0.05, min(verification, next_verification - time.monotonic()))
            self._wake_event.wait(timeout=timeout)
            self._wake_event.clear()
            if self._stop_event.is_set():
                break
            now = time.monotonic()
            periodic = now >= next_verification
            if not periodic:
                while not self._stop_event.is_set():
                    with self._lock:
                        remaining = debounce - (time.monotonic() - self._last_change_monotonic)
                    if remaining <= 0:
                        break
                    self._wake_event.wait(timeout=min(remaining, debounce))
                    self._wake_event.clear()
            try:
                self.refresh(force=periodic)
            except Exception as exc:
                self._emit("context.index.failed", {"error": str(exc), "background": True})
            next_verification = time.monotonic() + verification

    def wait_until_fresh(self, timeout_ms: int | None = None) -> bool:
        if not self.workspace.active:
            return False
        timeout = max(0.0, float(timeout_ms or self.settings.get("freshness_wait_ms", 1200)) / 1000.0)
        with self._fresh_condition:
            dirty = self._dirty
        if not dirty:
            return True
        self._wake_event.set()
        deadline = time.monotonic() + timeout
        with self._fresh_condition:
            while self._dirty and time.monotonic() < deadline:
                self._fresh_condition.wait(timeout=max(0.01, deadline - time.monotonic()))
            if not self._dirty:
                return True
        # Watcher kapalıysa veya yoğun indeks sürüyorsa prompt thread'i doğruluğu garanti eder.
        self.refresh(force=False)
        with self._lock:
            return not self._dirty

    def close(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        watcher = self._watcher
        self._watcher = None
        if watcher is not None:
            watcher.stop()
        worker = self._worker
        self._worker = None
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=8)
        store = self._store
        self._store = None
        if store is not None:
            store.close()

    def _git_files(self, root: Path) -> list[str] | None:
        timeout = max(2, int(self.settings.get("git_list_timeout_seconds", 12)))
        env = os.environ.copy()
        env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"})
        try:
            completed = subprocess.run(
                ["git", "-c", "core.quotepath=false", "ls-files", "-co", "--exclude-standard", "-z"],
                cwd=str(root),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if completed.returncode != 0:
            return None
        return [item.decode("utf-8", errors="surrogateescape") for item in completed.stdout.split(b"\0") if item]

    def _walk_files(self, root: Path) -> list[str]:
        ignored = {str(item).casefold() for item in self.settings.get("ignored_directories", [])}
        result: list[str] = []
        for current, directories, files in os.walk(root, followlinks=False):
            directories[:] = sorted(
                [name for name in directories if name.casefold() not in ignored],
                key=str.casefold,
            )
            current_path = Path(current)
            for name in sorted(files, key=str.casefold):
                path = current_path / name
                try:
                    if path.is_symlink():
                        continue
                    result.append(path.relative_to(root).as_posix())
                except (OSError, ValueError):
                    continue
        return result

    @staticmethod
    def _is_text_candidate(path: Path) -> bool:
        name = path.name.casefold()
        return name in _IMPORTANT_NAMES or path.suffix.casefold() in _TEXT_SUFFIXES or name.startswith("readme")

    @staticmethod
    def _decode_text(data: bytes) -> str | None:
        if b"\0" in data[:4096]:
            return None
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            try:
                return data.decode("utf-8-sig")
            except UnicodeDecodeError:
                return None

    def _chunks(self, text: str) -> list[dict[str, Any]]:
        max_chars = max(500, int(self.settings.get("chunk_chars", 1600)))
        overlap = min(max_chars // 3, max(0, int(self.settings.get("chunk_overlap_chars", 180))))
        lines = text.splitlines()
        chunks: list[dict[str, Any]] = []
        start = 0
        while start < len(lines):
            size = 0
            end = start
            while end < len(lines):
                addition = len(lines[end]) + 1
                if end > start and size + addition > max_chars:
                    break
                size += addition
                end += 1
            if end == start:
                end += 1
            chunk_text = "\n".join(lines[start:end]).strip()
            if chunk_text:
                chunks.append({"line_start": start + 1, "line_end": end, "text": chunk_text})
            if end >= len(lines):
                break
            rewind_chars = 0
            next_start = end
            while next_start > start and rewind_chars < overlap:
                next_start -= 1
                rewind_chars += len(lines[next_start]) + 1
            start = max(start + 1, next_start)
        return chunks

    def _git_snapshot(self, root: Path) -> dict[str, Any]:
        """Tek subprocess ile branch, HEAD ve tracked-dirty durumunu alır."""
        env = os.environ.copy()
        env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"})
        result: dict[str, Any] = {"repository": False, "branch": None, "head": None, "dirty": None}
        try:
            completed = subprocess.run(
                ["git", "status", "--porcelain=v2", "--branch", "--untracked-files=no"],
                cwd=root,
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=8,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return result
        if completed.returncode != 0:
            return result
        branch = "detached"
        head = None
        dirty = False
        for line in completed.stdout.splitlines():
            if line.startswith("# branch.head "):
                branch = line.removeprefix("# branch.head ").strip() or "detached"
                if branch == "(detached)":
                    branch = "detached"
            elif line.startswith("# branch.oid "):
                value = line.removeprefix("# branch.oid ").strip()
                head = value[:12] if value and value != "(initial)" else None
            elif line and not line.startswith("#"):
                dirty = True
        result.update(repository=True, branch=branch, head=head, dirty=dirty)
        return result

    @staticmethod
    def _resolve_import_target(source_path: str, target: str, paths: set[str], basename_map: dict[str, list[str]]) -> str | None:
        target = target.strip().strip("'\"<>").replace("\\", "/")
        if not target:
            return None
        source_parent = Path(source_path).parent
        candidates: list[str] = []
        if target.startswith("."):
            relative = (source_parent / target).as_posix()
            candidates.extend([relative, relative + ".py", relative + ".js", relative + ".ts", relative + ".tsx"])
            candidates.extend([f"{relative}/__init__.py", f"{relative}/index.js", f"{relative}/index.ts"])
        module_path = target.lstrip(".").replace("::", "/").replace(".", "/")
        candidates.extend(
            [
                target,
                module_path,
                module_path + ".py",
                module_path + ".js",
                module_path + ".ts",
                module_path + ".tsx",
                module_path + ".rs",
                f"{module_path}/__init__.py",
                f"{module_path}/index.js",
                f"{module_path}/index.ts",
            ]
        )
        for candidate in candidates:
            normalized = Path(candidate).as_posix().lstrip("./")
            if normalized in paths:
                return normalized
        matches = basename_map.get(Path(target).name.casefold(), [])
        return matches[0] if len(matches) == 1 else None

    def _resolve_analysis_edges(self, records: list[dict[str, Any]]) -> None:
        paths = {str(item.get("path", "")) for item in records}
        basename_map: dict[str, list[str]] = {}
        symbol_paths: dict[str, set[str]] = {}
        for record in records:
            path = str(record.get("path", ""))
            basename_map.setdefault(Path(path).name.casefold(), []).append(path)
            analysis = record.get("analysis", {})
            for symbol in analysis.get("symbols", []):
                symbol_paths.setdefault(str(symbol.get("name", "")).casefold(), set()).add(path)
        for record in records:
            source_path = str(record.get("path", ""))
            analysis = record.get("analysis", {})
            for imported in analysis.get("imports", []):
                imported["target_path"] = self._resolve_import_target(
                    source_path,
                    str(imported.get("target", "")),
                    paths,
                    basename_map,
                )
            for reference in analysis.get("references", []):
                matches = symbol_paths.get(str(reference.get("name", "")).split(".")[-1].casefold(), set())
                if len(matches) == 1:
                    reference["target_path"] = next(iter(matches))

    def refresh(self, *, force: bool = False) -> dict[str, Any]:
        with self._refresh_lock:
            return self._refresh_locked(force=force)

    def _refresh_locked(self, *, force: bool = False) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "indexed": False}
        root = self.workspace.require_root()
        min_interval = max(0.2, float(self.settings.get("refresh_min_interval_seconds", 3.0)))
        with self._lock:
            if (
                not force
                and not self._dirty
                and self._index is not None
                and time.monotonic() - self._last_refresh_monotonic < min_interval
            ):
                return self.status(refresh=False)

        started = time.monotonic()
        with self._lock:
            pending_paths = sorted(self._pending_paths)
        self._emit(
            "context.index.started",
            {"root": str(root), "force": force, "pending_paths": pending_paths[:50]},
        )
        old = self._index or self._load_cache() or {}
        old_files = {str(item.get("path")): item for item in old.get("files", [])}
        paths = self._git_files(root)
        source = "git" if paths is not None else "walk"
        if paths is None:
            paths = self._walk_files(root)

        max_files = max(50, int(self.settings.get("max_files", 3500)))
        max_file_bytes = max(4096, int(self.settings.get("max_file_bytes", 262144)))
        max_total_bytes = max(max_file_bytes, int(self.settings.get("max_total_text_bytes", 6291456)))
        paths = sorted(dict.fromkeys(paths), key=str.casefold)[: max_files * 3]

        records: list[dict[str, Any]] = []
        total_bytes = 0
        reused = 0
        skipped = 0
        analyzed = 0
        excluded_prefixes = tuple(
            str(item).strip().strip("/").casefold()
            for item in self.settings.get("excluded_path_prefixes", [".agents/skills", ".os/skills"])
            if str(item).strip()
        )
        sensitive_globs = tuple(
            str(item).strip().replace("\\", "/").casefold()
            for item in self.settings.get("sensitive_file_globs", [])
            if str(item).strip()
        )
        for relative in paths:
            if len(records) >= max_files or total_bytes >= max_total_bytes:
                break
            normalized_relative = relative.replace("\\", "/").strip("/").casefold()
            if any(
                normalized_relative == prefix or normalized_relative.startswith(prefix + "/")
                for prefix in excluded_prefixes
            ):
                continue
            if any(
                fnmatch.fnmatch(normalized_relative, pattern)
                or fnmatch.fnmatch(Path(normalized_relative).name, pattern)
                for pattern in sensitive_globs
            ):
                skipped += 1
                continue
            try:
                target = self.workspace.resolve(relative, must_exist=True)
                if not target.is_file() or target.is_symlink() or not self._is_text_candidate(target):
                    continue
                stat = target.stat()
            except (OSError, WorkspaceError):
                continue
            if stat.st_size > max_file_bytes:
                skipped += 1
                continue
            prior = old_files.get(relative)
            if (
                prior
                and int(prior.get("size", -1)) == stat.st_size
                and int(prior.get("mtime_ns", -1)) == stat.st_mtime_ns
                and int(prior.get("analysis_version", -1)) == self.analyzer.VERSION
                and isinstance(prior.get("chunks"), list)
                and isinstance(prior.get("analysis"), dict)
            ):
                record = prior
                reused += 1
            else:
                try:
                    data = target.read_bytes()
                except OSError:
                    continue
                text = self._decode_text(data)
                if text is None:
                    skipped += 1
                    continue
                structural = self.analyzer.analyze(relative, text)
                record = {
                    "path": relative,
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                    "language": _LANGUAGE_NAMES.get(target.suffix.casefold(), "Text"),
                    "chunks": self._chunks(text),
                    "analysis_version": self.analyzer.VERSION,
                    "analysis": {
                        "backend": structural.backend,
                        "language": structural.language,
                        "symbols": structural.symbols,
                        "imports": structural.imports,
                        "references": structural.references,
                        "parse_errors": structural.parse_errors,
                    },
                }
                analyzed += 1
            total_bytes += int(record.get("size", 0))
            records.append(record)

        self._resolve_analysis_edges(records)
        languages = Counter(str(item.get("language", "Text")) for item in records)
        manifests: list[dict[str, str]] = []
        instructions: list[str] = []
        top_dirs = Counter()
        parser_backends = Counter()
        symbol_kinds = Counter()
        symbol_count = 0
        edge_count = 0
        for item in records:
            path = str(item["path"])
            name = Path(path).name.casefold()
            if name in _MANIFEST_HINTS:
                manifests.append({"path": path, "kind": _MANIFEST_HINTS[name]})
            if name in {"agents.md", "gemini.md", "claude.md", "copilot-instructions.md"}:
                instructions.append(path)
            parts = Path(path).parts
            if len(parts) > 1:
                top_dirs[parts[0]] += 1
            analysis = item.get("analysis", {})
            parser_backends[str(analysis.get("backend", "none"))] += 1
            symbols = analysis.get("symbols", [])
            symbol_count += len(symbols)
            edge_count += len(analysis.get("imports", [])) + len(analysis.get("references", []))
            symbol_kinds.update(str(symbol.get("kind", "symbol")) for symbol in symbols)

        payload = {
            "schema_version": self.SCHEMA_VERSION,
            "root": str(root),
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": source,
            "files": records,
            "summary": {
                "file_count": len(records),
                "total_text_bytes": total_bytes,
                "languages": languages.most_common(12),
                "manifests": manifests[:30],
                "instruction_files": sorted(instructions)[:20],
                "top_directories": top_dirs.most_common(15),
                "truncated": len(records) >= max_files or total_bytes >= max_total_bytes,
                "skipped": skipped,
                "reused": reused,
                "analyzed": analyzed,
                "symbols": symbol_count,
                "edges": edge_count,
                "symbol_kinds": symbol_kinds.most_common(12),
                "parser_backends": parser_backends.most_common(8),
                "git": self._git_snapshot(root),
            },
        }
        store_sync = {"changed": 0, "removed": 0, "files": len(records)}
        try:
            store_sync = self._get_store().sync(
                records,
                analyzer_version=self.analyzer.VERSION,
                meta={"root": str(root), "generated_at": payload["generated_at"], "summary": payload["summary"]},
            )
        except Exception as exc:
            self._emit("context.store.failed", {"error": str(exc)})
        payload["summary"]["store_sync"] = store_sync
        try:
            self._save_cache(payload)
        except OSError:
            pass
        with self._fresh_condition:
            self._index = payload
            self._dirty = False
            self._pending_paths.clear()
            self._last_refresh_monotonic = time.monotonic()
            self._generation += 1
            generation = self._generation
            self._fresh_condition.notify_all()
        duration = int((time.monotonic() - started) * 1000)
        self._emit(
            "context.index.completed",
            {
                "root": str(root),
                "files": len(records),
                "reused": reused,
                "analyzed": analyzed,
                "symbols": symbol_count,
                "edges": edge_count,
                "generation": generation,
                "duration_ms": duration,
            },
        )
        return self.status(refresh=False)

    def _ensure(self) -> dict[str, Any]:
        self.wait_until_fresh()
        with self._lock:
            index = self._index or self._load_cache() or {}
            if self._index is None and index:
                self._index = index
            return index

    @staticmethod
    def _tokens(text: str) -> list[str]:
        expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
        expanded = expanded.replace("_", " ").replace("-", " ")
        return [item.casefold() for item in _TOKEN_RE.findall(expanded)]

    def plan_query(self, user_prompt: str) -> QueryPlan:
        folded = " ".join(str(user_prompt).casefold().split())
        if any(signal in folded for signal in _DEBUG_SIGNALS):
            intent = "debug"
        elif any(signal in folded for signal in _TEST_SIGNALS):
            intent = "test"
        elif any(signal in folded for signal in _CHANGE_SIGNALS):
            intent = "change"
        elif any(signal in folded for signal in _ARCHITECTURE_SIGNALS):
            intent = "architecture"
        else:
            intent = "lookup"
        terms = list(dict.fromkeys(self._tokens(user_prompt)))[:32]
        file_hints = list(dict.fromkeys(match.group(1).replace("\\", "/") for match in _FILE_HINT_RE.finditer(user_prompt)))[:12]
        symbol_hints = list(dict.fromkeys(match.group(1) for match in _SYMBOL_HINT_RE.finditer(user_prompt)))[:12]
        base_hits = max(3, int(self.settings.get("automatic_retrieval_hits", 7)))
        retrieval_hits = min(14, base_hits + (2 if intent in {"debug", "change", "architecture"} else 0))
        symbol_hits = min(12, max(3, int(self.settings.get("automatic_symbol_hits", 6))))
        return QueryPlan(
            intent=intent,
            terms=terms,
            file_hints=file_hints,
            symbol_hints=symbol_hints,
            retrieval_hits=retrieval_hits,
            symbol_hits=symbol_hits,
            graph_expansion=intent in {"architecture", "debug", "change", "lookup"},
            include_recent_changes=intent in {"debug", "change", "test"},
        )

    def _working_set(self, session_id: str | None) -> list[str]:
        if not session_id:
            return []
        with self._lock:
            memory = list(self._working_sets.get(session_id, ()))
        if memory:
            return memory
        try:
            persisted = self._get_store().session_working_set(
                session_id,
                limit=max(4, int(self.settings.get("session_working_set_files", 24))),
            )
        except Exception:
            persisted = []
        if persisted:
            with self._lock:
                self._working_sets[session_id] = deque(
                    persisted,
                    maxlen=max(4, int(self.settings.get("session_working_set_files", 24))),
                )
        return persisted

    def _recent_path_set(self) -> set[str]:
        result: set[str] = set()
        with self._lock:
            changes = list(self._recent_changes)
        for item in changes[-20:]:
            result.update(str(path) for path in item.get("paths", []))
        return result

    @staticmethod
    def _jaccard(left: str, right: str) -> float:
        left_tokens = set(ProjectContextEngine._tokens(left))
        right_tokens = set(ProjectContextEngine._tokens(right))
        if not left_tokens or not right_tokens:
            return 0.0
        return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)

    def _fallback_search(self, query: str, *, limit: int) -> list[ContextHit]:
        index = self._ensure()
        tokens = list(dict.fromkeys(self._tokens(query)))
        if not tokens:
            return []
        candidates: list[tuple[str, dict[str, Any], str, Counter[str], int]] = []
        document_frequency = Counter()
        total_length = 0
        for record in index.get("files", []):
            path = str(record.get("path", ""))
            for chunk in record.get("chunks", []):
                text = str(chunk.get("text", ""))
                counts = Counter(self._tokens(text))
                length = max(1, sum(counts.values()))
                present = {token for token in tokens if counts.get(token, 0)}
                if not present and not any(token in path.casefold() for token in tokens):
                    continue
                document_frequency.update(present)
                total_length += length
                candidates.append((path, chunk, text, counts, length))
        if not candidates:
            return []
        document_count = len(candidates)
        average_length = max(1.0, total_length / document_count)
        k1 = float(self.settings.get("bm25_k1", 1.2))
        b = float(self.settings.get("bm25_b", 0.75))
        scored: list[ContextHit] = []
        query_folded = query.casefold()
        for path, chunk, text, counts, length in candidates:
            path_folded = path.casefold()
            score = 0.0
            coverage = 0
            for token in tokens:
                tf = counts.get(token, 0)
                if tf:
                    coverage += 1
                    df = document_frequency.get(token, 0)
                    idf = math.log(1.0 + (document_count - df + 0.5) / (df + 0.5))
                    denominator = tf + k1 * (1.0 - b + b * length / average_length)
                    score += idf * (tf * (k1 + 1.0) / max(0.001, denominator))
                if token in path_folded:
                    score += 2.8
                    if token in Path(path).name.casefold():
                        score += 1.5
            if query_folded in text.casefold():
                score += 4.5
            score += coverage * 0.35
            if score > 0:
                scored.append(
                    ContextHit(
                        path=path,
                        line_start=int(chunk.get("line_start", 1)),
                        line_end=int(chunk.get("line_end", 1)),
                        score=score,
                        text=text,
                        reason="fallback-bm25",
                    )
                )
        scored.sort(key=lambda item: (-item.score, item.path.casefold(), item.line_start))
        return scored[: max(limit * 4, limit)]

    def search(self, query: str, *, limit: int | None = None, session_id: str | None = None) -> list[ContextHit]:
        query = " ".join(str(query).split())
        if not query:
            return []
        self._ensure()
        max_hits = min(20, max(1, int(limit or self.settings.get("retrieval_hits", 8))))
        raw: list[ContextHit] = []
        try:
            rows = self._get_store().search_chunks(query, limit=max(40, max_hits * 8))
            raw = [
                ContextHit(
                    path=str(item["path"]),
                    line_start=int(item["line_start"]),
                    line_end=int(item["line_end"]),
                    score=float(item.get("fts_score", 0.0)),
                    text=str(item["text"]),
                    reason="sqlite-fts5",
                )
                for item in rows
            ]
        except Exception:
            raw = []
        if not raw:
            raw = self._fallback_search(query, limit=max_hits)

        tokens = list(dict.fromkeys(self._tokens(query)))
        query_folded = query.casefold()
        working = set(self._working_set(session_id))
        recent = self._recent_path_set()
        reranked: list[ContextHit] = []
        for hit in raw:
            path_folded = hit.path.casefold()
            folded = hit.text.casefold()
            score = hit.score
            coverage = 0
            for token in tokens:
                if token in folded:
                    coverage += 1
                    score += 0.35
                if token in path_folded:
                    score += 3.0
                    if token in Path(hit.path).name.casefold():
                        score += 1.7
            if query_folded in folded:
                score += 5.0
            if hit.path in working:
                score += 2.4
            if hit.path in recent:
                score += 1.6
            if Path(hit.path).name.casefold() in _IMPORTANT_NAMES:
                score += 0.65
            score += coverage * 0.25
            reranked.append(
                ContextHit(hit.path, hit.line_start, hit.line_end, score, hit.text, hit.reason)
            )
        reranked.sort(key=lambda item: (-item.score, item.path.casefold(), item.line_start))

        selected: list[ContextHit] = []
        per_file = Counter()
        for hit in reranked:
            if per_file[hit.path] >= 2:
                continue
            redundancy = max((self._jaccard(hit.text, item.text) for item in selected), default=0.0)
            adjusted = hit.score - redundancy * float(self.settings.get("mmr_redundancy_penalty", 1.25))
            if adjusted <= 0:
                continue
            selected.append(
                ContextHit(hit.path, hit.line_start, hit.line_end, adjusted, hit.text, hit.reason)
            )
            per_file[hit.path] += 1
            if len(selected) >= max_hits:
                break
        return selected

    def search_symbols(self, query: str, *, limit: int = 8) -> list[dict[str, Any]]:
        self._ensure()
        try:
            rows = self._get_store().search_symbols(query, limit=max(10, limit * 4))
        except Exception:
            rows = []
        query_folded = query.casefold()
        for item in rows:
            score = float(item.get("fts_score", 0.0))
            name = str(item.get("name", ""))
            qualified = str(item.get("qualified_name", ""))
            if name.casefold() == query_folded or qualified.casefold() == query_folded:
                score += 8.0
            elif query_folded in name.casefold() or query_folded in qualified.casefold():
                score += 4.0
            item["score"] = round(score, 4)
            item.pop("rank", None)
        rows.sort(key=lambda item: (-float(item.get("score", 0.0)), str(item.get("path", "")), int(item.get("line_start", 0))))
        return rows[: max(1, min(20, int(limit)))]

    def impact(self, name_or_path: str, *, limit: int = 60) -> dict[str, Any]:
        self._ensure()
        try:
            return self._get_store().symbol_impact(name_or_path, limit=limit)
        except Exception as exc:
            return {"query": name_or_path, "definitions": [], "edges": [], "related_paths": [], "error": str(exc)}

    def record_tool_activity(self, session_id: str, call: ToolCall, result: ToolResult) -> None:
        paths: list[str] = []

        def collect(value: Any, key: str = "") -> None:
            if isinstance(value, dict):
                for child_key, child in value.items():
                    collect(child, str(child_key))
            elif isinstance(value, list):
                for child in value:
                    collect(child, key)
            elif isinstance(value, str) and key.casefold() in {
                "path", "paths", "directory", "cwd", "source_path", "target_path"
            }:
                normalized = value.replace("\\", "/").strip("/")
                if normalized and not Path(normalized).is_absolute():
                    paths.append(normalized)

        collect(call.arguments)
        collect(result.structured)
        max_files = max(4, int(self.settings.get("session_working_set_files", 24)))
        with self._lock:
            working = self._working_sets.setdefault(session_id, deque(maxlen=max_files))
            for path in paths:
                try:
                    working.remove(path)
                except ValueError:
                    pass
                working.appendleft(path)
            if len(self._working_sets) > 200:
                for stale in list(self._working_sets)[:50]:
                    if stale != session_id:
                        self._working_sets.pop(stale, None)
        if paths:
            try:
                self._get_store().touch_session_paths(session_id, paths, limit=max_files)
            except Exception:
                pass
        if call.name in {"write_file", "append_file", "replace_text", "create_directory"}:
            self.mark_dirty(paths=set(paths), reason=f"tool:{call.name}")
        elif call.name == "run_command":
            self.mark_dirty(reason="tool:run_command", force=True)

    def brief(self, *, max_chars: int | None = None) -> str:
        index = self._ensure()
        summary = index.get("summary", {})
        limit = max(1000, int(max_chars or self.settings.get("brief_max_chars", 6500)))
        languages = ", ".join(f"{name}:{count}" for name, count in summary.get("languages", [])[:8]) or "belirlenemedi"
        manifests = ", ".join(f"{item['path']} ({item['kind']})" for item in summary.get("manifests", [])[:12]) or "yok"
        directories = ", ".join(name for name, _ in summary.get("top_directories", [])[:10]) or "kök dizin"
        git = summary.get("git", {})
        git_text = "Git deposu değil"
        if git.get("repository"):
            git_text = f"branch={git.get('branch')}, head={git.get('head')}, dirty={git.get('dirty')}"
        watcher_backend = self._watcher.backend if self._watcher is not None else "periodic-verification"
        text = (
            f"Kök: {index.get('root')}\n"
            f"İndeks: {summary.get('file_count', 0)} metin dosyası, {summary.get('total_text_bytes', 0)} bayt, "
            f"sembol={summary.get('symbols', 0)}, ilişki={summary.get('edges', 0)}, "
            f"kaynak={index.get('source')}, truncated={summary.get('truncated', False)}\n"
            f"Diller: {languages}\n"
            f"Manifestler: {manifests}\n"
            f"Üst klasörler: {directories}\n"
            f"Proje talimat dosyaları: {', '.join(summary.get('instruction_files', [])) or 'yok'}\n"
            f"Git: {git_text}\n"
            f"Bağlam watcher: {watcher_backend}, generation={self._generation}, dirty={self._dirty}"
        )
        return text[:limit]

    def _foundation_context(self, index: dict[str, Any]) -> list[dict[str, Any]]:
        summary = index.get("summary", {})
        files = {str(item.get("path", "")): item for item in index.get("files", [])}
        candidates: list[tuple[str, str]] = []
        for item in summary.get("instruction_files", []):
            candidates.append((str(item), "project_instruction"))
        for path in sorted(files, key=str.casefold):
            name = Path(path).name.casefold()
            if len(Path(path).parts) == 1 and name.startswith("readme"):
                candidates.append((path, "root_readme"))
        for item in summary.get("manifests", []):
            candidates.append((str(item.get("path", "")), "project_manifest"))

        max_files = min(10, max(1, int(self.settings.get("foundation_files", 5))))
        max_chars = max(500, int(self.settings.get("foundation_context_max_chars", 5000)))
        selected: list[dict[str, Any]] = []
        seen: set[str] = set()
        used = 0
        for path, reason in candidates:
            if not path or path in seen or path not in files or len(selected) >= max_files:
                continue
            chunks = files[path].get("chunks", [])
            if not chunks:
                continue
            chunk = chunks[0]
            text = str(chunk.get("text", ""))
            remaining = max_chars - used
            if remaining <= 0:
                break
            if len(text) > remaining:
                text = text[:remaining] + "\n... <proje omurgası kırpıldı>"
            selected.append(
                {
                    "path": path,
                    "line_start": int(chunk.get("line_start", 1)),
                    "line_end": int(chunk.get("line_end", 1)),
                    "reason": reason,
                    "text": text,
                }
            )
            seen.add(path)
            used += len(text)
        return selected

    def _recent_change_payload(self, limit: int = 12) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._recent_changes)[-max(1, limit):]

    def prompt_context(self, user_prompt: str, *, session_id: str | None = None) -> dict[str, Any]:
        if not self.enabled or not self.workspace.active:
            return {
                "brief": "",
                "plan": {},
                "foundations": [],
                "hits": [],
                "symbols": [],
                "relations": [],
                "recent_changes": [],
                "working_set": [],
                "status": self.status(refresh=False),
            }
        index = self._ensure()
        try:
            live_git = self._git_snapshot(self.workspace.require_root())
            if live_git.get("repository"):
                index.setdefault("summary", {})["git"] = live_git
        except Exception:
            pass
        plan = self.plan_query(user_prompt)
        foundations = self._foundation_context(index)
        hits = self.search(user_prompt, limit=plan.retrieval_hits, session_id=session_id)
        symbols = self.search_symbols(user_prompt, limit=plan.symbol_hits)
        graph_seed_paths = [item.path for item in hits[:4]] + [str(item.get("path", "")) for item in symbols[:4]]
        relations: list[dict[str, Any]] = []
        if plan.graph_expansion and graph_seed_paths:
            try:
                relations = self._get_store().related_paths(
                    graph_seed_paths,
                    limit=max(2, int(self.settings.get("graph_expansion_hits", 8))),
                )
            except Exception:
                relations = []
        max_chars = max(2000, int(self.settings.get("automatic_context_max_chars", 15000)))
        used = sum(len(item["text"]) for item in foundations)
        foundation_ranges = {
            (item["path"], item["line_start"], item["line_end"]) for item in foundations
        }
        wire_hits: list[dict[str, Any]] = []
        for hit in hits:
            item = hit.to_wire()
            if (item["path"], item["line_start"], item["line_end"]) in foundation_ranges:
                continue
            remaining = max_chars - used
            if remaining <= 0:
                break
            if len(item["text"]) > remaining:
                item["text"] = item["text"][:remaining] + "\n... <bağlam kırpıldı>"
            used += len(item["text"])
            wire_hits.append(item)
        return {
            "brief": self.brief(),
            "plan": plan.to_wire(),
            "foundations": foundations,
            "hits": wire_hits,
            "symbols": symbols,
            "relations": relations,
            "recent_changes": self._recent_change_payload() if plan.include_recent_changes else [],
            "working_set": self._working_set(session_id),
            "status": self.status(refresh=False),
        }

    def health(self, *, integrity_check: bool = False) -> dict[str, Any]:
        status = self.status(refresh=True)
        try:
            status["store"] = self._get_store().health(check_integrity=integrity_check)
        except Exception as exc:
            status["store"] = {"error": str(exc)}
        return status

    def status(self, *, refresh: bool = True) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "indexed": False}
        if not self.workspace.active:
            return {"enabled": True, "indexed": False, "reason": "workspace_missing"}
        if refresh:
            try:
                self.refresh(force=False)
            except Exception as exc:
                return {"enabled": True, "indexed": False, "error": str(exc)}
        index = self._index or self._load_cache()
        if not index:
            return {
                "enabled": True,
                "indexed": False,
                "dirty": self._dirty,
                "generation": self._generation,
                "watcher": self._watcher.backend if self._watcher else "inactive",
                "watcher_error": self._watcher_error,
            }
        summary = dict(index.get("summary", {}))
        try:
            live_git = self._git_snapshot(self.workspace.require_root())
            if live_git.get("repository"):
                summary["git"] = live_git
        except Exception:
            pass
        store_health: dict[str, Any] = {}
        try:
            store_health = self._get_store().health()
        except Exception as exc:
            store_health = {"error": str(exc)}
        with self._lock:
            summary.update(
                {
                    "enabled": True,
                    "indexed": True,
                    "root": index.get("root"),
                    "generated_at": index.get("generated_at"),
                    "source": index.get("source"),
                    "dirty": self._dirty,
                    "generation": self._generation,
                    "pending_paths": len(self._pending_paths),
                    "watcher": self._watcher.backend if self._watcher else "periodic-verification",
                    "watcher_error": self._watcher_error,
                    "background_worker": bool(self._worker and self._worker.is_alive()),
                    "store": store_health,
                }
            )
        return summary
