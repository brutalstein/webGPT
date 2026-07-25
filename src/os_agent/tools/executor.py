from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from ..errors import ToolError
from .audit import ToolAuditLog
from .models import ApprovalDecision, ApprovalRequest, ToolCall, ToolResult
from .policy import ToolPolicy
from .registry import ToolContext, ToolRegistry
from .workspace import WorkspaceManager

ApprovalHandler = Callable[[ApprovalRequest], ApprovalDecision]
ActivityHandler = Callable[[str, dict[str, Any]], None]


def _safe_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    hidden = {"content", "old_text", "new_text"}
    result: dict[str, Any] = {}
    for key, value in arguments.items():
        if key in hidden and isinstance(value, str):
            result[key] = {"redacted": True, "characters": len(value)}
        else:
            result[key] = value
    return result


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        workspace: WorkspaceManager,
        policy: ToolPolicy,
        audit: ToolAuditLog,
        settings: dict[str, Any],
    ):
        self.registry = registry
        self.workspace = workspace
        self.policy = policy
        self.audit = audit
        self.settings = settings
        self.approval_handler: ApprovalHandler | None = None
        self.activity_handler: ActivityHandler | None = None
        self._session_approvals: set[str] = set()
        self._approval_session_id: str | None = None
        self._executed: dict[str, ToolResult] = {}

    def reset_run(self) -> None:
        self._executed.clear()

    def execute_many(self, calls: list[ToolCall], session_id: str) -> list[ToolResult]:
        return [self.execute(call, session_id) for call in calls]

    def execute(self, call: ToolCall, session_id: str) -> ToolResult:
        if self._approval_session_id != session_id:
            self._approval_session_id = session_id
            self._session_approvals.clear()
        if call.call_id in self._executed:
            return self._executed[call.call_id]

        started = time.monotonic()
        tool = self.registry.get(call.name)
        definition = tool.definition
        self.policy.require_tool(definition)
        self.registry.validate_arguments(call.name, call.arguments)

        summary = tool.summarize(call.arguments)
        if self.activity_handler:
            self.activity_handler(
                "tool.requested",
                {
                    "call_id": call.call_id,
                    "tool": call.name,
                    "title": definition.title,
                    "risk": definition.risk.value,
                    "summary": summary,
                    "arguments": _safe_arguments(call.arguments),
                },
            )

        try:
            if self.policy.requires_confirmation(definition, call.arguments) and call.name not in self._session_approvals:
                if self.approval_handler is None:
                    raise ToolError(f"{definition.title} için kullanıcı onayı gerekiyor.")
                decision = self.approval_handler(
                    ApprovalRequest(call=call, definition=definition, summary=summary)
                )
                if not decision.approved:
                    raise ToolError("Kullanıcı araç çağrısını reddetti.")
                if decision.remember_for_session:
                    self._session_approvals.add(call.name)

            if self.activity_handler:
                self.activity_handler(
                    "tool.started",
                    {"call_id": call.call_id, "tool": call.name, "title": definition.title, "summary": summary},
                )
            context = ToolContext(workspace=self.workspace, settings=self.settings, session_id=session_id)
            payload = tool.execute(context, call.arguments)
            duration = int((time.monotonic() - started) * 1000)
            content_limit = max(1000, int(self.settings.get("max_tool_result_chars", 24000)))
            content = payload.content
            if len(content) > content_limit:
                content = content[:content_limit] + f"\n... <{len(payload.content) - content_limit} karakter kırpıldı>"
            result = ToolResult(
                call_id=call.call_id,
                name=call.name,
                ok=True,
                content=content,
                structured=payload.structured,
                duration_ms=duration,
            )
        except Exception as exc:
            duration = int((time.monotonic() - started) * 1000)
            result = ToolResult(
                call_id=call.call_id,
                name=call.name,
                ok=False,
                content=str(exc),
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=duration,
            )

        self._executed[call.call_id] = result
        self.audit.write(
            session_id=session_id,
            call_id=call.call_id,
            tool=call.name,
            arguments=call.arguments,
            ok=result.ok,
            duration_ms=result.duration_ms,
            error=result.error,
        )
        if self.activity_handler:
            self.activity_handler(
                "tool.completed" if result.ok else "tool.failed",
                {
                    "call_id": call.call_id,
                    "tool": call.name,
                    "title": definition.title,
                    "summary": summary,
                    "ok": result.ok,
                    "duration_ms": result.duration_ms,
                    "preview": result.content[:1200],
                    "error": result.error,
                },
            )
        return result
