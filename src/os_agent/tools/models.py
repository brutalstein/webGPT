from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ToolRisk(str, Enum):
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    risk: ToolRisk = ToolRisk.READ
    idempotent: bool = True
    destructive: bool = False

    def to_wire(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": {
                "risk": self.risk.value,
                "idempotent": self.idempotent,
                "destructive": self.destructive,
            },
        }


@dataclass(frozen=True, slots=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(slots=True)
class ToolResult:
    call_id: str
    name: str
    ok: bool
    content: str
    structured: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    duration_ms: int = 0

    def to_wire(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.call_id,
            "name": self.name,
            "ok": self.ok,
            "content": self.content,
            "structuredContent": self.structured,
            "durationMs": self.duration_ms,
        }
        if self.error:
            payload["error"] = self.error
        return payload


@dataclass(frozen=True, slots=True)
class ToolPayload:
    content: str
    structured: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    call: ToolCall
    definition: ToolDefinition
    summary: str


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    approved: bool
    remember_for_session: bool = False
