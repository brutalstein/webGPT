from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse

from ..errors import SkillInstallError, SkillValidationError
from .models import SkillInspection
from .parser import parse_skill_directory

_GITHUB_HOST = "github.com"
_REPO_PART = re.compile(r"^[A-Za-z0-9_.-]+$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_BINARY_DENY = {".exe", ".dll", ".com", ".msi", ".scr", ".sys", ".pyd", ".so", ".dylib", ".class", ".jar", ".pyc"}
_SCRIPT_SUFFIXES = {".py", ".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd", ".js", ".ts", ".rb", ".php"}
_RISK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("shell_execution", re.compile(r"\b(?:subprocess\.|os\.system\s*\(|child_process|powershell|cmd\.exe|bash\s+-c)")),
    ("dynamic_execution", re.compile(r"\b(?:eval|exec)\s*\(")),
    ("network_download", re.compile(r"\b(?:curl|wget|Invoke-WebRequest|requests\.(?:get|post)|urllib\.request)\b", re.I)),
    ("credential_access", re.compile(r"\b(?:OPENAI_API_KEY|GITHUB_TOKEN|AWS_SECRET|credentials|id_rsa|\.env)\b", re.I)),
    ("destructive_command", re.compile(r"\b(?:rm\s+-rf|git\s+reset\s+--hard|git\s+clean\s+-f|Remove-Item.+-Recurse|del\s+/s)\b", re.I)),
)


def parse_github_source(source: str, *, ref: str | None = None, skill_path: str | None = None) -> dict[str, str]:
    raw = source.strip()
    if not raw:
        raise SkillInstallError("GitHub skill kaynağı boş olamaz.")
    if len(raw) > 2048 or any(ord(char) < 32 for char in raw):
        raise SkillInstallError("GitHub skill kaynağı çok uzun veya kontrol karakteri içeriyor.")
    if "://" not in raw and raw.count("/") == 1:
        raw = f"https://github.com/{raw}"
    parsed = urlparse(raw)
    if parsed.scheme != "https" or parsed.hostname is None or parsed.hostname.casefold() != _GITHUB_HOST:
        raise SkillInstallError("Yalnızca https://github.com üzerindeki public repository kaynakları desteklenir.")
    if parsed.query or parsed.fragment:
        raise SkillInstallError("Query veya fragment içeren GitHub skill URL'si desteklenmez.")
    if parsed.username or parsed.password or parsed.port:
        raise SkillInstallError("Kimlik bilgisi veya özel port içeren GitHub URL'si reddedildi.")
    parts = [unquote(item) for item in parsed.path.strip("/").split("/") if item]
    if len(parts) < 2:
        raise SkillInstallError("GitHub URL'si owner/repository içermeli.")
    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not _REPO_PART.fullmatch(owner) or not _REPO_PART.fullmatch(repo):
        raise SkillInstallError("Geçersiz GitHub owner/repository adı.")
    url_ref = None
    url_path = None
    if len(parts) >= 4 and parts[2] in {"tree", "blob"}:
        url_ref = parts[3]
        if len(parts) > 4:
            url_path = "/".join(parts[4:])
            if parts[2] == "blob" and PurePosixPath(url_path).name.casefold() == "skill.md":
                parent = str(PurePosixPath(url_path).parent)
                url_path = "" if parent == "." else parent
    chosen_ref = (ref or url_ref or "HEAD").strip()
    chosen_path = (skill_path or url_path or "").strip().strip("/")
    if (
        not chosen_ref
        or len(chosen_ref) > 256
        or chosen_ref.startswith("-")
        or any(ord(char) < 32 for char in chosen_ref)
    ):
        raise SkillInstallError("Geçersiz veya güvenli olmayan GitHub ref değeri.")
    if len(chosen_path) > 1024 or any(ord(char) < 32 for char in chosen_path):
        raise SkillInstallError("Geçersiz veya çok uzun skill_path değeri.")
    path_parts = PurePosixPath(chosen_path).parts
    if ".." in path_parts or ".git" in {part.casefold() for part in path_parts}:
        raise SkillInstallError("skill_path güvenli skill kökü dışında kalamaz.")
    return {
        "owner": owner,
        "repo": repo,
        "clone_url": f"https://github.com/{owner}/{repo}.git",
        "web_url": f"https://github.com/{owner}/{repo}",
        "ref": chosen_ref,
        "skill_path": chosen_path,
    }


