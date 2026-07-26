from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import venv
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..errors import CapabilityExecutionError, CapabilityInstallError, CapabilityValidationError
from .adapters import AdapterRegistry, CapabilityAdapter, normalize_capability_name
from .github import GitHubCapabilityInspector
from .models import CapabilityInspection, CapabilityRecord, ProcessResult
from .process import CapabilityProcessRunner
from .state import CapabilityStore

if TYPE_CHECKING:
    from ..context import ProjectContextEngine
    from ..skills import SkillManager
    from ..tools.models import ToolCall
    from ..tools.workspace import WorkspaceManager


class CapabilityManager:
    """Global, izole ve provenance kayıtlı executable capability yaşam döngüsü.

    Capability süreçleri ana OS Python sürecine import edilmez. Her paket kendi venv'i
    içinde subprocess olarak çalışır. Bu izolasyon dependency ve process ağacını ayırır;
    kernel seviyesinde tam dosya sistemi/network sandbox garantisi vermez.
    """

    def __init__(
        self,
        workspace: WorkspaceManager,
        project_context: ProjectContextEngine,
        skills: SkillManager,
        root: Path,
        state_dir: Path,
        settings: dict[str, Any],
    ):
        self.workspace = workspace
        self.project_context = project_context
        self.skills = skills
        self.root = root
        self.settings = settings
        self.packages_root = root / "packages"
        self.data_root = root / "data"
        self.quarantine_root = root / "quarantine"
        self.backup_root = root / "backups"
        for path in (self.packages_root, self.data_root, self.quarantine_root, self.backup_root):
            path.mkdir(parents=True, exist_ok=True)
        self.adapters = AdapterRegistry()
        self.store = CapabilityStore(state_dir / "capabilities.sqlite3")
        self.runner = CapabilityProcessRunner(settings)
        self.inspector = GitHubCapabilityInspector(self.quarantine_root, settings, self.adapters)
        self._activity_handler = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._run_locks: dict[str, threading.RLock] = {}
        self._lock = threading.RLock()
        self._last_maintenance: dict[tuple[str, str], float] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.settings.get("enabled", True))

    def set_activity_handler(self, handler) -> None:
        self._activity_handler = handler

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._activity_handler is not None:
            self._activity_handler(event_type, payload)

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._background_loop, name="os-capability-supervisor", daemon=True)
        self._thread.start()
        self._wake.set()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        self.runner.cancel_all()
        if self._thread is not None:
            self._thread.join(timeout=max(2.0, float(self.settings.get("shutdown_timeout_seconds", 8))))
            self._thread = None
        self.store.checkpoint()

    def workspace_changed(self) -> None:
        self._wake.set()

    def _background_loop(self) -> None:
        poll = max(1.0, float(self.settings.get("supervisor_poll_seconds", 3.0)))
        while not self._stop.is_set():
            self._wake.wait(timeout=poll)
            self._wake.clear()
            if self._stop.is_set():
                return
            try:
                self._maintain_workspace()
            except Exception as exc:
                self._emit("capability.supervisor.failed", {"error": str(exc), "error_type": type(exc).__name__})

    def _workspace_identity(self) -> tuple[Path, str] | None:
        if not self.workspace.active:
            return None
        root = self.workspace.require_root()
        key = hashlib.sha256(str(root).casefold().encode("utf-8", errors="replace")).hexdigest()[:24]
        return root, key

    def _lock_for(self, name: str) -> threading.RLock:
        with self._lock:
            return self._run_locks.setdefault(name, threading.RLock())

    def _adapter_for(self, record: CapabilityRecord) -> CapabilityAdapter:
        adapter = self.adapters.get(record.adapter)
        if adapter is None:
            raise CapabilityExecutionError(
                f"{record.name} global olarak kurulu ancak güvenilen otomatik adapter'ı yok. "
                "Generic executable repository'ler ana ajan tarafından otomatik çalıştırılmaz."
            )
        return adapter

    def inspect_github(self, source: str, *, ref: str | None = None) -> CapabilityInspection:
        self._emit("capability.inspect.started", {"source": source, "ref": ref})
        inspection = self.inspector.inspect(source, ref=ref)
        self._emit(
            "capability.inspect.completed",
            {
                "inspection_id": inspection.inspection_id,
                "name": inspection.report.get("name"),
                "classification": inspection.report.get("classification"),
                "report": inspection.report,
            },
        )
        return inspection

    @staticmethod
    def _python_in_venv(venv_root: Path) -> Path:
        return venv_root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    @staticmethod
    def _safe_copy_source(source: Path, target: Path) -> None:
        shutil.copytree(
            source,
            target,
            symlinks=False,
            ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".pytest_cache", ".mypy_cache", ".ruff_cache"),
        )
        for path in target.rglob("*"):
            if path.is_symlink():
                raise CapabilityInstallError(f"Capability staging alanında symlink bulundu: {path}")

    def _run_install_command(self, command: list[str], *, cwd: Path) -> ProcessResult:
        result = self.runner.run(
            command,
            cwd=cwd,
            timeout_seconds=max(60, int(self.settings.get("install_timeout_seconds", 1200))),
            allow_network=True,
            memory_limit_mb=max(512, int(self.settings.get("install_memory_limit_mb", 2048))),
        )
        if not result.ok:
            detail = result.stderr.strip() or result.stdout.strip() or f"çıkış kodu {result.returncode}"
            raise CapabilityInstallError(f"Capability kurulumu başarısız: {detail[-4000:]}")
        return result

    def _verify_import(self, python: Path, package: dict[str, Any], cwd: Path) -> str:
        module = str(package.get("module", "")).strip()
        distribution = str(package.get("name", "")).strip()
        code = (
            "import importlib, importlib.metadata, sys; "
            "importlib.import_module(sys.argv[1]); "
            "print(importlib.metadata.version(sys.argv[2]))"
        )
        result = self.runner.run(
            [str(python), "-c", code, module, distribution],
            cwd=cwd,
            timeout_seconds=60,
            allow_network=False,
            memory_limit_mb=768,
        )
        if not result.ok:
            raise CapabilityInstallError(
                "Capability import doğrulaması başarısız: " + (result.stderr.strip() or result.stdout.strip())[-3000:]
            )
        return result.stdout.strip().splitlines()[-1] if result.stdout.strip() else str(package.get("version", "unknown"))

    @staticmethod
    def _skill_name(record: CapabilityRecord) -> str:
        return str(record.metadata.get("skill_name") or f"{record.name}-global")

    def _generated_skill_target(self, record: CapabilityRecord) -> Path:
        return self.skills.install_root / self._skill_name(record)

    def _install_generated_skill(
        self,
        record: CapabilityRecord,
        adapter: CapabilityAdapter,
        source_root: Path,
    ) -> None:
        skill_text, resources = adapter.generated_skill(record)
        upstream = source_root / "graphify" / "skill-agents.md"
        if upstream.is_file():
            resources["references/upstream-skill.md"] = upstream.read_text(encoding="utf-8", errors="replace")
        target = self._generated_skill_target(record)
        marker_name = ".os-capability-skill.json"
        if target.exists() and not (target / marker_name).is_file():
            raise CapabilityInstallError(
                f"Yönetilen capability skill adı başka bir global skill ile çakışıyor: {target.name}"
            )
        staging_parent = self.skills.install_root
        temp = Path(tempfile.mkdtemp(prefix=f".{record.name}.capability-", dir=staging_parent))
        staged = temp / record.name
        backup: Path | None = None
        try:
            staged.mkdir(parents=True)
            (staged / "SKILL.md").write_text(skill_text, encoding="utf-8")
            for relative, content in resources.items():
                supplied = Path(relative)
                if supplied.is_absolute() or ".." in supplied.parts:
                    raise CapabilityInstallError("Üretilen skill resource yolu güvenli değil.")
                path = staged / supplied
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            marker = {
                "schema_version": 1,
                "capability": record.name,
                "commit": record.commit,
                "version": record.version,
                "managed_by": "OS Global Capability Runtime",
            }
            (staged / marker_name).write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
            (staged / ".os-skill.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "trusted": True,
                        "installed_at": record.installed_at,
                        "source": record.source,
                        "license": {"status": "declared", "value": record.metadata.get("license")},
                        "risk": {
                            "executable_capability": True,
                            "isolated_environment": True,
                            "full_kernel_sandbox": False,
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            if target.exists():
                stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
                backup = self.backup_root / "skills" / stamp / record.name
                backup.parent.mkdir(parents=True, exist_ok=True)
                os.replace(target, backup)
            os.replace(staged, target)
        except Exception:
            if backup is not None and backup.exists() and not target.exists():
                os.replace(backup, target)
            raise
        finally:
            shutil.rmtree(temp, ignore_errors=True)
        self.skills.refresh()

    @staticmethod
    def _manifest_payload(record: CapabilityRecord, inspection: CapabilityInspection) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "record": record.to_wire(),
            "inspection_id": inspection.inspection_id,
            "file_hashes": inspection.report.get("file_hashes", {}),
            "risk": inspection.report.get("risk", {}),
        }

    def install_inspection(
        self,
        inspection_id: str,
        *,
        auto_start: bool | None = None,
        auto_query: bool | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        inspection = self.inspector.load(inspection_id)
        self.inspector.verify(inspection)
        adapter = self.adapters.get(inspection.adapter)
        trusted = bool(adapter and adapter.descriptor.trusted)
        requested_auto_start = adapter.descriptor.default_auto_start if auto_start is None and adapter else bool(auto_start)
        requested_auto_query = adapter.descriptor.default_auto_query if auto_query is None and adapter else bool(auto_query)
        if not trusted and (requested_auto_start or requested_auto_query):
            raise CapabilityValidationError(
                "Generic executable repository için auto_start/auto_query açılamaz; yalnızca denetlenmiş adapter'lar otomatik çalışır."
            )
        name = adapter.descriptor.name if adapter else normalize_capability_name(str(inspection.package["name"]))
        commit = str(inspection.source["commit"])
        package_root = self.packages_root / name
        version_root = package_root / "versions" / commit
        existing = self.store.get(name)
        if existing and existing.commit != commit and not overwrite:
            raise CapabilityInstallError("Capability zaten farklı commit ile kurulu. Güncellemek için overwrite=true gerekli.")

        self._emit("capability.install.started", {"name": name, "commit": commit})
        with self._lock_for(name):
            if version_root.exists() and overwrite:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
                repair_backup = self.backup_root / "packages" / stamp / name / commit
                repair_backup.parent.mkdir(parents=True, exist_ok=True)
                os.replace(version_root, repair_backup)
            if not version_root.exists():
                staging_parent = package_root / ".staging"
                staging_parent.mkdir(parents=True, exist_ok=True)
                staging = Path(tempfile.mkdtemp(prefix=f"{commit[:12]}-", dir=staging_parent))
                staged_version = staging / commit
                source_target = staged_version / "source"
                venv_root = staged_version / "venv"
                try:
                    staged_version.mkdir(parents=True)
                    self._safe_copy_source(inspection.source_root, source_target)
                    # venv stdlib üzerinden kurulur; capability kodu bu aşamada import edilmez.
                    venv.EnvBuilder(with_pip=True, clear=True, symlinks=False).create(venv_root)
                    python = self._python_in_venv(venv_root)
                    self._run_install_command(
                        [
                            str(python), "-m", "pip", "install", "--no-input", "--disable-pip-version-check",
                            "--no-warn-script-location", str(source_target),
                        ],
                        cwd=source_target,
                    )
                    installed_version = self._verify_import(python, inspection.package, source_target)
                    record = CapabilityRecord(
                        name=name,
                        kind="python_cli",
                        version=installed_version,
                        commit=commit,
                        source=dict(inspection.source),
                        install_root=version_root,
                        python_executable=version_root / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"),
                        module=str(inspection.package["module"]),
                        scripts={str(k): str(v) for k, v in dict(inspection.package.get("scripts", {})).items()},
                        adapter=inspection.adapter,
                        trusted_adapter=trusted,
                        enabled=True,
                        auto_start=bool(requested_auto_start),
                        auto_query=bool(requested_auto_query),
                        installed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        status="ready",
                        metadata={
                            "license": inspection.package.get("license"),
                            "description": inspection.package.get("description"),
                            "requires_python": inspection.package.get("requires_python"),
                            "runtime_isolation": "venv+subprocess+sanitized-env+process-tree-limits",
                            "full_kernel_sandbox": False,
                            "skill_name": f"{name}-global",
                        },
                    )
                    # Staging yolu final yola taşınmadan önce manifest staging pathsiz final record ile yazılır.
                    (staged_version / "capability.json").write_text(
                        json.dumps(self._manifest_payload(record, inspection), ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    version_root.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(staged_version, version_root)
                except Exception:
                    shutil.rmtree(staging, ignore_errors=True)
                    raise
                finally:
                    shutil.rmtree(staging, ignore_errors=True)
            python = self._python_in_venv(version_root / "venv")
            installed_version = self._verify_import(python, inspection.package, version_root / "source")
            record = CapabilityRecord(
                name=name,
                kind="python_cli",
                version=installed_version,
                commit=commit,
                source=dict(inspection.source),
                install_root=version_root,
                python_executable=python,
                module=str(inspection.package["module"]),
                scripts={str(k): str(v) for k, v in dict(inspection.package.get("scripts", {})).items()},
                adapter=inspection.adapter,
                trusted_adapter=trusted,
                enabled=True,
                auto_start=bool(requested_auto_start),
                auto_query=bool(requested_auto_query),
                installed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                status="ready",
                metadata={
                    "license": inspection.package.get("license"),
                    "description": inspection.package.get("description"),
                    "requires_python": inspection.package.get("requires_python"),
                    "runtime_isolation": "venv+subprocess+sanitized-env+process-tree-limits",
                    "full_kernel_sandbox": False,
                    "skill_name": f"{name}-global",
                },
            )
            current = package_root / "current.json"
            current.parent.mkdir(parents=True, exist_ok=True)
            temp_current = current.with_suffix(".tmp")
            temp_current.write_text(
                json.dumps({"commit": commit, "version_root": str(version_root)}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temp_current, current)
            if adapter is not None:
                self._install_generated_skill(record, adapter, inspection.source_root)
            self.store.upsert(record)
            self._prune_versions(name, keep=max(1, int(self.settings.get("keep_versions", 2))))
        shutil.rmtree(inspection.root, ignore_errors=True)
        self._emit("capability.install.completed", {"capability": record.to_wire()})
        self._wake.set()
        return record.to_wire()

    def _prune_versions(self, name: str, *, keep: int) -> None:
        versions = self.packages_root / name / "versions"
        if not versions.is_dir():
            return
        current = self.store.get(name)
        protected = current.commit if current else None
        candidates = sorted(
            [path for path in versions.iterdir() if path.is_dir() and path.name != protected],
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        for stale in candidates[max(0, keep - 1):]:
            shutil.rmtree(stale, ignore_errors=True)

    def get(self, name: str) -> CapabilityRecord:
        record = self.store.get(name.casefold().strip())
        if record is None:
            raise CapabilityValidationError(f"Global capability bulunamadı: {name}")
        if not record.python_executable.is_file():
            raise CapabilityValidationError(f"Capability kurulumu eksik veya bozuk: {name}")
        return record

    def _workspace_status(self, record: CapabilityRecord) -> dict[str, Any] | None:
        identity = self._workspace_identity()
        if identity is None:
            return None
        root, key = identity
        adapter = self.adapters.get(record.adapter)
        if adapter is None:
            return None
        output = adapter.output_root(self.data_root, root)
        state = self.store.workspace_state(record.name, key) or {
            "capability": record.name,
            "workspace_key": key,
            "workspace_root": str(root),
            "status": "pending",
            "source_generation": 0,
            "output_root": str(output),
            "graph_path": str(output / "graph.json"),
            "last_error": None,
        }
        state["ready"] = adapter.graph_ready(output)
        return state

    def status(self, name: str | None = None) -> dict[str, Any]:
        records = [self.get(name)] if name else self.store.list()
        capabilities = []
        for record in records:
            item = record.to_wire()
            item["workspace"] = self._workspace_status(record)
            capabilities.append(item)
        return {
            "enabled": self.enabled,
            "install_root": str(self.packages_root),
            "data_root": str(self.data_root),
            "registry_health": self.store.quick_check(),
            "count": len(capabilities),
            "capabilities": capabilities,
            "security_boundary": (
                "İzole venv, subprocess, temizlenmiş environment, timeout ve process-tree limitleri uygulanır; "
                "bu mekanizma kernel seviyesinde tam sandbox değildir."
            ),
        }

    def _run_adapter_action(
        self,
        record: CapabilityRecord,
        action: str,
        arguments: dict[str, Any],
        *,
        background: bool,
    ) -> dict[str, Any]:
        identity = self._workspace_identity()
        if identity is None:
            raise CapabilityExecutionError("Capability çalıştırmak için aktif çalışma alanı gerekli.")
        workspace, workspace_key = identity
        adapter = self._adapter_for(record)
        output = adapter.output_root(self.data_root, workspace)
        output.parent.mkdir(parents=True, exist_ok=True)
        command, env, allow_network = adapter.command(record, action, workspace, output, arguments)
        generation = int(self.project_context.status(refresh=False).get("generation", 0) or 0)
        state_status = "building" if action in {"build", "update"} else "querying"
        self.store.upsert_workspace_state(
            record.name,
            workspace_key,
            workspace_root=str(workspace),
            status=state_status,
            source_generation=generation,
            output_root=str(output),
            graph_path=str(output / "graph.json"),
            last_error=None,
        )
        self._emit(
            "capability.run.started",
            {"name": record.name, "action": action, "workspace": str(workspace), "background": background},
        )
        with self._lock_for(record.name):
            result = self.runner.run(
                command,
                cwd=workspace,
                env_overrides=env,
                timeout_seconds=(
                    int(self.settings.get("build_timeout_seconds", 900))
                    if action in {"build", "update"}
                    else int(self.settings.get("query_timeout_seconds", 90))
                ),
                allow_network=allow_network,
                memory_limit_mb=int(self.settings.get("process_memory_limit_mb", 1536)),
            )
        if not result.ok:
            error = result.stderr.strip() or result.stdout.strip() or (
                "Capability zaman aşımına uğradı." if result.timed_out else f"Çıkış kodu {result.returncode}"
            )
            self.store.upsert_workspace_state(
                record.name,
                workspace_key,
                workspace_root=str(workspace),
                status="error",
                source_generation=generation,
                output_root=str(output),
                graph_path=str(output / "graph.json"),
                last_error=error[-4000:],
            )
            self._emit(
                "capability.run.failed",
                {"name": record.name, "action": action, "error": error[-2000:], "duration_ms": result.duration_ms},
            )
            raise CapabilityExecutionError(f"{record.name} {action} başarısız: {error[-4000:]}")
        ready = adapter.graph_ready(output)
        status = "ready" if ready or action not in {"build", "update"} else "incomplete"
        built_at = datetime.now(timezone.utc).isoformat(timespec="seconds") if action in {"build", "update"} else None
        self.store.upsert_workspace_state(
            record.name,
            workspace_key,
            workspace_root=str(workspace),
            status=status,
            source_generation=generation,
            output_root=str(output),
            graph_path=str(output / "graph.json"),
            last_error=None,
            built_at=built_at,
        )
        payload = {
            "name": record.name,
            "action": action,
            "workspace": str(workspace),
            "output_root": str(output),
            "ready": ready,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration_ms": result.duration_ms,
            "returncode": result.returncode,
            "background": background,
        }
        self._emit("capability.run.completed", payload | {"stdout": result.stdout[:1200], "stderr": result.stderr[:600]})
        return payload

    def query(self, name: str, action: str, arguments: dict[str, Any]) -> dict[str, Any]:
        record = self.get(name)
        if not record.enabled or not record.trusted_adapter:
            raise CapabilityExecutionError("Yalnızca etkin ve güvenilen adapter capability'leri otomatik sorgulanabilir.")
        if action not in {"query", "explain", "path", "report"}:
            raise CapabilityExecutionError("Read-only capability aracı yalnızca query/explain/path/report işlemlerini destekler.")
        state = self._workspace_status(record)
        if action != "report" and (not state or not state.get("ready")):
            self._wake.set()
            raise CapabilityExecutionError(
                f"{record.name} proje grafı henüz hazır değil. Arka plan build başlatıldı; capability_status ile durumu izle."
            )
        return self._run_adapter_action(record, action, arguments, background=False)

    def run(self, name: str, action: str) -> dict[str, Any]:
        if action not in {"build", "update"}:
            raise CapabilityExecutionError("Yüksek riskli capability aracı yalnızca build/update işlemlerini destekler.")
        return self._run_adapter_action(self.get(name), action, {}, background=False)

    def configure(
        self,
        name: str,
        *,
        enabled: bool | None = None,
        auto_start: bool | None = None,
        auto_query: bool | None = None,
    ) -> dict[str, Any]:
        record = self.get(name)
        if not record.trusted_adapter and (auto_start or auto_query):
            raise CapabilityValidationError("Güvenilen adapter'ı olmayan capability otomatik çalıştırılamaz.")
        if enabled is not None:
            record.enabled = enabled
        if auto_start is not None:
            record.auto_start = auto_start
        if auto_query is not None:
            record.auto_query = auto_query
        self.store.upsert(record)
        adapter = self.adapters.get(record.adapter)
        if adapter is not None:
            self._install_generated_skill(record, adapter, record.install_root / "source")
        self._emit("capabilities.changed", {"action": "configured", "capability": record.to_wire()})
        self._wake.set()
        return record.to_wire()

    def uninstall(self, name: str) -> dict[str, Any]:
        record = self.get(name)
        package_root = self.packages_root / record.name
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        backup = self.backup_root / "packages" / stamp / record.name
        backup.parent.mkdir(parents=True, exist_ok=True)
        if package_root.exists():
            os.replace(package_root, backup)
        skill_root = self._generated_skill_target(record)
        if (skill_root / ".os-capability-skill.json").is_file():
            shutil.rmtree(skill_root, ignore_errors=True)
        self.store.delete(record.name)
        self.skills.refresh()
        payload = {"name": record.name, "removed": True, "backup": str(backup)}
        self._emit("capabilities.changed", {"action": "uninstalled", "capability": payload})
        return payload

    def _maintain_workspace(self) -> None:
        identity = self._workspace_identity()
        if identity is None:
            return
        workspace, workspace_key = identity
        context = self.project_context.status(refresh=False)
        generation = int(context.get("generation", 0) or 0)
        min_interval = max(5.0, float(self.settings.get("min_refresh_interval_seconds", 20.0)))
        now = time.monotonic()
        for record in self.store.list():
            if not record.enabled or not record.auto_start or not record.trusted_adapter:
                continue
            adapter = self.adapters.get(record.adapter)
            if adapter is None:
                continue
            output = adapter.output_root(self.data_root, workspace)
            state = self.store.workspace_state(record.name, workspace_key)
            ready = adapter.graph_ready(output)
            prior_generation = int((state or {}).get("source_generation", 0) or 0)
            action = "build" if not ready else ("update" if generation > prior_generation else "")
            if not action:
                continue
            key = (record.name, workspace_key)
            if now - self._last_maintenance.get(key, 0.0) < min_interval:
                continue
            self._last_maintenance[key] = now
            try:
                self._run_adapter_action(record, action, {}, background=True)
            except CapabilityExecutionError:
                # Durum store/event içinde kayıtlı; supervisor yaşamaya devam eder.
                continue

    def record_tool_activity(self, session_id: str, call, result) -> None:
        # Dosya/terminal değişikliklerinden sonra context generation watcher tarafından
        # güncellenir. Supervisor'ı hemen uyandırmak debounce gecikmesini azaltır.
        if result.ok and call.name in {
            "write_file", "append_file", "replace_text", "create_directory", "run_command",
            "refresh_project_context",
        }:
            self._wake.set()

    @staticmethod
    def _extract_install_url(prompt: str) -> str | None:
        lowered = prompt.casefold()
        install_terms = (" kur", "kur ", "yükle", "install", "ekle", "entegre", "global")
        if not any(term in lowered for term in install_terms):
            return None
        import re
        match = re.search(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/(?:tree|blob)/[^\s]+)?", prompt)
        return match.group(0).rstrip(".,);]}") if match else None

    def preflight_calls(self, prompt: str, session_id: str) -> list[ToolCall]:
        from ..tools.models import ToolCall

        if not self.enabled:
            return []
        source = self._extract_install_url(prompt)
        if source:
            digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:10]
            return [
                ToolCall(
                    call_id=f"os-preflight-extension-{digest}",
                    name="inspect_github_extension",
                    arguments={"source": source},
                )
            ]
        if not self.workspace.active:
            return []
        calls: list[ToolCall] = []
        for record in self.store.list():
            if not record.enabled or not record.trusted_adapter:
                continue
            adapter = self.adapters.get(record.adapter)
            if adapter is None or not adapter.is_relevant(prompt):
                continue
            skill_name = self._skill_name(record)
            if skill_name not in self.skills.activated(session_id):
                calls.append(
                    ToolCall(
                        call_id=f"os-preflight-capability-skill-{record.name}",
                        name="activate_skill",
                        arguments={"name": skill_name},
                    )
                )
            state = self._workspace_status(record)
            if record.auto_query and state and state.get("ready"):
                calls.append(
                    ToolCall(
                        call_id=f"os-preflight-capability-query-{record.name}",
                        name="query_capability",
                        arguments={"name": record.name, "action": "query", "query": prompt},
                    )
                )
            elif record.auto_start:
                self._wake.set()
                calls.append(
                    ToolCall(
                        call_id=f"os-preflight-capability-status-{record.name}",
                        name="capability_status",
                        arguments={"name": record.name},
                    )
                )
            break
        return calls[:3]
