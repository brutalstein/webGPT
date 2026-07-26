from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..errors import ToolLoopError, ToolProtocolError
from ..models import ProviderResponse
from .executor import ActivityHandler, ToolExecutor
from .protocol import ToolProtocol

RawSender = Callable[[str, str], ProviderResponse]


class GeminiToolAgent:
    """Gemini web konuşmasını doğrulanan yerel araç döngüsüne bağlar."""

    def __init__(self, protocol: ToolProtocol, executor: ToolExecutor, settings: dict[str, Any]):
        self.protocol = protocol
        self.executor = executor
        self.settings = settings
        self.activity_handler: ActivityHandler | None = None

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.activity_handler is not None:
            self.activity_handler(event_type, payload)

    def run(self, sender: RawSender, user_prompt: str, session_id: str) -> ProviderResponse:
        self.executor.reset_run()
        max_rounds = max(1, int(self.settings.get("max_agent_rounds", 12)))
        correction_budget = max(3, int(self.settings.get("protocol_correction_retries", 4)))
        trace: list[dict[str, Any]] = []
        self._emit("agent.started", {"session_id": session_id, "phase": "planning"})
        preflight_calls = self.protocol.workspace_preflight(user_prompt)
        capability_manager = self.executor.services.get("capabilities")
        capability_preflight = getattr(capability_manager, "preflight_calls", None)
        if callable(capability_preflight):
            preflight_calls.extend(capability_preflight(user_prompt, session_id))
        # Aynı preflight aracını aynı id ile iki kez yürütme.
        preflight_calls = list({call.call_id: call for call in preflight_calls}.values())
        preflight_results = self.executor.execute_many(preflight_calls, session_id) if preflight_calls else []
        trace.extend(
            {
                "id": result.call_id,
                "name": result.name,
                "ok": result.ok,
                "duration_ms": result.duration_ms,
                "preflight": True,
            }
            for result in preflight_results
        )
        if preflight_results:
            self._emit("agent.preflight", {"session_id": session_id, "calls": len(preflight_results)})
        response = sender(
            self.protocol.initial_prompt(user_prompt, preflight_results, session_id=session_id),
            session_id,
        )

        for round_index in range(max_rounds):
            self._emit("agent.round", {"session_id": session_id, "round": round_index + 1})
            try:
                calls = self.protocol.parse_calls(response.text)
            except ToolProtocolError as exc:
                if correction_budget <= 0:
                    self._emit(
                        "agent.protocol_exhausted",
                        {"session_id": session_id, "message": str(exc)},
                    )
                    response = sender(
                        "[OS ARAÇ PLANI KURTARMA]\n"
                        "Önceki araç zarfı birkaç kez geçersizdi. Bu turda araç çağrısı üretme. "
                        "Kullanıcıya görevin hangi kısmının tamamlandığını ve otomatik olarak "
                        "hangi adımın yeniden deneneceğini temiz Türkçe Markdown ile açıkla.",
                        session_id,
                    )
                    return ProviderResponse(
                        text=response.text,
                        provider=response.provider,
                        conversation_id=response.conversation_id,
                        metadata=dict(response.metadata) | {
                            "tool_runtime": "recovered_without_tools",
                            "tool_trace": trace,
                        },
                    )
                correction_budget -= 1
                self._emit(
                    "agent.protocol_repair",
                    {"session_id": session_id, "remaining": correction_budget, "message": str(exc)},
                )
                response = sender(self.protocol.correction_prompt(str(exc)), session_id)
                continue

            if getattr(self.protocol, "last_parse_repaired", False):
                self._emit(
                    "agent.protocol_repaired",
                    {"session_id": session_id, "round": round_index + 1},
                )

            if calls is None:
                metadata = dict(response.metadata)
                metadata.update(
                    {
                        "tool_runtime": "enabled",
                        "workspace": self.protocol.workspace.describe(),
                        "tool_rounds": round_index,
                        "tool_trace": trace,
                        "project_context": (
                            self.protocol.project_context.status(refresh=False)
                            if self.protocol.project_context is not None
                            else None
                        ),
                        "active_skills": (
                            self.protocol.skills.activated(session_id)
                            if self.protocol.skills is not None
                            else []
                        ),
                        "capabilities": (
                            capability_manager.status()
                            if capability_manager is not None
                            else None
                        ),
                    }
                )
                self._emit(
                    "agent.completed",
                    {"session_id": session_id, "rounds": round_index, "tool_calls": len(trace)},
                )
                return ProviderResponse(
                    text=response.text,
                    provider=response.provider,
                    conversation_id=response.conversation_id,
                    metadata=metadata,
                )

            results = self.executor.execute_many(calls, session_id)
            trace.extend(
                {
                    "id": result.call_id,
                    "name": result.name,
                    "ok": result.ok,
                    "duration_ms": result.duration_ms,
                }
                for result in results
            )
            response = sender(self.protocol.results_prompt(results), session_id)

        raise ToolLoopError(f"Gemini araç döngüsü {max_rounds} tur sınırına ulaştı.")
