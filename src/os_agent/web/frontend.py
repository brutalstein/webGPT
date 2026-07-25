from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..errors import ConfigurationError


class FrontendBuilder:
    """React/Vite arayüzünü kaynak ve bağımlılık hash'leriyle artımlı üretir."""

    def __init__(self, root: Path, settings: dict[str, Any]):
        self.root = root
        self.settings = settings
        self.source_dir = root / str(settings.get("frontend_source_dir", "web"))
        self.dist_dir = root / str(settings.get("frontend_dist_dir", "web/dist"))
        self.build_marker = self.source_dir / ".build-hash"
        self.dependencies_marker = self.source_dir / ".deps-hash"

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
            return self.dependencies_marker.read_text(encoding="utf-8").strip() == self._dependencies_hash()
        except OSError:
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

    def _run_npm(self, command: list[str], *, timeout: int, description: str) -> None:
        environment = os.environ.copy()
        environment.setdefault("CI", "true")
        environment.setdefault("npm_config_update_notifier", "false")
        try:
            completed = subprocess.run(
                command,
                cwd=self.source_dir,
                env=environment,
                check=False,
                timeout=max(30, timeout),
            )
        except subprocess.TimeoutExpired as exc:
            raise ConfigurationError(f"{description} zaman aşımına uğradı.") from exc
        if completed.returncode != 0:
            raise ConfigurationError(f"{description} başarısız oldu.")

    def ensure_built(self) -> Path:
        if self.ready():
            return self.dist_dir
        if not self.source_dir.is_dir() or not (self.source_dir / "package.json").is_file():
            raise ConfigurationError(f"Web kaynak klasörü bulunamadı: {self.source_dir}")
        _, npm = self._validate_node()

        if not self.dependencies_ready():
            print("[WEB] React bağımlılıkları hazırlanıyor; bu işlem yalnızca paketler değiştiğinde yapılır...")
            self._run_npm(
                [npm, "install", "--no-audit", "--no-fund", "--package-lock=false", "--prefer-offline"],
                timeout=int(self.settings.get("frontend_install_timeout_seconds", 600)),
                description="Web bağımlılık kurulumu (npm install)",
            )
            self.dependencies_marker.write_text(self._dependencies_hash(), encoding="utf-8")

        print("[WEB] React arayüzü üretim modunda derleniyor...")
        self._run_npm(
            [npm, "run", "build"],
            timeout=int(self.settings.get("frontend_build_timeout_seconds", 180)),
            description="React üretim derlemesi (npm run build)",
        )
        if not (self.dist_dir / "index.html").is_file():
            raise ConfigurationError("React derlemesi tamamlandı ancak web/dist/index.html bulunamadı.")
        self.build_marker.write_text(self._source_hash(), encoding="utf-8")
        print("[WEB] React arayüzü hazır.")
        return self.dist_dir
