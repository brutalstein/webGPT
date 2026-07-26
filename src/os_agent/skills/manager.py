from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..errors import SkillInstallError, SkillValidationError
from .github import GitHubSkillInspector
from .models import SkillInspection, SkillRecord
from .parser import parse_skill_directory

if TYPE_CHECKING:
    from ..tools.workspace import WorkspaceManager


class SkillManager:
    """Global ve proje skill kataloglarını progressive disclosure ile yönetir."""

    MANIFEST_NAME = ".os-skill.json"

    def __init__(
        self,
        workspace: WorkspaceManager,
        install_root: Path,
        quarantine_root: Path,
        backup_root: Path,
        settings: dict[str, Any],
    ):
        self.workspace = workspace
        self.install_root = install_root
        self.quarantine_root = quarantine_root
        self.backup_root = backup_root
        self.settings = settings
        self.install_root.mkdir(parents=True, exist_ok=True)
        self.quarantine_root.mkdir(parents=True, exist_ok=True)
        self.backup_root.mkdir(parents=True, exist_ok=True)
        self.inspector = GitHubSkillInspector(self.quarantine_root, settings)
        self.inspector.cleanup()
        self._lock = threading.RLock()
        self._catalog: dict[str, SkillRecord] = {}
        self._activated: dict[str, set[str]] = {}
        self._activity_handler = None
        self.refresh()

    @property
    def enabled(self) -> bool:
        return bool(self.settings.get("enabled", True))

    def set_activity_handler(self, handler) -> None:
        self._activity_handler = handler

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._activity_handler is not None:
            self._activity_handler(event_type, payload)

    def _project_roots(self) -> list[Path]:
        if not self.workspace.active or not self.workspace.trusted:
            return []
        root = self.workspace.require_root()
        configured = self.settings.get("project_skill_directories", [".agents/skills", ".os/skills"])
        result: list[Path] = []
        for value in configured:
            try:
                candidate = self.workspace.resolve(str(value), must_exist=True)
            except Exception:
                continue
            if candidate.is_dir():
                result.append(candidate)
        return result

    def _scan_root(self, root: Path, scope: str) -> list[SkillRecord]:
        result: list[SkillRecord] = []
        if not root.is_dir():
            return result
        for child in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
            if not child.is_dir() or child.is_symlink() or not (child / "SKILL.md").is_file():
                continue
            try:
                record = parse_skill_directory(
                    child,
                    scope=scope,
                    max_body_chars=max(1000, int(self.settings.get("max_skill_body_chars", 20000))),
                )
            except SkillValidationError:
                continue
            result.append(record)
        return result

    def refresh(self) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        catalog: dict[str, SkillRecord] = {}
        for record in self._scan_root(self.install_root, "global"):
            catalog[record.name] = record
        # Workspace-local skills intentionally shadow global skills with the same name.
        for project_root in self._project_roots():
            for record in self._scan_root(project_root, "project"):
                catalog[record.name] = record
        with self._lock:
            self._catalog = catalog
            active_names = {name for names in self._activated.values() for name in names}
        return [record.catalog_entry(active=record.name in active_names) for record in self.records()]

    def records(self) -> list[SkillRecord]:
        with self._lock:
            return [self._catalog[name] for name in sorted(self._catalog)]

    def get(self, name: str) -> SkillRecord:
        normalized = name.strip().casefold()
        with self._lock:
            record = self._catalog.get(normalized)
        if record is None:
            self.refresh()
            with self._lock:
                record = self._catalog.get(normalized)
        if record is None:
            raise SkillValidationError(f"Skill bulunamadı: {name}")
        return record

    def catalog(self, *, session_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            active = set(self._activated.get(session_id or "", set()))
        return [record.catalog_entry(active=record.name in active) for record in self.records()]

    def prompt_catalog(self, *, session_id: str | None = None) -> list[dict[str, Any]]:
        """Tier-1 progressive disclosure: yalnızca tetikleme için gerekli metadata."""
        with self._lock:
            active = set(self._activated.get(session_id or "", set()))
        return [
            {
                "name": record.name,
                "description": record.description,
                "scope": record.scope,
                "active": record.name in active,
            }
            for record in self.records()
        ]

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {token.casefold() for token in re.findall(r"[\w.-]{2,}", text)}

    def suggest(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        query_tokens = self._tokens(query)
        if not query_tokens:
            return []
        scored: list[tuple[float, SkillRecord]] = []
        for record in self.records():
            name_tokens = self._tokens(record.name.replace("-", " "))
            desc_tokens = self._tokens(record.description)
            metadata_tokens = self._tokens(json.dumps(record.metadata, ensure_ascii=False))
            overlap = len(query_tokens & desc_tokens)
            score = overlap * 2.0 + len(query_tokens & name_tokens) * 4.0 + len(query_tokens & metadata_tokens) * 0.7
            phrase = record.name.replace("-", " ")
            if phrase in query.casefold():
                score += 6.0
            if score > 0:
                scored.append((score, record))
        scored.sort(key=lambda item: (-item[0], item[1].name))
        return [record.catalog_entry() | {"match_score": round(score, 3)} for score, record in scored[: max(1, limit)]]

    def activate(self, name: str, session_id: str) -> dict[str, Any]:
        record = self.get(name)
        max_chars = max(1000, int(self.settings.get("activation_max_chars", 20000)))
        body = record.body[:max_chars]
        truncated = len(record.body) > max_chars
        with self._lock:
            limit = max(10, int(self.settings.get("activation_session_limit", 200)))
            if session_id not in self._activated and len(self._activated) >= limit:
                self._activated.pop(next(iter(self._activated)), None)
            self._activated.setdefault(session_id, set()).add(record.name)
        result = record.catalog_entry(active=True) | {
            "instructions": body + ("\n... <skill talimatı kırpıldı>" if truncated else ""),
            "truncated": truncated,
            "resource_policy": "Kaynakları yalnızca read_skill_resource ile ve ihtiyaç olduğunda oku. Scriptler otomatik çalıştırılmaz.",
        }
        self._emit("skill.activated", {"session_id": session_id, "skill": record.name, "scope": record.scope})
        return result

    def activated(self, session_id: str) -> list[str]:
        with self._lock:
            return sorted(self._activated.get(session_id, set()))

    def read_resource(self, name: str, resource: str, *, session_id: str | None = None) -> dict[str, Any]:
        record = self.get(name)
        if session_id is not None:
            with self._lock:
                active = record.name in self._activated.get(session_id, set())
            if not active:
                raise SkillValidationError(
                    f"Skill resource okumadan önce skill'i etkinleştir: {record.name}"
                )
        supplied = Path(resource)
        if supplied.is_absolute() or ".." in supplied.parts:
            raise SkillValidationError("Skill resource yolu skill kökünün dışına çıkamaz.")
        candidate = (record.root / supplied).resolve(strict=True)
        try:
            candidate.relative_to(record.root.resolve(strict=True))
        except ValueError as exc:
            raise SkillValidationError("Skill resource yolu skill kökünün dışına çıkıyor.") from exc
        if candidate.is_symlink() or not candidate.is_file():
            raise SkillValidationError("Skill resource normal bir dosya olmalı.")
        max_bytes = max(4096, int(self.settings.get("max_resource_bytes", 524288)))
        if candidate.stat().st_size > max_bytes:
            raise SkillValidationError(f"Skill resource çok büyük: {candidate.stat().st_size} > {max_bytes}")
        data = candidate.read_bytes()
        if b"\0" in data[:4096]:
            raise SkillValidationError("Binary skill resource modele açılamaz.")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SkillValidationError("Skill resource UTF-8 metni değil.") from exc
        return {
            "skill": record.name,
            "resource": supplied.as_posix(),
            "content": text,
            "characters": len(text),
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    def inspect_github(self, source: str, *, ref: str | None = None, skill_path: str | None = None) -> SkillInspection:
        self._emit("skill.inspect.started", {"source": source, "ref": ref, "skill_path": skill_path})
        inspection = self.inspector.inspect(source, ref=ref, skill_path=skill_path)
        self._emit(
            "skill.inspect.completed",
            {"inspection_id": inspection.inspection_id, "skill": inspection.skill.name, "report": inspection.report},
        )
        return inspection

    def _backup_existing(self, path: Path) -> str | None:
        if not path.is_dir():
            return None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        target = self.backup_root / stamp / path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(path, target)
        return str(target)

    @staticmethod
    def _verify_inspection_payload(inspection: SkillInspection) -> None:
        expected = inspection.report.get("file_hashes", {})
        if not isinstance(expected, dict) or not expected:
            raise SkillInstallError("Skill inceleme kaydında dosya hash manifestosu yok.")
        actual: dict[str, str] = {}
        for path in sorted(inspection.skill.root.rglob("*"), key=lambda item: item.as_posix().casefold()):
            if path.is_symlink():
                raise SkillInstallError("Karantina skill'i incelemeden sonra symlink ile değiştirildi.")
            if not path.is_file() or path.name == SkillManager.MANIFEST_NAME:
                continue
            relative = path.relative_to(inspection.skill.root).as_posix()
            actual[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        normalized_expected = {str(key): str(value) for key, value in expected.items()}
        if actual != normalized_expected:
            missing = sorted(set(normalized_expected) - set(actual))
            added = sorted(set(actual) - set(normalized_expected))
            changed = sorted(key for key in set(actual) & set(normalized_expected) if actual[key] != normalized_expected[key])
            raise SkillInstallError(
                "Karantina skill içeriği incelemeden sonra değişti; kurulum iptal edildi. "
                f"missing={missing[:5]}, added={added[:5]}, changed={changed[:5]}"
            )

    def install_inspection(self, inspection_id: str, *, overwrite: bool = False) -> dict[str, Any]:
        inspection = self.inspector.load(inspection_id)
        skill = inspection.skill
        self._verify_inspection_payload(inspection)
        target = self.install_root / skill.name
        if target.exists() and not overwrite:
            raise SkillInstallError("Skill zaten kurulu. Güncellemek için overwrite=true gerekli.")
        backup = self._backup_existing(target) if target.exists() else None
        parent = self.install_root
        temp = Path(tempfile.mkdtemp(prefix=f".{skill.name}.", dir=parent))
        staged = temp / skill.name
        try:
            shutil.copytree(skill.root, staged)
            # Skill kaynakları veri olarak kurulur; Unix executable bitleri kaldırılır.
            for installed_file in staged.rglob("*"):
                if installed_file.is_file():
                    try:
                        installed_file.chmod(installed_file.stat().st_mode & ~0o111)
                    except OSError:
                        pass
            manifest = {
                "schema_version": 1,
                "trusted": True,
                "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "source": inspection.source,
                "license": inspection.report.get("license", {}),
                "risk": inspection.report.get("risk", {}),
                "file_hashes": inspection.report.get("file_hashes", {}),
                "inspection_id": inspection.inspection_id,
            }
            (staged / self.MANIFEST_NAME).write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            # Parse the exact staged payload before it becomes live.
            parse_skill_directory(staged, scope="global", max_body_chars=int(self.settings.get("max_skill_body_chars", 20000)))
            old = None
            try:
                if target.exists():
                    old = parent / f".{skill.name}.old-{os.getpid()}"
                    if old.exists():
                        shutil.rmtree(old, ignore_errors=True)
                    os.replace(target, old)
                os.replace(staged, target)
            except Exception:
                if old is not None and old.exists() and not target.exists():
                    os.replace(old, target)
                raise
            if old is not None:
                shutil.rmtree(old, ignore_errors=True)
        except Exception:
            shutil.rmtree(temp, ignore_errors=True)
            raise
        finally:
            shutil.rmtree(temp, ignore_errors=True)
            shutil.rmtree(inspection.root, ignore_errors=True)
        self.refresh()
        record = self.get(skill.name)
        payload = record.catalog_entry() | {"backup": backup}
        self._emit("skills.changed", {"action": "installed", "skill": payload})
        return payload

    def uninstall(self, name: str) -> dict[str, Any]:
        record = self.get(name)
        if record.scope != "global" or record.root.parent.resolve() != self.install_root.resolve():
            raise SkillInstallError("Yalnızca OS global skill kurulumları kaldırılabilir; proje skill'i workspace tarafından yönetilir.")
        backup = self._backup_existing(record.root)
        shutil.rmtree(record.root)
        with self._lock:
            for active in self._activated.values():
                active.discard(record.name)
        self.refresh()
        payload = {"name": record.name, "removed": True, "backup": backup}
        self._emit("skills.changed", {"action": "uninstalled", "skill": payload})
        return payload

    def status(self, *, session_id: str | None = None) -> dict[str, Any]:
        catalog = self.catalog(session_id=session_id)
        return {
            "enabled": self.enabled,
            "install_root": str(self.install_root),
            "project_skills_trusted": self.workspace.trusted,
            "count": len(catalog),
            "active": self.activated(session_id) if session_id else [],
            "skills": catalog,
            "execution_policy": "Skill talimatları ve kaynakları veri olarak yüklenir; indirilen scriptler otomatik çalıştırılmaz.",
        }
