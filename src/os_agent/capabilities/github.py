from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import tomllib
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse

from ..errors import CapabilityInstallError, CapabilityValidationError
from .adapters import AdapterRegistry, normalize_capability_name
from .jobs import CapabilityJobRuntime, JobWorkspace
from .models import CapabilityInspection, ProcessResult
from .process import CapabilityProcessRunner

_GITHUB_HOST = "github.com"
_SAFE_PART = re.compile(r"^[A-Za-z0-9_.-]+$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_BINARY_DENY = {".exe", ".dll", ".com", ".msi", ".scr", ".sys", ".pyd", ".so", ".dylib", ".class", ".jar", ".pyc"}
_RISK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("process_execution", re.compile(r"\b(?:subprocess\.|os\.system\s*\(|Popen\s*\()")),
    ("dynamic_execution", re.compile(r"\b(?:eval|exec)\s*\(")),
    ("network_access", re.compile(r"\b(?:requests\.|urllib\.|httpx\.|socket\.|aiohttp\.)")),
    ("credential_access", re.compile(r"\b(?:API_KEY|TOKEN|SECRET|PASSWORD|credentials|\.env)\b", re.I)),
    ("destructive_command", re.compile(r"\b(?:rm\s+-rf|git\s+reset\s+--hard|Remove-Item.+-Recurse|shutil\.rmtree)\b", re.I)),
)


def parse_github_repository(source: str, *, ref: str | None = None) -> dict[str, str]:
    raw = source.strip()
    if "://" not in raw and raw.count("/") == 1:
        raw = f"https://github.com/{raw}"
    if not raw or len(raw) > 2048 or any(ord(char) < 32 for char in raw):
        raise CapabilityInstallError("GitHub extension kaynağı boş, çok uzun veya kontrol karakteri içeriyor.")
    parsed = urlparse(raw)
    if parsed.scheme != "https" or parsed.hostname is None or parsed.hostname.casefold() != _GITHUB_HOST:
        raise CapabilityInstallError("Yalnızca https://github.com üzerindeki public repository kaynakları desteklenir.")
    if parsed.query or parsed.fragment or parsed.username or parsed.password or parsed.port:
        raise CapabilityInstallError("Kimlik bilgisi, özel port, query veya fragment içeren GitHub URL'si reddedildi.")
    parts = [unquote(item) for item in parsed.path.strip("/").split("/") if item]
    if len(parts) < 2:
        raise CapabilityInstallError("GitHub URL'si owner/repository içermeli.")
    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not _SAFE_PART.fullmatch(owner) or not _SAFE_PART.fullmatch(repo):
        raise CapabilityInstallError("Geçersiz GitHub owner/repository adı.")
    url_ref = parts[3] if len(parts) >= 4 and parts[2] in {"tree", "blob"} else None
    chosen_ref = (ref or url_ref or "HEAD").strip()
    if not chosen_ref or len(chosen_ref) > 256 or chosen_ref.startswith("-") or any(ord(c) < 32 for c in chosen_ref):
        raise CapabilityInstallError("Geçersiz veya güvenli olmayan GitHub ref değeri.")
    return {
        "owner": owner,
        "repo": repo,
        "clone_url": f"https://github.com/{owner}/{repo}.git",
        "web_url": f"https://github.com/{owner}/{repo}",
        "ref": chosen_ref,
    }


