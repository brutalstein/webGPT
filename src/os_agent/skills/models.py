from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class SkillRecord:
    name: str
    description: str
    root: Path
    scope: str
    body: str
    license: str | None = None
    compatibility: str | None = None
    allowed_tools: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    resources: tuple[str, ...] = ()
    manifest: dict[str, Any] = field(default_factory=dict)

    @property
    def trusted(self) -> bool:
        return self.scope in {"global", "project"} or bool(self.manifest.get("trusted", False))

    def catalog_entry(self, *, active: bool = False) -> dict[str, Any]:
        source = self.manifest.get("source", {}) if isinstance(self.manifest, dict) else {}
        return {
            "name": self.name,
            "description": self.description,
            "scope": self.scope,
            "license": self.license,
            "compatibility": self.compatibility,
            "allowed_tools": list(self.allowed_tools),
            "metadata": self.metadata,
            "resources": list(self.resources),
            "active": active,
            "trusted": self.trusted,
            "source": source,
            "license_info": self.manifest.get("license", {}) if isinstance(self.manifest, dict) else {},
            "risk": self.manifest.get("risk", {}) if isinstance(self.manifest, dict) else {},
            "installed_at": self.manifest.get("installed_at") if isinstance(self.manifest, dict) else None,
        }


@dataclass(frozen=True, slots=True)
class SkillInspection:
    inspection_id: str
    root: Path
    skill: SkillRecord
    source: dict[str, Any]
    report: dict[str, Any]
    created_at: str
