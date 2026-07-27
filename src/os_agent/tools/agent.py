from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..errors import ToolProtocolError
from ..models import ProviderResponse
from .executor import ActivityHandler, ToolExecutor
from .models import ToolCall, ToolResult
from .protocol import ToolProtocol

RawSender = Callable[[str, str], ProviderResponse]


@dataclass(slots=True)
class _FailureRecord:
    epoch: int
    fingerprint: str
    failures: int
    blocked_retries: int
    error: str


class GeminiToolAgent:
    """Gemini web konuşmasını doğrulanan, kendini düzelten yerel araç döngüsüne bağlar."""

    def __init__(self, protocol: ToolProtocol, executor: ToolExecutor, settings: dict[str, Any]):
        self.protocol = protocol
        self.executor = executor
        self.settings = settings
        self.activity_handler: ActivityHandler | None = None

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.activity_handler is not None:
            self.activity_handler(event_type, payload)

    @staticmethod
    def _call_signature(call: ToolCall) -> str:
        canonical = json.dumps(
            {"name": call.name, "arguments": call.arguments},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8", errors="replace")).hexdigest()[:16]

    @staticmethod
    def _normalise_error(result: ToolResult) -> str:
        text = " ".join((result.error or result.content or "unknown tool failure").casefold().split())
        text = re.sub(r"\b0x[0-9a-f]+\b", "<hex>", text)
        text = re.sub(r"\b[0-9a-f]{16,}\b", "<id>", text)
        text = re.sub(r"(?<![a-z])\d+(?:\.\d+)?(?![a-z])", "<n>", text)
        return text[:2000]

    @classmethod
    def _failure_fingerprint(cls, call: ToolCall, result: ToolResult) -> str:
        canonical = f"{call.name}\0{cls._normalise_error(result)}"
        return hashlib.sha256(canonical.encode("utf-8", errors="replace")).hexdigest()[:16]

    @staticmethod
    def _failure_kind(result: ToolResult) -> str:
        text = (result.error or result.content).casefold()
        if "reddetti" in text or "onayı gerekiyor" in text or "approval" in text:
            return "approval"
        if "timeout" in text or "timed out" in text or "temporar" in text or "geçici" in text:
            return "transient"
        if "validation" in text or "geçersiz" in text or "argument" in text:
            return "arguments"
        if "not found" in text or "bulunamad" in text or "no such file" in text:
            return "missing_dependency"
        return "tool_failure"

    @staticmethod
    def _blocked_result(call: ToolCall, signature: str, record: _FailureRecord) -> ToolResult:
        record.blocked_retries += 1
        message = (
            "Aynı araç çağrısı, son başarısızlıktan sonra hiçbir düzeltici işlem başarıyla "
            "tamamlanmadan tekrarlandı. Çağrı çalıştırılmadı. Önce kök nedeni incele; dosya, "
            "yapılandırma, önkoşul veya argümanı değiştir. Aynı doğrulama komutunu ancak "
            "düzeltici bir araç başarıyla tamamlandıktan sonra yeniden dene."
        )
        return ToolResult(
            call_id=call.call_id,
            name=call.name,
            ok=False,
            content=message,
            structured={
                "loop_guard": {
                    "blocked": True,
                    "reason": "unchanged_failed_call",
                    "signature": signature,
                    "repeat_count": record.blocked_retries,
                    "last_error": record.error[:1200],
                }
            },
            error="RepeatedToolFailure: unchanged failed call blocked",
        )

    def _execute_guarded(
        self,
        calls: list[ToolCall],
        session_id: str,
        failures: dict[str, _FailureRecord],
        progress_epoch: int,
    ) -> tuple[list[ToolResult], list[str], list[str]]:
        results: list[ToolResult] = []
        signatures: list[str] = []
        blocked_signatures: list[str] = []
        batch_seen: set[str] = set()
        for call in calls:
            signature = self._call_signature(call)
            signatures.append(signature)
            if signature in batch_seen:
                blocked_signatures.append(signature)
                results.append(
                    ToolResult(
                        call_id=call.call_id,
                        name=call.name,
                        ok=False,
                        content=(
                            "Aynı araç ve argümanlar aynı tur içinde birden fazla kez üretildi. "
                            "Yinelenen çağrı çalıştırılmadı; tek bir sonucu değerlendirip sonraki "
                            "adımda farklı bir düzeltme üret."
                        ),
                        structured={
                            "loop_guard": {
                                "blocked": True,
                                "reason": "duplicate_call_in_batch",
                                "signature": signature,
                                "repeat_count": 1,
                            }
                        },
                        error="RepeatedToolCall: duplicate call in one batch blocked",
                    )
                )
                continue
            batch_seen.add(signature)
            previous = failures.get(signature)
            if previous is not None and previous.epoch == progress_epoch:
                results.append(self._blocked_result(call, signature, previous))
                blocked_signatures.append(signature)
                continue
            try:
                results.append(self.executor.execute(call, session_id))
            except Exception as exc:
                # Registry/policy failures can occur outside ToolExecutor's internal try block.
                # Keep them inside the self-healing loop instead of crashing the provider worker.
                results.append(
                    ToolResult(
                        call_id=call.call_id,
                        name=call.name,
                        ok=False,
                        content=str(exc),
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
        return results, signatures, blocked_signatures

    @staticmethod
    def _trace_item(result: ToolResult, *, preflight: bool = False) -> dict[str, Any]:
        loop_guard = result.structured.get("loop_guard", {}) if isinstance(result.structured, dict) else {}
        item: dict[str, Any] = {
            "id": result.call_id,
            "name": result.name,
            "ok": result.ok,
            "duration_ms": result.duration_ms,
        }
        if preflight:
            item["preflight"] = True
        if result.error:
            item["error"] = result.error[:1200]
        if loop_guard:
            item["loop_guard"] = loop_guard
        return item

    def _finish_safely(
        self,
        sender: RawSender,
        response: ProviderResponse,
        session_id: str,
        *,
        reason: str,
        attempts: int,
        tool_rounds: int,
        recovery_cycles: int,
        trace: list[dict[str, Any]],
        failure_history: list[dict[str, Any]],
        capability_manager: Any,
        last_results: list[ToolResult] | None = None,
    ) -> ProviderResponse:
        recovery = {
            "reason": reason,
            "attempts": attempts,
            "tool_rounds": tool_rounds,
            "recovery_cycles": recovery_cycles,
            "recent_failures": failure_history[-8:],
            "last_results": [
                {
                    "id": result.call_id,
                    "name": result.name,
                    "ok": result.ok,
                    "content": result.content[:2000],
                    "error": result.error[:1200] if result.error else None,
                }
                for result in (last_results or [])
            ],
        }
        try:
            final_response = sender(self.protocol.exhaustion_prompt(recovery), session_id)
        except Exception as exc:
            failure_history = failure_history + [
                {
                    "kind": "safe_stop_provider_failure",
                    "error": f"{type(exc).__name__}: {exc}",
                    "attempt": attempts,
                }
            ]
            final_response = ProviderResponse(
                text="",
                provider=response.provider,
                conversation_id=response.conversation_id,
                metadata=dict(response.metadata),
            )
        text = final_response.text
        if not text.strip() or "<os_tool_calls>" in text.casefold():
            last_error = failure_history[-1]["error"] if failure_history else "Belirli bir araç hatası kaydedilmedi."
            text = (
                "## Araç döngüsü güvenli biçimde durduruldu\n\n"
                "Aynı stratejiyi sonsuza kadar tekrarlamak yerine çalışma durumu korundu. "
                f"Son engel: `{last_error[:800]}`\n\n"
                "Tamamlanan değişiklikler korunuyor; sonraki denemede bu hatayı üreten çağrı "
                "değiştirilmeden yeniden çalıştırılmayacak."
            )
        metadata = dict(final_response.metadata)
        metadata.update(
            {
                "tool_runtime": "self_healing_stopped",
                "termination_reason": reason,
                "workspace": self.protocol.workspace.describe(),
                "tool_attempts": attempts,
                "tool_rounds": tool_rounds,
                "recovery_cycles": recovery_cycles,
                "tool_trace": trace,
                "capabilities": capability_manager.status() if capability_manager is not None else None,
            }
        )
        self._emit(
            "agent.stopped",
            {
                "session_id": session_id,
                "reason": reason,
                "attempts": attempts,
                "tool_rounds": tool_rounds,
            },
        )
        return ProviderResponse(
            text=text,
            provider=final_response.provider,
            conversation_id=final_response.conversation_id,
            metadata=metadata,
        )

    def run(self, sender: RawSender, user_prompt: str, session_id: str) -> ProviderResponse:
        self.executor.reset_run()
        soft_round_limit = max(1, int(self.settings.get("max_agent_rounds", 12)))
        max_attempts = max(
            soft_round_limit + 1,
            int(self.settings.get("max_agent_attempts", soft_round_limit * 3)),
        )
        max_stalled_rounds = max(1, int(self.settings.get("max_stalled_rounds", 3)))
        max_same_failure_repeats = max(1, int(self.settings.get("max_same_failure_repeats", 1)))
        max_recovery_cycles = max(0, int(self.settings.get("max_recovery_cycles", 2)))
        correction_limit = max(1, int(self.settings.get("protocol_correction_retries", 4)))
        correction_budget = correction_limit

        trace: list[dict[str, Any]] = []
        failures: dict[str, _FailureRecord] = {}
        seen_failure_fingerprints: set[str] = set()
        failure_history: list[dict[str, Any]] = []
        progress_epoch = 0
        stalled_rounds = 0
        recovery_cycles = 0
        attempts = 0
        tool_rounds = 0

        self._emit("agent.started", {"session_id": session_id, "phase": "planning"})
        preflight_calls = self.protocol.workspace_preflight(user_prompt)
        capability_manager = self.executor.services.get("capabilities")
        capability_preflight = getattr(capability_manager, "preflight_calls", None)
        if callable(capability_preflight):
            preflight_calls.extend(capability_preflight(user_prompt, session_id))
        preflight_calls = list({call.call_id: call for call in preflight_calls}.values())
        preflight_results = self.executor.execute_many(preflight_calls, session_id) if preflight_calls else []
        trace.extend(self._trace_item(result, preflight=True) for result in preflight_results)
        if preflight_results:
            self._emit("agent.preflight", {"session_id": session_id, "calls": len(preflight_results)})
        response = sender(
            self.protocol.initial_prompt(user_prompt, preflight_results, session_id=session_id),
            session_id,
        )

        while attempts < max_attempts:
            attempts += 1
            self._emit(
                "agent.round",
                {
                    "session_id": session_id,
                    "round": tool_rounds + 1,
                    "attempt": attempts,
                    "soft_limit": soft_round_limit,
                    "extended": tool_rounds >= soft_round_limit,
                },
            )
            try:
                calls = self.protocol.parse_calls(response.text)
            except ToolProtocolError as exc:
                correction_budget -= 1
                self._emit(
                    "agent.protocol_repair",
                    {"session_id": session_id, "remaining": correction_budget, "message": str(exc)},
                )
                if correction_budget <= 0:
                    return self._finish_safely(
                        sender,
                        response,
                        session_id,
                        reason="protocol_correction_exhausted",
                        attempts=attempts,
                        tool_rounds=tool_rounds,
                        recovery_cycles=recovery_cycles,
                        trace=trace,
                        failure_history=failure_history
                        + [{"kind": "protocol", "error": str(exc), "attempt": attempts}],
                        capability_manager=capability_manager,
                    )
                response = sender(self.protocol.correction_prompt(str(exc)), session_id)
                continue

            correction_budget = correction_limit
            if getattr(self.protocol, "last_parse_repaired", False):
                self._emit(
                    "agent.protocol_repaired",
                    {"session_id": session_id, "attempt": attempts},
                )

            if calls is None:
                metadata = dict(response.metadata)
                metadata.update(
                    {
                        "tool_runtime": "enabled",
                        "workspace": self.protocol.workspace.describe(),
                        "tool_attempts": attempts,
                        "tool_rounds": tool_rounds,
                        "recovery_cycles": recovery_cycles,
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
                        "capabilities": capability_manager.status() if capability_manager is not None else None,
                    }
                )
                self._emit(
                    "agent.completed",
                    {
                        "session_id": session_id,
                        "attempts": attempts,
                        "rounds": tool_rounds,
                        "tool_calls": len(trace),
                    },
                )
                return ProviderResponse(
                    text=response.text,
                    provider=response.provider,
                    conversation_id=response.conversation_id,
                    metadata=metadata,
                )

            tool_rounds += 1
            if tool_rounds == soft_round_limit + 1:
                self._emit(
                    "agent.budget_extended",
                    {
                        "session_id": session_id,
                        "soft_limit": soft_round_limit,
                        "hard_attempt_limit": max_attempts,
                    },
                )

            results, signatures, blocked_signatures = self._execute_guarded(
                calls,
                session_id,
                failures,
                progress_epoch,
            )
            trace.extend(self._trace_item(result) for result in results)

            had_success = any(result.ok for result in results)
            next_epoch = progress_epoch + (1 if had_success else 0)
            new_failure = False
            round_failures: list[dict[str, Any]] = []

            for call, result, signature in zip(calls, results, signatures, strict=True):
                if result.ok:
                    failures.pop(signature, None)
                    continue
                loop_guard = result.structured.get("loop_guard", {}) if isinstance(result.structured, dict) else {}
                if loop_guard.get("blocked"):
                    record = failures.get(signature)
                    detail = {
                        "tool": call.name,
                        "kind": str(loop_guard.get("reason") or "repeated_unchanged_call"),
                        "signature": signature,
                        "repeat_count": int(loop_guard.get("repeat_count") or 1),
                        "error": str(
                            loop_guard.get("last_error")
                            or (record.error if record is not None else result.error or result.content)
                        )[:1200],
                    }
                    round_failures.append(detail)
                    failure_history.append(detail | {"attempt": attempts})
                    continue

                fingerprint = self._failure_fingerprint(call, result)
                if fingerprint not in seen_failure_fingerprints:
                    seen_failure_fingerprints.add(fingerprint)
                    new_failure = True
                previous = failures.get(signature)
                failure_count = previous.failures + 1 if previous is not None else 1
                error = result.error or result.content or "unknown tool failure"
                failures[signature] = _FailureRecord(
                    epoch=next_epoch,
                    fingerprint=fingerprint,
                    failures=failure_count,
                    blocked_retries=0,
                    error=error,
                )
                detail = {
                    "tool": call.name,
                    "kind": self._failure_kind(result),
                    "signature": signature,
                    "fingerprint": fingerprint,
                    "failure_count": failure_count,
                    "error": error[:1200],
                }
                round_failures.append(detail)
                failure_history.append(detail | {"attempt": attempts})

            progress_epoch = next_epoch
            if had_success or new_failure:
                stalled_rounds = 0
            else:
                stalled_rounds += 1

            repeated_too_often = any(
                int(result.structured.get("loop_guard", {}).get("repeat_count") or 0)
                >= max_same_failure_repeats
                for result in results
                if isinstance(result.structured, dict)
                and result.structured.get("loop_guard", {}).get("blocked")
            )
            recovery_payload = {
                "attempt": attempts,
                "tool_round": tool_rounds,
                "soft_round_limit": soft_round_limit,
                "remaining_attempts": max_attempts - attempts,
                "progress_epoch": progress_epoch,
                "stalled_rounds": stalled_rounds,
                "blocked_call_signatures": blocked_signatures,
                "failures": round_failures[-8:],
            }

            if stalled_rounds >= max_stalled_rounds or repeated_too_often:
                if recovery_cycles < max_recovery_cycles:
                    recovery_cycles += 1
                    self._emit(
                        "agent.recovery",
                        {
                            "session_id": session_id,
                            "cycle": recovery_cycles,
                            "stalled_rounds": stalled_rounds,
                            "blocked_calls": len(blocked_signatures),
                        },
                    )
                    response = sender(
                        self.protocol.recovery_prompt(recovery_payload | {"cycle": recovery_cycles}),
                        session_id,
                    )
                    stalled_rounds = 0
                    continue
                return self._finish_safely(
                    sender,
                    response,
                    session_id,
                    reason="stalled_without_corrective_progress",
                    attempts=attempts,
                    tool_rounds=tool_rounds,
                    recovery_cycles=recovery_cycles,
                    trace=trace,
                    failure_history=failure_history,
                    capability_manager=capability_manager,
                    last_results=results,
                )

            if attempts >= max_attempts:
                return self._finish_safely(
                    sender,
                    response,
                    session_id,
                    reason="hard_attempt_limit",
                    attempts=attempts,
                    tool_rounds=tool_rounds,
                    recovery_cycles=recovery_cycles,
                    trace=trace,
                    failure_history=failure_history,
                    capability_manager=capability_manager,
                    last_results=results,
                )

            response = sender(self.protocol.results_prompt(results, recovery=recovery_payload), session_id)

        return self._finish_safely(
            sender,
            response,
            session_id,
            reason="hard_attempt_limit",
            attempts=attempts,
            tool_rounds=tool_rounds,
            recovery_cycles=recovery_cycles,
            trace=trace,
            failure_history=failure_history,
            capability_manager=capability_manager,
        )
