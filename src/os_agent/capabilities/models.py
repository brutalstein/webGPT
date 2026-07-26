from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class CapabilityInspection:
    inspection_id: str
    root: Path
    source_root: Path
    source: dict[str, Any]
    package: dict[str, Any]
    adapter: str | None
    report: dict[str, Any]
    created_at: str


@dataclass(frozen=True, slots=True)
class ProcessResult:
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


@dataclass(slots=True)
class CapabilityRecord:
    name: str
    kind: str
    version: str
    commit: str
    source: dict[str, Any]
    install_root: Path
    python_executable: Path
    module: str
    scripts: dict[str, str] = field(default_factory=dict)
    adapter: str | None = None
    trusted_adapter: bool = False
    enabled: bool = True
    auto_start: bool = False
    auto_query: bool = False
    installed_at: str = ""
    status: str = "ready"
    last_error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_wire(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "version": self.version,
            "commit": self.commit,
            "source": dict(self.source),
            "install_root": str(self.install_root),
            "python_executable": str(self.python_executable),
            "module": self.module,
            "scripts": dict(self.scripts),
            "adapter": self.adapter,
            "trusted_adapter": self.trusted_adapter,
            "enabled": self.enabled,
            "auto_start": self.auto_start,
            "auto_query": self.auto_query,
            "installed_at": self.installed_at,
            "status": self.status,
            "last_error": self.last_error,
            "metadata": dict(self.metadata),
        }
