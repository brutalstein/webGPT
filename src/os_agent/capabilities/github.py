from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import tomllib
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse

from ..errors import CapabilityInstallError, CapabilityValidationError
from .adapters import AdapterRegistry, normalize_capability_name
from .models import CapabilityInspection

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
    def __init__(self, quarantine_root: Path, settings: dict[str, Any], adapters: AdapterRegistry):
        self.quarantine_root = quarantine_root
        self.settings = settings
        self.adapters = adapters
        self.quarantine_root.mkdir(parents=True, exist_ok=True)
        self.cleanup()

    def _git(self, command: list[str], *, cwd: Path | None = None, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
            "WINDIR": os.environ.get("WINDIR", ""),
            "TEMP": os.environ.get("TEMP", ""),
            "TMP": os.environ.get("TMP", ""),
            "HOME": os.environ.get("HOME", ""),
            "USERPROFILE": os.environ.get("USERPROFILE", ""),
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": "",
            "GIT_CONFIG_NOSYSTEM": "1",
        }
        actual = ["git", "-c", "credential.helper=", "-c", "core.symlinks=false", *command]
        try:
            return subprocess.run(
                actual,
                cwd=str(cwd) if cwd else None,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout or max(10, int(self.settings.get("git_timeout_seconds", 120))),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CapabilityInstallError("GitHub capability kaynağı zaman aşımına uğradı.") from exc
        except OSError as exc:
            raise CapabilityInstallError(f"git çalıştırılamadı: {exc}") from exc

    def _resolve_ref(self, source: dict[str, str]) -> str:
        ref = source["ref"]
        if _COMMIT.fullmatch(ref.casefold()):
            return ref.casefold()
        completed = self._git(["ls-remote", source["clone_url"], "HEAD" if ref == "HEAD" else ref], timeout=45)
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

    def _fetch(self, source: dict[str, str], commit: str, repo_root: Path) -> None:
        repo_root.mkdir(parents=True, exist_ok=False)
        commands = (
            ["init", "--quiet"],
            ["remote", "add", "origin", source["clone_url"]],
            ["fetch", "--quiet", "--filter=blob:none", "--depth", "1", "origin", commit],
        )
        for command in commands:
            completed = self._git(command, cwd=repo_root)
            if completed.returncode != 0:
                raise CapabilityInstallError(completed.stderr.strip() or "GitHub repository indirilemedi.")

    def _preflight_tree(self, repo_root: Path) -> dict[str, int]:
        completed = self._git(["ls-tree", "-r", "-l", "FETCH_HEAD"], cwd=repo_root, timeout=60)
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
            size = 0 if size_raw == "-" else int(size_raw)
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

    def _checkout(self, repo_root: Path) -> None:
        completed = self._git(["checkout", "--quiet", "--detach", "FETCH_HEAD"], cwd=repo_root)
        if completed.returncode != 0:
            raise CapabilityInstallError(completed.stderr.strip() or "Capability checkout başarısız.")

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
        commit = self._resolve_ref(source)
        inspection_id = uuid.uuid4().hex
        root = self.quarantine_root / inspection_id
        repo_root = root / "repo"
        try:
            self._fetch(source, commit, repo_root)
            tree = self._preflight_tree(repo_root)
            self._checkout(repo_root)
            package = self._read_package(repo_root)
            adapter = self.adapters.detect(source, package)
            scan = self._scan(repo_root)
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
                "tree": tree,
                "risk": {
                    "findings": scan["findings"],
                    "credential_environment_removed": True,
                    "runtime_network_default": "blocked_by_proxy_environment",
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
