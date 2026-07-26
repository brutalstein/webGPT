from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from ..errors import CapabilityExecutionError


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(slots=True)
class JobWorkspace:
    """Tek bir dış kaynak/kurulum/çalıştırma işleminin izole dosya alanı."""

    job_id: str
    kind: str
    root: Path
    work: Path
    home: Path
    temp: Path
    logs: Path
    artifacts: Path
    cache_root: Path
    manifest: Path

    def environment(self, overrides: Mapping[str, str] | None = None) -> dict[str, str]:
        pycache = self.temp / "pycache"
        pycache.mkdir(parents=True, exist_ok=True)
        git_config = self.home / ".gitconfig"
        git_config.touch(exist_ok=True)
        values = {
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "TEMP": str(self.temp),
            "TMP": str(self.temp),
            "TMPDIR": str(self.temp),
            "XDG_CACHE_HOME": str(self.cache_root / "xdg"),
            "PIP_CACHE_DIR": str(self.cache_root / "pip"),
            "UV_CACHE_DIR": str(self.cache_root / "uv"),
            "UV_NO_CONFIG": "1",
            "UV_PYTHON_DOWNLOADS": "never",
            "UV_NO_MANAGED_PYTHON": "1",
            "UV_NO_PROGRESS": "1",
            "UV_SYSTEM_CERTS": "true",
            "UV_VENV_RELOCATABLE": "1",
            "PYTHONPYCACHEPREFIX": str(pycache),
            "GIT_CONFIG_GLOBAL": str(self.home / ".gitconfig"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": "",
        }
        for path in (self.cache_root / "xdg", self.cache_root / "pip", self.cache_root / "uv"):
            path.mkdir(parents=True, exist_ok=True)
        for key, value in dict(overrides or {}).items():
            values[str(key)] = str(value)
        return values

    def update(self, status: str, **fields: Any) -> None:
        payload: dict[str, Any] = {}
        try:
            if self.manifest.is_file():
                payload = json.loads(self.manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        payload.update(fields)
        payload.update({"job_id": self.job_id, "kind": self.kind, "status": status, "updated_at": _utc_now()})
        temporary = self.manifest.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.manifest)


class CapabilityJobRuntime:
    """Capability işlemleri için ephemeral workspace, cache ve process ortamı yöneticisi.

    Job alanları proje klasörünün dışında tutulur. Başarılı işler varsayılan olarak
    kaldırılır; başarısız işler bounded TTL boyunca tanılama amacıyla saklanır.
    """

    def __init__(self, extension_root: Path, settings: dict[str, Any]):
        self.extension_root = extension_root
        self.settings = settings
        self.jobs_root = extension_root / "jobs"
        self.cache_root = extension_root / "cache"
        self.git_cache_root = self.cache_root / "git"
        self.lock_root = self.cache_root / "locks"
        for path in (self.jobs_root, self.cache_root, self.git_cache_root, self.lock_root):
            path.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._active: dict[str, JobWorkspace] = {}
        self.cleanup()

    def create(self, kind: str, metadata: Mapping[str, Any] | None = None) -> JobWorkspace:
        safe_kind = "".join(char if char.isalnum() or char in "-_" else "-" for char in kind.casefold()).strip("-")
        safe_kind = safe_kind or "job"
        job_id = f"{safe_kind}-{uuid.uuid4().hex[:16]}"
        root = self.jobs_root / job_id
        root.mkdir(parents=False, exist_ok=False)
        try:
            os.chmod(root, 0o700)
        except OSError:
            pass
        work = root / "work"
        home = root / "home"
        temp = root / "tmp"
        logs = root / "logs"
        artifacts = root / "artifacts"
        for path in (work, home, temp, logs, artifacts):
            path.mkdir()
        job = JobWorkspace(
            job_id=job_id,
            kind=safe_kind,
            root=root,
            work=work,
            home=home,
            temp=temp,
            logs=logs,
            artifacts=artifacts,
            cache_root=self.cache_root,
            manifest=root / "job.json",
        )
        job.update("running", created_at=_utc_now(), metadata=dict(metadata or {}), pid=os.getpid())
        with self._lock:
            self._active[job_id] = job
        return job

    def complete(self, job: JobWorkspace, **fields: Any) -> None:
        job.update("completed", completed_at=_utc_now(), **fields)
        with self._lock:
            self._active.pop(job.job_id, None)
        if bool(self.settings.get("job_success_cleanup", True)):
            shutil.rmtree(job.root, ignore_errors=True)

    def fail(self, job: JobWorkspace, error: BaseException) -> None:
        try:
            job.update(
                "failed",
                failed_at=_utc_now(),
                error=str(error)[-8000:],
                error_type=type(error).__name__,
            )
        finally:
            with self._lock:
                self._active.pop(job.job_id, None)

    @contextmanager
    def job(self, kind: str, metadata: Mapping[str, Any] | None = None) -> Iterator[JobWorkspace]:
        workspace = self.create(kind, metadata)
        try:
            yield workspace
        except BaseException as exc:
            self.fail(workspace, exc)
            raise
        else:
            self.complete(workspace)

    def git_cache_path(self, clone_url: str) -> Path:
        import hashlib

        key = hashlib.sha256(clone_url.casefold().encode("utf-8", errors="replace")).hexdigest()
        return self.git_cache_root / f"{key}.git"

    @contextmanager
    def cache_lock(self, key: str) -> Iterator[None]:
        import hashlib

        digest = hashlib.sha256(key.encode("utf-8", errors="replace")).hexdigest()
        lock_path = self.lock_root / f"{digest}.lock"
        timeout = max(10.0, float(self.settings.get("job_lock_timeout_seconds", 180)))
        stale = max(timeout, float(self.settings.get("job_lock_stale_seconds", 1800)))
        started = time.monotonic()
        descriptor: int | None = None
        while descriptor is None:
            try:
                descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.write(descriptor, f"pid={os.getpid()} created={_utc_now()}\n".encode("utf-8"))
            except FileExistsError:
                try:
                    if time.time() - lock_path.stat().st_mtime > stale:
                        lock_path.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                if time.monotonic() - started >= timeout:
                    raise CapabilityExecutionError(f"Capability cache kilidi zaman aşımına uğradı: {key}")
                time.sleep(0.1)
        try:
            yield
        finally:
            try:
                if descriptor is not None:
                    os.close(descriptor)
            finally:
                lock_path.unlink(missing_ok=True)

    def touch_cache(self, path: Path) -> None:
        marker = path / ".os-cache-used" if path.is_dir() else path
        try:
            marker.touch(exist_ok=True)
        except OSError:
            pass

    def cleanup(self) -> None:
        now = time.time()
        retention = max(300, int(self.settings.get("job_retention_seconds", 21600)))
        with self._lock:
            active = set(self._active)
        for path in self.jobs_root.iterdir():
            if not path.is_dir() or path.name in active:
                continue
            try:
                if now - path.stat().st_mtime > retention:
                    shutil.rmtree(path, ignore_errors=True)
            except OSError:
                continue

        ttl = max(3600, int(self.settings.get("git_cache_ttl_seconds", 2_592_000)))
        maximum = max(1, int(self.settings.get("git_cache_max_repositories", 32)))
        caches = sorted(
            [path for path in self.git_cache_root.iterdir() if path.is_dir()],
            key=lambda item: (item / ".os-cache-used").stat().st_mtime
            if (item / ".os-cache-used").exists()
            else item.stat().st_mtime,
            reverse=True,
        )
        for index, path in enumerate(caches):
            marker = path / ".os-cache-used"
            try:
                modified = marker.stat().st_mtime if marker.exists() else path.stat().st_mtime
                if index >= maximum or now - modified > ttl:
                    shutil.rmtree(path, ignore_errors=True)
            except OSError:
                continue

    @staticmethod
    def _manifest_snapshot(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads((path / "job.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {"job_id": path.name, "status": "unknown"}
        payload["root"] = str(path)
        return payload

    def status(self) -> dict[str, Any]:
        with self._lock:
            active_ids = set(self._active)
            active = [self._manifest_snapshot(job.root) for job in self._active.values()]
        retained_items: list[dict[str, Any]] = []
        try:
            candidates = sorted(
                [item for item in self.jobs_root.iterdir() if item.is_dir() and item.name not in active_ids],
                key=lambda item: item.stat().st_mtime_ns,
                reverse=True,
            )
            limit = max(1, int(self.settings.get("job_retained_status_limit", 10)))
            retained_items = [self._manifest_snapshot(item) for item in candidates[:limit]]
        except OSError:
            retained_items = []
        try:
            cache_count = sum(1 for item in self.git_cache_root.iterdir() if item.is_dir())
        except OSError:
            cache_count = 0
        return {
            "jobs_root": str(self.jobs_root),
            "cache_root": str(self.cache_root),
            "active_count": len(active),
            "active": active,
            "retained_failed_jobs": sum(1 for item in retained_items if item.get("status") == "failed"),
            "retained": retained_items,
            "git_cache_repositories": cache_count,
        }
