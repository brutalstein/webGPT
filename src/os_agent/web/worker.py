from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from ..config import AppConfig
from ..core.memory_store import MemoryStore
from ..core.orchestrator import Orchestrator
from ..core.provider_registry import ProviderRegistry
from ..core.session_store import SessionStore
from ..errors import ProviderError
from ..tools import LocalToolRuntime
from .approval import WebApprovalHandler
from .events import EventHub


class AgentWorker:
    """Playwright'ın thread affinity kuralını koruyan tek iş parçacıklı ajan kuyruğu."""

    def __init__(
        self,
        config: AppConfig,
        registry: ProviderRegistry,
        sessions: SessionStore,
        memory: MemoryStore,
        tool_runtime: LocalToolRuntime,
        hub: EventHub,
        approval: WebApprovalHandler,
    ):
        self.config = config
        self.registry = registry
        self.sessions = sessions
        self.memory = memory
        self.tool_runtime = tool_runtime
        self.hub = hub
        self.approval = approval
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="os-gemini-agent")
        self._orchestrator: Orchestrator | None = None
        self._state_lock = threading.RLock()
        self._busy = False
        self._closed = False
        self._session_id: str | None = None

    @property
    def busy(self) -> bool:
        with self._state_lock:
            return self._busy

    @property
    def current_session_id(self) -> str | None:
        with self._state_lock:
            return self._session_id

    def _ensure_orchestrator(self) -> Orchestrator:
        if self._orchestrator is None:
            self.tool_runtime.set_approval_handler(self.approval)
            self.tool_runtime.set_activity_handler(self._publish)
            provider = self.registry.get("gemini")
            set_handler = getattr(provider, "set_event_handler", None)
            if callable(set_handler):
                set_handler(self._publish)
            self._orchestrator = Orchestrator(
                self.config,
                self.registry,
                self.sessions,
                self.memory,
                "gemini",
            )
        return self._orchestrator

    def _publish(self, event_type: str, payload: dict[str, Any]) -> None:
        self.hub.publish(event_type, payload)

    async def _run(self, function: Callable[..., Any], *args: Any) -> Any:
        if self._closed:
            raise RuntimeError("Web ajan worker kapalı.")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, function, *args)

    def _new_session_sync(self) -> dict[str, Any]:
        orchestrator = self._ensure_orchestrator()
        orchestrator.switch_provider("gemini")
        session_id = orchestrator.new_session()
        with self._state_lock:
            self._session_id = session_id
        record = self.sessions.get(session_id)
        assert record is not None
        self._publish("session.opened", {"session": record, "new": True})
        return record

    async def new_session(self) -> dict[str, Any]:
        return await self._run(self._new_session_sync)

    def _resume_session_sync(self, session_id: str) -> dict[str, Any]:
        record = self.sessions.get(session_id)
        if record is None:
            raise ProviderError(f"Oturum bulunamadı: {session_id}")
        if str(record.get("provider", "")).casefold() != "gemini":
            raise ProviderError("Web çalışma alanı şu an yalnızca Gemini oturumlarını açar.")
        orchestrator = self._ensure_orchestrator()
        orchestrator.resume_session(session_id)
        with self._state_lock:
            self._session_id = session_id
        refreshed = self.sessions.get(session_id)
        assert refreshed is not None
        self._publish("session.opened", {"session": refreshed, "new": False})
        return refreshed

    async def resume_session(self, session_id: str) -> dict[str, Any]:
        return await self._run(self._resume_session_sync, session_id)

    def _send_sync(self, prompt: str) -> dict[str, Any]:
        prompt = prompt.strip()
        if not prompt:
            raise ProviderError("Boş mesaj gönderilemez.")
        with self._state_lock:
            if self._busy:
                raise ProviderError("Gemini zaten bir istek üzerinde çalışıyor.")
            self._busy = True
        try:
            orchestrator = self._ensure_orchestrator()
            if not orchestrator.session_id:
                self._new_session_sync()
            session_id = orchestrator.session_id
            self._publish("chat.accepted", {"session_id": session_id, "prompt": prompt})
            response = orchestrator.send(prompt)
            record = orchestrator.current_session()
            payload = {
                "session_id": record["session_id"],
                "response": {
                    "text": response.text,
                    "provider": response.provider,
                    "metadata": response.metadata,
                },
                "session": record,
            }
            self._publish("chat.completed", payload)
            return payload
        except Exception as exc:
            self._publish(
                "chat.failed",
                {"error": str(exc), "error_type": type(exc).__name__},
            )
            raise
        finally:
            with self._state_lock:
                self._busy = False

    async def send(self, prompt: str) -> dict[str, Any]:
        return await self._run(self._send_sync, prompt)

    def cancel_current(self) -> bool:
        provider = self.registry.peek("gemini")
        if provider is None:
            return False
        cancel = getattr(provider, "cancel", None)
        if not callable(cancel):
            return False
        cancel()
        self._publish("chat.cancel_requested", {})
        return True

    def _close_sync(self) -> None:
        if self._orchestrator is not None:
            try:
                self._orchestrator.suspend()
            except Exception:
                pass
        self.approval.cancel_all()

    def close(self) -> None:
        if self._closed:
            return
        self.cancel_current()
        self.approval.cancel_all()
        self._closed = True
        future = self._executor.submit(self._close_sync)
        try:
            future.result(timeout=20)
        except Exception:
            pass
        self._executor.shutdown(wait=True, cancel_futures=True)