class GitHubCapabilityInspector:
    def __init__(
        self,
        quarantine_root: Path,
        settings: dict[str, Any],
        adapters: AdapterRegistry,
        *,
        runner: CapabilityProcessRunner | None = None,
        jobs: CapabilityJobRuntime | None = None,
    ):
        self.quarantine_root = quarantine_root
        self.settings = settings
        self.adapters = adapters
        self.runner = runner or CapabilityProcessRunner(settings)
        self.jobs = jobs or CapabilityJobRuntime(quarantine_root.parent, settings)
        located_git = shutil.which("git")
        if not located_git:
            raise CapabilityInstallError("GitHub capability incelemesi için git executable bulunamadı.")
        self.git_executable = str(Path(located_git).resolve())
        self.quarantine_root.mkdir(parents=True, exist_ok=True)
        self.cleanup()

    @staticmethod
    def _retryable_git_failure(result: ProcessResult) -> bool:
        text = f"{result.stdout}\n{result.stderr}".casefold()
        markers = (
            "could not resolve host", "connection reset", "connection timed out", "early eof",
            "rpc failed", "http 429", "http 500", "http 502", "http 503", "http 504",
            "tls", "ssl", "network is unreachable", "remote end hung up", "temporarily unavailable",
        )
        return result.timed_out or any(marker in text for marker in markers)

    def _git(
        self,
        command: list[str],
        *,
        job: JobWorkspace,
        cwd: Path | None = None,
        timeout: int | None = None,
        allow_network: bool = False,
        retries: int = 0,
        env_overrides: dict[str, str] | None = None,
    ) -> ProcessResult:
        actual = [
            self.git_executable,
            "-c", "credential.helper=",
            "-c", "core.symlinks=false",
            "-c", "advice.detachedHead=false",
            *command,
        ]
        clean_env = {
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": "",
            "GIT_OBJECT_DIRECTORY": "",
            "GIT_COMMON_DIR": "",
            "GIT_DIR": "",
            "GIT_WORK_TREE": "",
        }
        if env_overrides:
            clean_env.update(env_overrides)
        attempts = max(1, retries + 1)
        last: ProcessResult | None = None
        for attempt in range(attempts):
            last = self.runner.run(
                actual,
                cwd=(cwd or job.work),
                env_overrides=job.environment(clean_env),
                timeout_seconds=timeout or max(10, int(self.settings.get("git_timeout_seconds", 120))),
                allow_network=allow_network,
                memory_limit_mb=512,
            )
            if last.ok or attempt + 1 >= attempts or not self._retryable_git_failure(last):
                return last
            backoff = max(0.25, float(self.settings.get("git_retry_backoff_seconds", 2))) * (2 ** attempt)
            job.update("retrying", attempt=attempt + 1, command="git " + " ".join(command), backoff_seconds=backoff)
            time.sleep(backoff)
        assert last is not None
        return last

    def _resolve_ref(self, source: dict[str, str], job: JobWorkspace) -> str:
        ref = source["ref"]
        if _COMMIT.fullmatch(ref.casefold()):
            return ref.casefold()
        completed = self._git(
            ["ls-remote", source["clone_url"], "HEAD" if ref == "HEAD" else ref],
            job=job,
            timeout=60,
            allow_network=True,
            retries=max(1, int(self.settings.get("git_fetch_retries", 3)) - 1),
        )
        if completed.returncode != 0:
            raise CapabilityInstallError(completed.stderr.strip() or f"GitHub ref çözümlenemedi: {ref}")
        entries = []
        for line in completed.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and _COMMIT.fullmatch(parts[0].casefold()):
                entries.append((parts[0].casefold(), parts[1]))
        if not entries:
            raise CapabilityInstallError(f"GitHub ref bulunamadı: {ref}")
        preferred = [
            sha for sha, remote_ref in entries
            if remote_ref.endswith("^{}") or remote_ref == f"refs/heads/{ref}" or remote_ref == "HEAD"
        ]
        return (preferred or [sha for sha, _ in entries])[0]

    def _fetch(self, source: dict[str, str], commit: str, repo_root: Path, job: JobWorkspace) -> tuple[Path, bool]:
        cache = self.jobs.git_cache_path(source["clone_url"])
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache_hit = False
        with self.jobs.cache_lock(source["clone_url"]):
            if (cache / "HEAD").is_file() and not (cache / "objects").is_dir():
                shutil.rmtree(cache, ignore_errors=True)
            if not (cache / "HEAD").is_file():
                temporary = cache.with_name(cache.name + f".tmp-{uuid.uuid4().hex[:8]}")
                shutil.rmtree(temporary, ignore_errors=True)
                initialized = self._git(["init", "--bare", str(temporary)], job=job, allow_network=False)
                if not initialized.ok:
                    raise CapabilityInstallError(initialized.stderr.strip() or "Git cache oluşturulamadı.")
                os.replace(temporary, cache)
            remote = self._git(["--git-dir", str(cache), "remote", "get-url", "origin"], job=job)
            if remote.ok:
                configured = self._git(
                    ["--git-dir", str(cache), "remote", "set-url", "origin", source["clone_url"]], job=job
                )
            else:
                configured = self._git(
                    ["--git-dir", str(cache), "remote", "add", "origin", source["clone_url"]], job=job
                )
            if not configured.ok:
                raise CapabilityInstallError(configured.stderr.strip() or "Git cache remote ayarlanamadı.")

            present = self._git(
                ["--git-dir", str(cache), "cat-file", "-e", f"{commit}^{{commit}}"], job=job
            )
            cache_hit = present.ok
            if not cache_hit:
                maximum_blob = max(65_536, int(self.settings.get("max_repository_file_bytes", 16_777_216))) + 1
                fetched = self._git(
                    [
                        "--git-dir", str(cache), "fetch", "--progress", "--no-tags", "--depth", "1",
                        f"--filter=blob:limit={maximum_blob}", "origin",
                        f"+{commit}:refs/os-cache/{commit}",
                    ],
                    job=job,
                    timeout=max(60, int(self.settings.get("git_fetch_timeout_seconds", 600))),
                    allow_network=True,
                    retries=max(0, int(self.settings.get("git_fetch_retries", 3)) - 1),
                )
                if not fetched.ok:
                    detail = fetched.stderr.strip() or fetched.stdout.strip() or "GitHub repository indirilemedi."
                    raise CapabilityInstallError(detail[-5000:])
            self.jobs.touch_cache(cache)

        repo_root.mkdir(parents=True, exist_ok=False)
        initialized = self._git(["init", "--quiet"], job=job, cwd=repo_root, allow_network=False)
        if not initialized.ok:
            raise CapabilityInstallError(initialized.stderr.strip() or "Git çalışma repository'si oluşturulamadı.")
        alternates = repo_root / ".git" / "objects" / "info" / "alternates"
        alternates.parent.mkdir(parents=True, exist_ok=True)
        object_directory = str((cache / "objects").resolve())
        # Git alternates satırı Windows'ta CRLF ile yazılırsa sondaki \r,
        # object directory adının parçası kabul edilir ve "objects?" hatası oluşur.
        alternates.write_bytes(object_directory.encode("utf-8", errors="strict") + b"\n")
        return cache, cache_hit

    def _preflight_tree(self, cache: Path, commit: str, job: JobWorkspace) -> dict[str, int]:
        completed = self._git(
            ["--git-dir", str(cache), "ls-tree", "-r", "-l", commit],
            job=job,
            timeout=90,
            env_overrides={"GIT_NO_LAZY_FETCH": "1"},
        )
        if completed.returncode != 0:
            raise CapabilityInstallError(completed.stderr.strip() or "Repository ağacı okunamadı.")
        max_files = max(50, int(self.settings.get("max_repository_files", 6000)))
        max_total = max(1_048_576, int(self.settings.get("max_repository_bytes", 100_663_296)))
        max_single = max(65_536, int(self.settings.get("max_repository_file_bytes", 16_777_216)))
        count = 0
        total = 0
        for line in completed.stdout.splitlines():
            match = re.match(r"^(\d{6})\s+(\w+)\s+([0-9a-f]{40})\s+(-|\d+)\t(.+)$", line)
            if not match:
                continue
            mode, object_type, _, size_raw, path = match.groups()
            if mode in {"120000", "160000"} or object_type != "blob":
                raise CapabilityValidationError(f"Symlink veya submodule içeren executable repository reddedildi: {path}")
            if size_raw == "-":
                raise CapabilityValidationError(
                    f"Repository blob filtresi sınırını aşan veya eksik dosya içeriyor: {path}"
                )
            size = int(size_raw)
            if size > max_single:
                raise CapabilityValidationError(f"Repository dosyası çok büyük: {path} ({size} bayt)")
            if PurePosixPath(path).suffix.casefold() in _BINARY_DENY:
                raise CapabilityValidationError(f"Kaynak repository derlenmiş binary içeriyor: {path}")
            count += 1
            total += size
            if count > max_files or total > max_total:
                raise CapabilityValidationError(
                    f"Repository capability sınırlarını aşıyor: files={count}/{max_files}, bytes={total}/{max_total}"
                )
        return {"file_count": count, "total_bytes": total}

    def _checkout(self, repo_root: Path, commit: str, job: JobWorkspace) -> None:
        self._git(["remote", "remove", "origin"], job=job, cwd=repo_root)
        completed = self._git(
            ["checkout", "--quiet", "--detach", commit],
            job=job,
            cwd=repo_root,
            env_overrides={"GIT_NO_LAZY_FETCH": "1"},
        )
        if completed.returncode != 0:
            raise CapabilityInstallError(
                completed.stderr.strip()
                or "Capability checkout eksik blob nedeniyle tamamlanamadı; cache güvenli biçimde iptal edildi."
            )
        shutil.rmtree(repo_root / ".git", ignore_errors=True)

    @staticmethod
    def _read_package(repo_root: Path) -> dict[str, Any]:
        pyproject = repo_root / "pyproject.toml"
        if not pyproject.is_file():
            raise CapabilityValidationError(
                "Repository root SKILL.md içermiyor ve desteklenen bir Python package manifestosu da bulunamadı."
            )
        try:
            payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise CapabilityValidationError(f"pyproject.toml okunamadı: {exc}") from exc
        project = payload.get("project")
        if not isinstance(project, dict):
            raise CapabilityValidationError("pyproject.toml içinde [project] tablosu gerekli.")
        package_name = str(project.get("name", "")).strip()
        version = str(project.get("version", "")).strip() or "unknown"
        scripts = project.get("scripts", {})
        if not package_name or not isinstance(scripts, dict) or not scripts:
            raise CapabilityValidationError("Executable capability için package adı ve [project.scripts] gerekli.")
        module = ""
        normalized_scripts: dict[str, str] = {}
        for name, target in scripts.items():
            if isinstance(name, str) and isinstance(target, str):
                normalized_scripts[name] = target
                if not module and ":" in target:
                    module = target.split(":", 1)[0].split(".", 1)[0]
        if not module:
            module = package_name.replace("-", "_")
        license_value = project.get("license")
        if isinstance(license_value, dict):
            license_value = license_value.get("text") or license_value.get("file")
        return {
            "name": package_name,
            "version": version,
            "description": str(project.get("description", "")),
            "requires_python": str(project.get("requires-python", "")),
            "license": str(license_value or ""),
            "scripts": normalized_scripts,
            "module": module,
            "dependencies": [str(item) for item in project.get("dependencies", []) if isinstance(item, str)],
        }

    def _scan(self, repo_root: Path) -> dict[str, Any]:
        max_hash_files = max(100, int(self.settings.get("max_hash_files", 6000)))
        max_scan_bytes = max(1_048_576, int(self.settings.get("max_static_scan_bytes", 33_554_432)))
        hashes: dict[str, str] = {}
        findings: list[dict[str, str]] = []
        scanned = 0
        for path in sorted(repo_root.rglob("*"), key=lambda item: item.as_posix().casefold()):
            if ".git" in path.parts or not path.is_file():
                continue
            if path.is_symlink():
                raise CapabilityValidationError(f"Checkout sonrasında symlink bulundu: {path}")
            relative = path.relative_to(repo_root).as_posix()
            data = path.read_bytes()
            hashes[relative] = hashlib.sha256(data).hexdigest()
            if len(hashes) > max_hash_files:
                raise CapabilityValidationError("Repository hash manifestosu dosya sınırını aşıyor.")
            if scanned < max_scan_bytes and b"\0" not in data[:4096]:
                text = data[:262144].decode("utf-8", errors="replace")
                scanned += min(len(data), 262144)
                for risk, pattern in _RISK_PATTERNS:
                    if pattern.search(text):
                        findings.append({"type": risk, "path": relative})
        return {"file_hashes": hashes, "findings": findings[:200], "scanned_bytes": scanned}

    def inspect(self, source_value: str, *, ref: str | None = None) -> CapabilityInspection:
        source = parse_github_repository(source_value, ref=ref)
        inspection_id = uuid.uuid4().hex
        root = self.quarantine_root / inspection_id
        repo_root = root / "repo"
        try:
            with self.jobs.job(
                "github-inspection",
                {"source": source["web_url"], "ref": source["ref"], "inspection_id": inspection_id},
            ) as job:
                job.update("running", phase="resolve-ref")
                commit = self._resolve_ref(source, job)
                temporary_repo = job.work / "repo"
                job.update("running", phase="fetch", commit=commit)
                cache, cache_hit = self._fetch(source, commit, temporary_repo, job)
                job.update("running", phase="tree-preflight", cache_hit=cache_hit)
                tree = self._preflight_tree(cache, commit, job)
                job.update("running", phase="checkout")
                self._checkout(temporary_repo, commit, job)
                job.update("running", phase="manifest-and-risk-scan")
                package = self._read_package(temporary_repo)
                adapter = self.adapters.detect(source, package)
                scan = self._scan(temporary_repo)
                root.mkdir(parents=True, exist_ok=False)
                os.replace(temporary_repo, repo_root)
            name = adapter.descriptor.name if adapter else normalize_capability_name(package["name"])
            source_payload = {**source, "commit": commit}
            report = {
                "classification": "trusted_capability" if adapter and adapter.descriptor.trusted else "python_cli_extension",
                "name": name,
                "source": source_payload,
                "package": package,
                "adapter": adapter.descriptor.name if adapter else None,
                "trusted_adapter": bool(adapter and adapter.descriptor.trusted),
                "default_auto_start": bool(adapter and adapter.descriptor.default_auto_start),
                "default_auto_query": bool(adapter and adapter.descriptor.default_auto_query),
                "download": {
                    "cache_hit": cache_hit,
                    "cache_root": str(cache),
                    "strategy": "commit-pinned bounded-blob bare-cache",
                    "ephemeral_job": True,
                },
                "tree": tree,
                "risk": {
                    "findings": scan["findings"],
                    "credential_environment_removed": True,
                    "runtime_network_default": "blocked_by_proxy_environment",
                    "project_directory_untouched": True,
                    "full_kernel_sandbox": False,
                },
                "file_hashes": scan["file_hashes"],
            }
            metadata = {
                "inspection_id": inspection_id,
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "source": source_payload,
                "package": package,
                "adapter": adapter.descriptor.name if adapter else None,
                "report": report,
            }
            root.mkdir(parents=True, exist_ok=True)
            (root / "inspection.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            return CapabilityInspection(
                inspection_id=inspection_id,
                root=root,
                source_root=repo_root,
                source=source_payload,
                package=package,
                adapter=adapter.descriptor.name if adapter else None,
                report=report,
                created_at=metadata["created_at"],
            )
        except Exception:
            shutil.rmtree(root, ignore_errors=True)
            raise

    def load(self, inspection_id: str) -> CapabilityInspection:
        if not re.fullmatch(r"[0-9a-f]{32}", inspection_id):
            raise CapabilityInstallError("Geçersiz capability inspection_id.")
        root = self.quarantine_root / inspection_id
        metadata_path = root / "inspection.json"
        if not metadata_path.is_file():
            raise CapabilityInstallError("Capability incelemesi bulunamadı veya süresi doldu.")
        ttl = max(300, int(self.settings.get("inspection_ttl_seconds", 3600)))
        if time.time() - metadata_path.stat().st_mtime > ttl:
            shutil.rmtree(root, ignore_errors=True)
            raise CapabilityInstallError("Capability incelemesinin süresi doldu; yeniden incele.")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CapabilityInstallError("Capability inceleme kaydı bozuk.") from exc
        return CapabilityInspection(
            inspection_id=inspection_id,
            root=root,
            source_root=root / "repo",
            source=dict(metadata.get("source", {})),
            package=dict(metadata.get("package", {})),
            adapter=str(metadata["adapter"]) if metadata.get("adapter") else None,
            report=dict(metadata.get("report", {})),
            created_at=str(metadata.get("created_at", "")),
        )

    @staticmethod
    def verify(inspection: CapabilityInspection) -> None:
        expected = {str(k): str(v) for k, v in dict(inspection.report.get("file_hashes", {})).items()}
        actual: dict[str, str] = {}
        for path in sorted(inspection.source_root.rglob("*"), key=lambda item: item.as_posix().casefold()):
            if ".git" in path.parts or not path.is_file():
                continue
            if path.is_symlink():
                raise CapabilityInstallError("Karantina repository'si incelemeden sonra symlink ile değiştirildi.")
            actual[path.relative_to(inspection.source_root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise CapabilityInstallError("Karantina repository içeriği incelemeden sonra değişti; kurulum iptal edildi.")

    def cleanup(self) -> None:
        ttl = max(300, int(self.settings.get("inspection_ttl_seconds", 3600)))
        now = time.time()
        if not self.quarantine_root.exists():
            return
        for path in self.quarantine_root.iterdir():
            try:
                if path.is_dir() and now - path.stat().st_mtime > ttl:
                    shutil.rmtree(path, ignore_errors=True)
            except OSError:
                continue
