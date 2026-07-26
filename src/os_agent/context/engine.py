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
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..errors import WorkspaceError

if TYPE_CHECKING:
    from ..tools.workspace import WorkspaceManager

_TOKEN_RE = re.compile(r"[\w.-]{2,}", re.UNICODE)
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


@dataclass(frozen=True, slots=True)
class ContextHit:
    path: str
    line_start: int
    line_end: int
    score: float
    text: str

    def to_wire(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "score": round(self.score, 4),
            "text": self.text,
        }


class ProjectContextEngine:
    """Ağ gerektirmeyen, artımlı ve sınırlı proje bağlam indeksi.

    İndeks yalnızca seçilmiş workspace içinde kalır. Dosya içerikleri model için
    güvenilmeyen veri olarak etiketlenir; bu sınıf hiçbir dosya talimatını sistem
    talimatı olarak yükseltmez.
    """

    SCHEMA_VERSION = 1

    def __init__(self, workspace: WorkspaceManager, cache_root: Path, settings: dict[str, Any]):
        self.workspace = workspace
        self.cache_root = cache_root
        self.settings = settings
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        self._dirty = True
        self._last_refresh_monotonic = 0.0
        self._index: dict[str, Any] | None = None
        self._activity_handler = None

    def set_activity_handler(self, handler) -> None:
        self._activity_handler = handler

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._activity_handler is not None:
            self._activity_handler(event_type, payload)

    @property
    def enabled(self) -> bool:
        return bool(self.settings.get("enabled", True))

    def mark_dirty(self) -> None:
        with self._lock:
            self._dirty = True

    def _workspace_key(self) -> str:
        root = str(self.workspace.require_root()).casefold().encode("utf-8", errors="replace")
        return hashlib.sha256(root).hexdigest()[:24]

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
        env = os.environ.copy()
        env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"})
        result: dict[str, Any] = {"repository": False, "branch": None, "head": None, "dirty": None}
        try:
            branch = subprocess.run(
                ["git", "branch", "--show-current"], cwd=root, env=env, text=True,
                encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=5, check=False,
            )
            head = subprocess.run(
                ["git", "rev-parse", "--short=12", "HEAD"], cwd=root, env=env, text=True,
                encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=5, check=False,
            )
            status = subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=no"], cwd=root, env=env, text=True,
                encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=7, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return result
        if head.returncode == 0:
            result.update(
                repository=True,
                branch=branch.stdout.strip() or "detached",
                head=head.stdout.strip(),
                dirty=bool(status.stdout.strip()) if status.returncode == 0 else None,
            )
        return result

    def refresh(self, *, force: bool = False) -> dict[str, Any]:
        # Web yenilemesi ve agent retrieval aynı anda tetiklense bile tek indeks üret.
        # Bu kilit disk taraması/cache replace işlemlerini process içinde serialize eder.
        with self._refresh_lock:
            return self._refresh_locked(force=force)

    def _refresh_locked(self, *, force: bool = False) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "indexed": False}
        root = self.workspace.require_root()
        min_interval = max(0.2, float(self.settings.get("refresh_min_interval_seconds", 3.0)))
        with self._lock:
            if not force and not self._dirty and self._index is not None and time.monotonic() - self._last_refresh_monotonic < min_interval:
                return self.status(refresh=False)

        started = time.monotonic()
        self._emit("context.index.started", {"root": str(root), "force": force})
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
                and isinstance(prior.get("chunks"), list)
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
                record = {
                    "path": relative,
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                    "language": _LANGUAGE_NAMES.get(target.suffix.casefold(), "Text"),
                    "chunks": self._chunks(text),
                }
            total_bytes += int(record.get("size", 0))
            records.append(record)

        languages = Counter(str(item.get("language", "Text")) for item in records)
        manifests: list[dict[str, str]] = []
        instructions: list[str] = []
        top_dirs = Counter()
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
                "git": self._git_snapshot(root),
            },
        }
        try:
            self._save_cache(payload)
        except OSError:
            pass
        with self._lock:
            self._index = payload
            self._dirty = False
            self._last_refresh_monotonic = time.monotonic()
        duration = int((time.monotonic() - started) * 1000)
        self._emit(
            "context.index.completed",
            {"root": str(root), "files": len(records), "reused": reused, "duration_ms": duration},
        )
        return self.status(refresh=False)

    def _ensure(self) -> dict[str, Any]:
        self.refresh(force=False)
        with self._lock:
            return self._index or {}

    @staticmethod
    def _tokens(text: str) -> list[str]:
        # snake_case, kebab-case ve camelCase sembollerini birlikte indeksle.
        expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
        expanded = expanded.replace("_", " ").replace("-", " ")
        return [item.casefold() for item in _TOKEN_RE.findall(expanded)]

    def search(self, query: str, *, limit: int | None = None) -> list[ContextHit]:
        """Path boost + BM25 tabanlı deterministik hybrid retrieval."""
        query = " ".join(str(query).split())
        if not query:
            return []
        index = self._ensure()
        tokens = list(dict.fromkeys(self._tokens(query)))
        if not tokens:
            return []
        max_hits = min(20, max(1, int(limit or self.settings.get("retrieval_hits", 6))))

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
            folded = text.casefold()
            if query_folded in folded:
                score += 4.5
            if Path(path).name.casefold() in _IMPORTANT_NAMES:
                score += 0.8
            score += coverage * 0.35
            if score <= 0:
                continue
            scored.append(
                ContextHit(
                    path=path,
                    line_start=int(chunk.get("line_start", 1)),
                    line_end=int(chunk.get("line_end", 1)),
                    score=score,
                    text=text,
                )
            )
        scored.sort(key=lambda item: (-item.score, item.path.casefold(), item.line_start))
        selected: list[ContextHit] = []
        per_file = Counter()
        for hit in scored:
            if per_file[hit.path] >= 2:
                continue
            selected.append(hit)
            per_file[hit.path] += 1
            if len(selected) >= max_hits:
                break
        return selected

    def brief(self, *, max_chars: int | None = None) -> str:
        index = self._ensure()
        summary = index.get("summary", {})
        limit = max(1000, int(max_chars or self.settings.get("brief_max_chars", 5000)))
        languages = ", ".join(f"{name}:{count}" for name, count in summary.get("languages", [])[:8]) or "belirlenemedi"
        manifests = ", ".join(f"{item['path']} ({item['kind']})" for item in summary.get("manifests", [])[:12]) or "yok"
        directories = ", ".join(name for name, _ in summary.get("top_directories", [])[:10]) or "kök dizin"
        git = summary.get("git", {})
        git_text = "Git deposu değil"
        if git.get("repository"):
            git_text = f"branch={git.get('branch')}, head={git.get('head')}, dirty={git.get('dirty')}"
        text = (
            f"Kök: {index.get('root')}\n"
            f"İndeks: {summary.get('file_count', 0)} metin dosyası, {summary.get('total_text_bytes', 0)} bayt, "
            f"kaynak={index.get('source')}, truncated={summary.get('truncated', False)}\n"
            f"Diller: {languages}\n"
            f"Manifestler: {manifests}\n"
            f"Üst klasörler: {directories}\n"
            f"Proje talimat dosyaları: {', '.join(summary.get('instruction_files', [])) or 'yok'}\n"
            f"Git: {git_text}"
        )
        return text[:limit]

    def _foundation_context(self, index: dict[str, Any]) -> list[dict[str, Any]]:
        """Her görevde gereken küçük proje omurgasını progressive biçimde seçer.

        Proje talimatları, kök README ve manifestlerin yalnızca ilk parçaları alınır.
        Bunlar da model açısından güvenilmeyen çalışma verisidir; amaç proje yapısını
        kaybetmeden semantic retrieval bütçesini korumaktır.
        """
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

        max_files = min(8, max(1, int(self.settings.get("foundation_files", 4))))
        max_chars = max(500, int(self.settings.get("foundation_context_max_chars", 3500)))
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

    def prompt_context(self, user_prompt: str) -> dict[str, Any]:
        if not self.enabled or not self.workspace.active:
            return {"brief": "", "foundations": [], "hits": [], "status": self.status(refresh=False)}
        index = self._ensure()
        foundations = self._foundation_context(index)
        hits = self.search(user_prompt, limit=int(self.settings.get("automatic_retrieval_hits", 5)))
        max_chars = max(1000, int(self.settings.get("automatic_context_max_chars", 9000)))
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
            "foundations": foundations,
            "hits": wire_hits,
            "status": self.status(refresh=False),
        }

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
            return {"enabled": True, "indexed": False, "dirty": self._dirty}
        summary = dict(index.get("summary", {}))
        summary.update(
            {
                "enabled": True,
                "indexed": True,
                "root": index.get("root"),
                "generated_at": index.get("generated_at"),
                "source": index.get("source"),
                "dirty": self._dirty,
            }
        )
        return summary
