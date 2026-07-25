from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


@dataclass(slots=True)
class ProviderResponse:
    text: str
    provider: str
    conversation_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ChatTurn:
    role: str
    text: str
    created_at: str = field(default_factory=utc_now_iso)


@dataclass(slots=True)
class SessionRecord:
    session_id: str
    provider: str
    title: str
    created_at: str
    updated_at: str
    turns: list[ChatTurn] = field(default_factory=list)
