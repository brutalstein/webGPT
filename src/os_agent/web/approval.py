from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass
from typing import Any

from ..tools.models import ApprovalDecision, ApprovalRequest
from .events import EventHub


def _safe_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    hidden = {"content", "old_text", "new_text"}
    result: dict[str, Any] = {}
    for key, value in arguments.items():
        if key in hidden and isinstance(value, str):
            result[key] = {"redacted": True, "characters": len(value)}
        else:
            result[key] = value
    return result


@dataclass(slots=True)
class _PendingApproval:
    request: ApprovalRequest
    event: threading.Event
    decision: ApprovalDecision | None = None


class WebApprovalHandler:
    """Tool worker'ını bloklarken web event loop'unu serbest bırakan onay köprüsü."""

    def __init__(self, hub: EventHub, *, timeout_seconds: int = 600):
        self.hub = hub
        self.timeout_seconds = max(5, timeout_seconds)
        self._pending: dict[str, _PendingApproval] = {}
        self._lock = threading.RLock()

    def __call__(self, request: ApprovalRequest) -> ApprovalDecision:
        approval_id = secrets.token_urlsafe(18)
        pending = _PendingApproval(request=request, event=threading.Event())
        with self._lock:
            self._pending[approval_id] = pending

        self.hub.publish(
            "approval.required",
            {
                "approval_id": approval_id,
                "call_id": request.call.call_id,
                "tool": request.call.name,
                "title": request.definition.title,
                "risk": request.definition.risk.value,
                "summary": request.summary,
                "arguments": _safe_arguments(request.call.arguments),
                "timeout_seconds": self.timeout_seconds,
            },
        )
        signaled = pending.event.wait(timeout=self.timeout_seconds)
        with self._lock:
            self._pending.pop(approval_id, None)

        if not signaled or pending.decision is None:
            self.hub.publish(
                "approval.expired",
                {"approval_id": approval_id, "tool": request.call.name},
            )
            return ApprovalDecision(approved=False)
        return pending.decision

    def resolve(self, approval_id: str, *, approved: bool, remember_for_session: bool = False) -> bool:
        with self._lock:
            pending = self._pending.get(approval_id)
            if pending is None:
                return False
            pending.decision = ApprovalDecision(
                approved=bool(approved),
                remember_for_session=bool(approved and remember_for_session),
            )
            pending.event.set()
        self.hub.publish(
            "approval.resolved",
            {
                "approval_id": approval_id,
                "approved": bool(approved),
                "remember_for_session": bool(approved and remember_for_session),
            },
        )
        return True

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._pending.items())
        return [
            {
                "approval_id": approval_id,
                "call_id": pending.request.call.call_id,
                "tool": pending.request.call.name,
                "title": pending.request.definition.title,
                "risk": pending.request.definition.risk.value,
                "summary": pending.request.summary,
                "arguments": _safe_arguments(pending.request.call.arguments),
            }
            for approval_id, pending in items
        ]

    def cancel_all(self) -> None:
        with self._lock:
            pending_items = list(self._pending.items())
            self._pending.clear()
        for approval_id, pending in pending_items:
            pending.decision = ApprovalDecision(approved=False)
            pending.event.set()
            self.hub.publish("approval.cancelled", {"approval_id": approval_id})