class GitHubSkillInspector:
    def __init__(self, quarantine_root: Path, settings: dict[str, Any]):
        self.quarantine_root = quarantine_root
        self.settings = settings
        self.quarantine_root.mkdir(parents=True, exist_ok=True)

    def _git(self, command: list[str], *, cwd: Path | None = None, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            {
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_ASKPASS": "",
                "GIT_CONFIG_NOSYSTEM": "1",
            }
        )
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
                timeout=timeout or max(10, int(self.settings.get("git_timeout_seconds", 90))),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise SkillInstallError("GitHub kaynağı zaman aşımına uğradı.") from exc
        except OSError as exc:
            raise SkillInstallError(f"git çalıştırılamadı: {exc}") from exc

    def _resolve_ref(self, source: dict[str, str]) -> str:
        ref = source["ref"]
        if _COMMIT.fullmatch(ref.casefold()):
            return ref.casefold()
        target = "HEAD" if ref == "HEAD" else ref
        completed = self._git(["ls-remote", source["clone_url"], target], timeout=30)
        if completed.returncode != 0:
            raise SkillInstallError(completed.stderr.strip() or f"GitHub ref çözümlenemedi: {ref}")
        entries = [line.split() for line in completed.stdout.splitlines() if line.strip()]
        entries = [
            (parts[0].casefold(), parts[1])
            for parts in entries
            if len(parts) >= 2 and _COMMIT.fullmatch(parts[0].casefold())
        ]
        if not entries:
            raise SkillInstallError(f"GitHub ref bulunamadı: {ref}")
        preferred = [
            sha for sha, remote_ref in entries
            if remote_ref.endswith("^{}")
            or remote_ref == f"refs/heads/{ref}"
            or remote_ref == "HEAD"
        ]
        return (preferred or [sha for sha, _ in entries])[0]

    def _fetch_commit(self, source: dict[str, str], commit: str, target: Path) -> None:
        target.mkdir(parents=True, exist_ok=False)
        for command in (
            ["init", "--quiet"],
            ["remote", "add", "origin", source["clone_url"]],
            ["fetch", "--quiet", "--filter=blob:none", "--depth", "1", "origin", commit],
        ):
            completed = self._git(command, cwd=target)
            if completed.returncode != 0:
                raise SkillInstallError(completed.stderr.strip() or "GitHub repository indirilemedi.")

    def _discover_skill_path(self, repo_root: Path, requested: str) -> str:
        completed = self._git(["ls-tree", "-r", "--name-only", "FETCH_HEAD"], cwd=repo_root, timeout=30)
        if completed.returncode != 0:
            raise SkillInstallError(completed.stderr.strip() or "Repository ağacı okunamadı.")
        names = [line.strip().replace("\\", "/") for line in completed.stdout.splitlines() if line.strip()]
        if requested:
            normalized = requested.strip("/")
            expected = f"{normalized}/SKILL.md" if normalized not in {"", "."} else "SKILL.md"
            if expected not in names:
                raise SkillValidationError(f"Seçilen GitHub yolunda SKILL.md bulunamadı: {requested}")
            return normalized or "."
        candidates = sorted(
            {str(PurePosixPath(name).parent) if str(PurePosixPath(name).parent) != "." else "." for name in names if PurePosixPath(name).name == "SKILL.md"},
            key=str.casefold,
        )
        if len(candidates) == 1:
            return candidates[0]
        hint = ", ".join(candidates[:16])
        if not candidates:
            raise SkillValidationError("Repository içinde SKILL.md bulunamadı.")
        raise SkillValidationError(
            "Repository birden fazla skill içeriyor; skill_path belirt. "
            f"Adaylar: {hint}"
        )

    def _preflight_tree(self, repo_root: Path, skill_path: str) -> None:
        target = "FETCH_HEAD" if skill_path == "." else f"FETCH_HEAD:{skill_path}"
        completed = self._git(["ls-tree", "-r", "-l", target], cwd=repo_root, timeout=30)
        if completed.returncode != 0:
            raise SkillInstallError(completed.stderr.strip() or "Skill repository ağacı okunamadı.")
        max_files = max(5, int(self.settings.get("max_files", 250)))
        max_total = max(65536, int(self.settings.get("max_total_bytes", 5_242_880)))
        max_single = max(4096, int(self.settings.get("max_single_file_bytes", 1_048_576)))
        count = 0
        total = 0
        for line in completed.stdout.splitlines():
            # Format: mode type sha size<TAB>path. Symlink/submodule mode is checked before checkout.
            match = re.match(r"^(\d{6})\s+(\w+)\s+([0-9a-f]{40})\s+(-|\d+)\t(.+)$", line)
            if not match:
                continue
            mode, object_type, _, size_raw, path = match.groups()
            if mode in {"120000", "160000"} or object_type != "blob":
                raise SkillValidationError(f"Symlink veya submodule içeren skill reddedildi: {path}")
            size = 0 if size_raw == "-" else int(size_raw)
            if size > max_single:
                raise SkillValidationError(f"Skill dosyası çok büyük: {path} ({size} bayt)")
            if Path(path).suffix.casefold() in _BINARY_DENY:
                raise SkillValidationError(f"Derlenmiş/yürütülebilir binary skill dosyası reddedildi: {path}")
            count += 1
            total += size
            if count > max_files:
                raise SkillValidationError(f"Skill dosya sayısı sınırını aşıyor: {max_files}")
            if total > max_total:
                raise SkillValidationError(f"Skill toplam boyut sınırını aşıyor: {total} bayt")

    def _checkout_sparse(self, repo_root: Path, skill_path: str) -> None:
        if skill_path == ".":
            # Repository kökünün tamamı skill ise preflight boyut sınırlarından sonra
            # normal checkout gerekir; cone-mode "." alt klasörleri dışarıda bırakır.
            commands = [["checkout", "--quiet", "--detach", "FETCH_HEAD"]]
        else:
            commands = [
                ["sparse-checkout", "init", "--cone"],
                ["sparse-checkout", "set", skill_path],
                ["checkout", "--quiet", "--detach", "FETCH_HEAD"],
            ]
        for command in commands:
            completed = self._git(command, cwd=repo_root)
            if completed.returncode != 0:
                raise SkillInstallError(completed.stderr.strip() or "Skill sparse checkout başarısız.")

    @staticmethod
    def _license_report(repo_root: Path, skill_root: Path, declared: str | None) -> dict[str, Any]:
        if declared:
            return {"status": "declared", "value": declared, "path": None}
        candidates = []
        for base in (skill_root, repo_root):
            for name in ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING", "NOTICE"):
                path = base / name
                if path.is_file():
                    candidates.append(path)
        if candidates:
            path = candidates[0]
            try:
                first = path.read_text(encoding="utf-8", errors="replace")[:500].strip()
            except OSError:
                first = ""
            return {"status": "file_present", "value": first.splitlines()[0] if first else None, "path": str(path.relative_to(repo_root))}
        return {"status": "missing", "value": None, "path": None}

    def _scan(self, repo_root: Path, skill_root: Path) -> dict[str, Any]:
        max_files = max(5, int(self.settings.get("max_files", 250)))
        max_total_bytes = max(65536, int(self.settings.get("max_total_bytes", 5_242_880)))
        max_single_bytes = max(4096, int(self.settings.get("max_single_file_bytes", 1_048_576)))
        files: list[dict[str, Any]] = []
        total = 0
        scripts: list[str] = []
        findings: list[dict[str, Any]] = []
        hashes: dict[str, str] = {}
        for path in sorted(skill_root.rglob("*"), key=lambda item: item.as_posix().casefold()):
            if path.is_symlink():
                raise SkillValidationError(f"Sembolik bağlantı içeren skill reddedildi: {path}")
            if not path.is_file():
                continue
            relative = path.relative_to(skill_root).as_posix()
            if ".git" in PurePosixPath(relative).parts:
                raise SkillValidationError("Skill içinde iç içe .git alanı reddedildi.")
            stat_result = path.stat()
            if stat_result.st_size > max_single_bytes:
                raise SkillValidationError(f"Skill dosyası çok büyük: {relative} ({stat_result.st_size} bayt)")
            total += stat_result.st_size
            if total > max_total_bytes:
                raise SkillValidationError(f"Skill toplam boyut sınırını aşıyor: {total} bayt")
            if len(files) >= max_files:
                raise SkillValidationError(f"Skill dosya sayısı sınırını aşıyor: {max_files}")
            suffix = path.suffix.casefold()
            if suffix in _BINARY_DENY:
                raise SkillValidationError(f"Derlenmiş/yürütülebilir binary skill dosyası reddedildi: {relative}")
            mode = stat_result.st_mode
            executable = bool(mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            hashes[relative] = digest
            files.append({"path": relative, "size": stat_result.st_size, "sha256": digest, "executable": executable})
            if suffix in _SCRIPT_SUFFIXES:
                scripts.append(relative)
            if b"\0" not in data[:4096] and stat_result.st_size <= 262144:
                text = data.decode("utf-8", errors="replace")
                for risk_name, pattern in _RISK_PATTERNS:
                    if pattern.search(text):
                        findings.append({"type": risk_name, "path": relative})
        return {
            "files": files,
            "file_count": len(files),
            "total_bytes": total,
            "scripts": scripts,
            "contains_scripts": bool(scripts),
            "findings": findings,
            "file_hashes": hashes,
        }

    def inspect(self, source_value: str, *, ref: str | None = None, skill_path: str | None = None) -> SkillInspection:
        source = parse_github_source(source_value, ref=ref, skill_path=skill_path)
        commit = self._resolve_ref(source)
        inspection_id = uuid.uuid4().hex
        root = self.quarantine_root / inspection_id
        repo_root = root / "repo"
        try:
            self._fetch_commit(source, commit, repo_root)
            discovered_path = self._discover_skill_path(repo_root, source["skill_path"])
            self._preflight_tree(repo_root, discovered_path)
            self._checkout_sparse(repo_root, discovered_path)
            candidate = repo_root if discovered_path == "." else repo_root / discovered_path
            candidate = candidate.resolve(strict=True)
            candidate.relative_to(repo_root.resolve(strict=True))
            if not (candidate / "SKILL.md").is_file():
                raise SkillValidationError("Sparse checkout sonrasında SKILL.md bulunamadı.")
            skill = parse_skill_directory(candidate, scope="quarantine", max_body_chars=int(self.settings.get("max_skill_body_chars", 20000)))
            scan = self._scan(repo_root, candidate)
            license_report = self._license_report(repo_root, candidate, skill.license)
            source_payload = {
                **source,
                "commit": commit,
                "skill_path": discovered_path,
            }
            report = {
                "source": source_payload,
                "skill": skill.catalog_entry(),
                "license": license_report,
                "risk": {
                    "contains_scripts": scan["contains_scripts"],
                    "scripts": scan["scripts"],
                    "findings": scan["findings"],
                    "automatic_script_execution": False,
                },
                "file_count": scan["file_count"],
                "total_bytes": scan["total_bytes"],
                "file_hashes": scan["file_hashes"],
            }
            metadata = {
                "inspection_id": inspection_id,
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "candidate_relative": candidate.relative_to(root).as_posix(),
                "source": source_payload,
                "report": report,
            }
            (root / "inspection.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            return SkillInspection(
                inspection_id=inspection_id,
                root=root,
                skill=skill,
                source=source_payload,
                report=report,
                created_at=metadata["created_at"],
            )
        except Exception:
            shutil.rmtree(root, ignore_errors=True)
            raise

    def load(self, inspection_id: str) -> SkillInspection:
        if not re.fullmatch(r"[0-9a-f]{32}", inspection_id):
            raise SkillInstallError("Geçersiz inspection_id.")
        root = self.quarantine_root / inspection_id
        metadata_path = root / "inspection.json"
        if not metadata_path.is_file():
            raise SkillInstallError("Skill incelemesi bulunamadı veya süresi doldu.")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            candidate = root / metadata["candidate_relative"]
            skill = parse_skill_directory(candidate, scope="quarantine", max_body_chars=int(self.settings.get("max_skill_body_chars", 20000)))
        except (OSError, KeyError, json.JSONDecodeError) as exc:
            raise SkillInstallError("Skill inceleme kaydı bozuk.") from exc
        ttl = max(300, int(self.settings.get("inspection_ttl_seconds", 3600)))
        if time.time() - metadata_path.stat().st_mtime > ttl:
            shutil.rmtree(root, ignore_errors=True)
            raise SkillInstallError("Skill incelemesinin süresi doldu; yeniden incele.")
        return SkillInspection(
            inspection_id=inspection_id,
            root=root,
            skill=skill,
            source=dict(metadata.get("source", {})),
            report=dict(metadata.get("report", {})),
            created_at=str(metadata.get("created_at", "")),
        )

    def cleanup(self) -> None:
        ttl = max(300, int(self.settings.get("inspection_ttl_seconds", 3600)))
        now = time.time()
        for path in self.quarantine_root.iterdir():
            try:
                if path.is_dir() and now - path.stat().st_mtime > ttl:
                    shutil.rmtree(path, ignore_errors=True)
            except OSError:
                continue
