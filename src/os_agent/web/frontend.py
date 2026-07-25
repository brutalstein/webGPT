from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import ConfigurationError


_OFFICIAL_NPM_REGISTRY = "https://registry.npmjs.org/"
_EXACT_VERSION = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
_RETRYABLE_NPM_ERRORS = (
    "etarget",
    "notarget",
    "e404",
    "eai_again",
    "econnreset",
    "etimedout",
    "enetunreach",
    "fetch failed",
)


@dataclass(slots=True)
class _CommandFailure(RuntimeError):
    description: str
    return_code: int
    output: str


class FrontendBuilder:
    """React/Vite arayüzünü doğrulanmış ve kendini onaran biçimde üretir."""

    def __init__(self, root: Path, settings: dict[str, Any]):
        self.root = root
        self.settings = settings
        self.source_dir = root / str(settings.get("frontend_source_dir", "web"))
        self.dist_dir = root / str(settings.get("frontend_dist_dir", "web/dist"))
        self.build_marker = self.source_dir / ".build-hash"
        self.dependencies_marker = self.source_dir / ".deps-hash"
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            self.npm_cache_dir = Path(local_app_data) / "OS" / "npm-cache"
        else:
            self.npm_cache_dir = Path.home() / ".cache" / "os-agent" / "npm"

    @staticmethod
    def _hash_files(paths: list[Path], *, base: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(paths):
            if not path.is_file():
                continue
            digest.update(path.relative_to(base).as_posix().encode("utf-8"))
            digest.update(path.read_bytes())
        return digest.hexdigest()

    def _source_hash(self) -> str:
        paths: list[Path] = []
        for path in self.source_dir.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(self.source_dir)
            if relative.parts and relative.parts[0] in {"node_modules", "dist"}:
                continue
            if path in {self.build_marker, self.dependencies_marker}:
                continue
            paths.append(path)
        return self._hash_files(paths, base=self.source_dir)

    def _dependencies_hash(self) -> str:
        candidates = [self.source_dir / "package.json", self.source_dir / "package-lock.json"]
        return self._hash_files([path for path in candidates if path.exists()], base=self.source_dir)

    def _manifest(self) -> dict[str, Any]:
        path = self.source_dir / "package.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigurationError(f"Web package.json okunamadı: {exc}") from exc
        if not isinstance(payload, dict):
            raise ConfigurationError("Web package.json kökü nesne olmalı.")
        return payload

    def _declared_dependencies(self) -> dict[str, str]:
        manifest = self._manifest()
        result: dict[str, str] = {}
        for group in ("dependencies", "devDependencies"):
            values = manifest.get(group, {})
            if not isinstance(values, dict):
                raise ConfigurationError(f"package.json içindeki {group} nesne olmalı.")
            for name, version in values.items():
                result[str(name)] = str(version)
        return result

    def _installed_package_manifest(self, package_name: str) -> Path:
        parts = package_name.split("/")
        return self.source_dir / "node_modules" / Path(*parts) / "package.json"

    def _dependency_problems(self) -> list[str]:
        problems: list[str] = []
        for package_name, expected in self._declared_dependencies().items():
            manifest_path = self._installed_package_manifest(package_name)
            if not manifest_path.is_file():
                problems.append(f"{package_name}: kurulu değil")
                continue
            if not _EXACT_VERSION.fullmatch(expected):
                continue
            try:
                installed = json.loads(manifest_path.read_text(encoding="utf-8")).get("version")
            except (OSError, json.JSONDecodeError):
                problems.append(f"{package_name}: kurulum manifestosu bozuk")
                continue
            if installed != expected:
                problems.append(f"{package_name}: beklenen {expected}, bulunan {installed or 'bilinmiyor'}")
        return problems

    def ready(self) -> bool:
        if not (self.dist_dir / "index.html").is_file() or not self.build_marker.is_file():
            return False
        try:
            return self.build_marker.read_text(encoding="utf-8").strip() == self._source_hash()
        except OSError:
            return False

    def dependencies_ready(self) -> bool:
        if not (self.source_dir / "node_modules").is_dir() or not self.dependencies_marker.is_file():
            return False
        try:
            marker_matches = self.dependencies_marker.read_text(encoding="utf-8").strip() == self._dependencies_hash()
            return marker_matches and not self._dependency_problems()
        except (OSError, ConfigurationError):
            return False

    @staticmethod
    def _find_program(name: str) -> str | None:
        candidates = [name]
        if os.name == "nt" and not name.endswith(".cmd"):
            candidates.insert(0, f"{name}.cmd")
        for candidate in candidates:
            located = shutil.which(candidate)
            if located:
                return located
        return None

    def _validate_node(self) -> tuple[str, str]:
        node = self._find_program("node")
        npm = self._find_program("npm")
        if node is None or npm is None:
            raise ConfigurationError(
                "Web arayüzü ilk derlemesi için Node.js 22.12+ ve npm gerekli. "
                "Node.js LTS kurulduktan sonra os.bat'i yeniden çalıştır."
            )
        completed = subprocess.run(
            [node, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=15,
        )
        match = re.search(r"v(\d+)\.(\d+)", completed.stdout)
        if completed.returncode != 0 or match is None:
            raise ConfigurationError("Node.js sürümü okunamadı.")
        major, minor = int(match.group(1)), int(match.group(2))
        if major < 22 or (major == 22 and minor < 12):
            raise ConfigurationError(
                f"Web arayüzü için Node.js 22.12+ gerekli; bulunan sürüm: {completed.stdout.strip()}"
            )
        return node, npm

    @staticmethod
    def _sanitize_output(output: str) -> str:
        clean = re.sub(r"(https?://)[^/@\s]+@", r"\1***@", output)
        clean = re.sub(r"(?i)(_authToken\s*[=:]\s*)\S+", r"\1<redacted>", clean)
        return clean

    @classmethod
    def _output_tail(cls, output: str, *, max_lines: int = 35, max_chars: int = 6000) -> str:
        clean = cls._sanitize_output(output).strip()
        lines = clean.splitlines()[-max_lines:]
        return "\n".join(lines)[-max_chars:]

    @staticmethod
    def _retryable_install_failure(output: str) -> bool:
        lowered = output.casefold()
        return any(marker in lowered for marker in _RETRYABLE_NPM_ERRORS)

    def _run_npm(self, command: list[str], *, timeout: int, description: str) -> str:
        environment = os.environ.copy()
        environment.setdefault("CI", "true")
        environment.setdefault("npm_config_update_notifier", "false")
        environment.setdefault("npm_config_fund", "false")
        environment.setdefault("npm_config_audit", "false")
        try:
            completed = subprocess.run(
                command,
                cwd=self.source_dir,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=max(30, timeout),
            )
        except subprocess.TimeoutExpired as exc:
            raise ConfigurationError(f"{description} zaman aşımına uğradı.") from exc

        output = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
        if completed.returncode != 0:
            raise _CommandFailure(description, completed.returncode, output)
        if output:
            print(self._sanitize_output(output))
        return output

    def _configured_registry(self, npm: str) -> str:
        try:
            completed = subprocess.run(
                [npm, "config", "get", "registry"],
                cwd=self.source_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            return _OFFICIAL_NPM_REGISTRY
        value = completed.stdout.strip()
        return value if completed.returncode == 0 and value.startswith(("http://", "https://")) else _OFFICIAL_NPM_REGISTRY

    def _install_command(self, npm: str, *, registry: str) -> list[str]:
        self.npm_cache_dir.mkdir(parents=True, exist_ok=True)
        return [
            npm,
            "install",
            "--no-audit",
            "--no-fund",
            "--package-lock=false",
            "--prefer-online",
            "--fetch-retries=3",
            "--fetch-retry-mintimeout=1000",
            "--fetch-retry-maxtimeout=10000",
            f"--cache={self.npm_cache_dir}",
            f"--registry={registry}",
        ]

    def _reset_partial_install(self, *, clear_cache: bool) -> None:
        self.dependencies_marker.unlink(missing_ok=True)
        node_modules = self.source_dir / "node_modules"
        if node_modules.exists():
            shutil.rmtree(node_modules, ignore_errors=True)
        if clear_cache and self.npm_cache_dir.exists():
            shutil.rmtree(self.npm_cache_dir, ignore_errors=True)

    def _install_dependencies(self, npm: str, *, timeout: int) -> None:
        configured_registry = self._configured_registry(npm)
        registries = [configured_registry]
        if configured_registry.rstrip("/") != _OFFICIAL_NPM_REGISTRY.rstrip("/"):
            registries.append(_OFFICIAL_NPM_REGISTRY)
        else:
            # Aynı resmi registry ikinci kez temiz, proje-özel cache ile denenir.
            registries.append(_OFFICIAL_NPM_REGISTRY)

        last_failure: _CommandFailure | None = None
        for attempt, registry in enumerate(registries, start=1):
            if attempt > 1:
                print("[WEB] İlk npm kurulumu başarısız oldu; yarım kurulum temizlenip resmi registry ile yeniden deneniyor...")
                self._reset_partial_install(clear_cache=True)
            try:
                self._run_npm(
                    self._install_command(npm, registry=registry),
                    timeout=timeout,
                    description="Web bağımlılık kurulumu (npm install)",
                )
            except _CommandFailure as exc:
                last_failure = exc
                if attempt == 1 and self._retryable_install_failure(exc.output):
                    continue
                break

            problems = self._dependency_problems()
            if problems:
                last_failure = _CommandFailure(
                    "Web bağımlılık doğrulaması",
                    1,
                    "\n".join(problems),
                )
                if attempt == 1:
                    continue
                break

            self.dependencies_marker.write_text(self._dependencies_hash(), encoding="utf-8")
            return

        assert last_failure is not None
        detail = self._output_tail(last_failure.output) or "npm ayrıntı üretmedi."
        raise ConfigurationError(
            f"{last_failure.description} başarısız oldu (kod {last_failure.return_code}).\n\n"
            f"Son npm çıktısı:\n{detail}\n\n"
            "OS yarım node_modules kurulumunu temizledi ve resmi npm registry ile yeniden denedi. "
            "Hata sürüyorsa `npm config get registry` ve `npm ping --registry=https://registry.npmjs.org/` "
            "komutlarıyla ağ/registry erişimini kontrol et."
        )

    def ensure_built(self) -> Path:
        if self.ready():
            return self.dist_dir
        if not self.source_dir.is_dir() or not (self.source_dir / "package.json").is_file():
            raise ConfigurationError(f"Web kaynak klasörü bulunamadı: {self.source_dir}")
        _, npm = self._validate_node()

        if not self.dependencies_ready():
            print("[WEB] React bağımlılıkları hazırlanıyor; bu işlem yalnızca paketler değiştiğinde yapılır...")
            self._install_dependencies(
                npm,
                timeout=int(self.settings.get("frontend_install_timeout_seconds", 600)),
            )

        print("[WEB] React arayüzü üretim modunda derleniyor...")
        try:
            self._run_npm(
                [npm, "run", "build"],
                timeout=int(self.settings.get("frontend_build_timeout_seconds", 180)),
                description="React üretim derlemesi (npm run build)",
            )
        except _CommandFailure as exc:
            detail = self._output_tail(exc.output) or "npm ayrıntı üretmedi."
            raise ConfigurationError(f"{exc.description} başarısız oldu.\n\nSon çıktı:\n{detail}") from exc
        if not (self.dist_dir / "index.html").is_file():
            raise ConfigurationError("React derlemesi tamamlandı ancak web/dist/index.html bulunamadı.")
        self.build_marker.write_text(self._source_hash(), encoding="utf-8")
        print("[WEB] React arayüzü hazır.")
        return self.dist_dir
