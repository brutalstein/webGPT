from __future__ import annotations

from typing import Any

from ..config import AppConfig
from ..errors import ProviderError
from ..models import ProviderResponse
from .memory_store import MemoryStore
from .provider_registry import ProviderRegistry
from .session_store import SessionStore


class Orchestrator:
    """Provider yaşam döngüsü ile kalıcı session kaydını eşler."""

    def __init__(
        self,
        config: AppConfig,
        registry: ProviderRegistry,
        sessions: SessionStore,
        memory: MemoryStore,
        provider_name: str,
    ):
        self.config = config
        self.registry = registry
        self.sessions = sessions
        self.memory = memory
        self.provider_name = provider_name.casefold().strip()
        self.config.provider(self.provider_name)
        self.session_id: str | None = None
        self._started: set[str] = set()
        self._bound_sessions: dict[str, str] = {}

    @property
    def provider(self):
        return self.registry.get(self.provider_name)

    def _require_session_id(self) -> str:
        if not self.session_id:
            raise ProviderError("Aktif konuşma seçilmedi.")
        return self.session_id

    def _current_record(self) -> dict[str, Any]:
        session_id = self._require_session_id()
        record = self.sessions.get(session_id)
        if record is None:
            raise ProviderError(f"Yerel oturum bulunamadı: {session_id}")
        return record

    def _settings_snapshot(self) -> dict[str, Any]:
        return self.config.snapshot(self.provider_name)

    def _context_snapshot(self) -> dict[str, str]:
        return self.memory.combined(self.provider_name)

    def _inject_local_memory_enabled(self) -> bool:
        settings = self.config.provider(self.provider_name)
        return bool(settings.get("inject_local_memory", self.config.inject_local_memory))

    def _snapshot_current(self) -> None:
        if not self.session_id:
            return
        self.sessions.update_snapshots(
            self.session_id,
            settings_snapshot=self._settings_snapshot(),
            context_snapshot=self._context_snapshot(),
        )

    def _persist_provider_state(self) -> None:
        if not self.session_id or self.provider_name not in self._started:
            return
        state = self.provider.session_state()
        self.sessions.update_provider_state(self.session_id, state)

    def ensure_started(self) -> None:
        session_id = self._require_session_id()
        if self.provider_name not in self._started:
            self.provider.start()
            self._started.add(self.provider_name)

        if self._bound_sessions.get(self.provider_name) != session_id:
            record = self._current_record()
            state = record.get("provider_state", {})
            resume_state = dict(state) if isinstance(state, dict) else {}
            # Uzak provider durumu kaybolursa yerel SQLite geçmişiyle konuşma yeniden kurulabilir.
            resume_state["_local_turns"] = list(record.get("turns", []))
            self.provider.resume_session(session_id, resume_state)
            self._bound_sessions[self.provider_name] = session_id
            self.sessions.touch_opened(session_id)
            self._persist_provider_state()
            self._snapshot_current()
            self.sessions.record_event(session_id, "session_resumed", {"provider": self.provider_name})

    def switch_provider(self, name: str) -> None:
        target = name.casefold().strip()
        self.config.provider(target)
        if target == self.provider_name:
            return

        old_provider = self.provider_name
        if old_provider in self._started:
            self.flush()
            self.registry.get(old_provider).close()
            self._started.remove(old_provider)
            self._bound_sessions.pop(old_provider, None)

        self.provider_name = target
        self.session_id = None

    def new_session(self) -> str:
        if self.session_id:
            self.flush()
        self.session_id = self.sessions.create(
            self.provider_name,
            settings_snapshot=self._settings_snapshot(),
            context_snapshot=self._context_snapshot(),
        )
        if self.provider_name not in self._started:
            self.provider.start()
            self._started.add(self.provider_name)
        self.provider.new_session(self.session_id)
        self._bound_sessions[self.provider_name] = self.session_id
        self._persist_provider_state()
        self._snapshot_current()
        return self.session_id

    def resume_session(self, session_id: str) -> str:
        record = self.sessions.get(session_id)
        if record is None:
            raise ProviderError(f"Oturum bulunamadı: {session_id}")

        target_provider = str(record.get("provider", "")).casefold().strip()
        self.config.provider(target_provider)
        if target_provider != self.provider_name:
            self.switch_provider(target_provider)
        elif self.session_id and self.session_id != session_id:
            self.flush()

        self.session_id = session_id
        self.ensure_started()
        return session_id

    def latest_session(self) -> dict[str, Any] | None:
        return self.sessions.latest_for_provider(self.provider_name)

    def current_session(self) -> dict[str, Any]:
        return self._current_record()

    def flush(self) -> None:
        if not self.session_id:
            return
        self._persist_provider_state()
        self._snapshot_current()

    def suspend(self) -> None:
        """Bakım işlemleri için aktif provider sürecini güvenli biçimde durdurur."""
        self.flush()
        if self.provider_name in self._started:
            self.provider.close()
            self._started.remove(self.provider_name)
            self._bound_sessions.pop(self.provider_name, None)

    def detach_session(self, session_id: str) -> bool:
        """Silinecek aktif oturumu provider durumunu tekrar yazmadan güvenle ayırır."""
        if self.session_id != session_id:
            return False
        if self.provider_name in self._started:
            self.provider.close()
            self._started.remove(self.provider_name)
        self._bound_sessions.pop(self.provider_name, None)
        self.session_id = None
        return True

    def send(self, user_prompt: str) -> ProviderResponse:
        if not self.session_id:
            self.new_session()
        self.ensure_started()
        session_id = self._require_session_id()

        provider_prompt = user_prompt
        inject_context = self._inject_local_memory_enabled()
        if inject_context:
            context = self.memory.render_context(
                self.provider_name,
                self.config.memory_context_max_chars,
            )
            if context:
                provider_prompt = (
                    "[OS yerel kalıcı bağlamı]\n"
                    f"{context}\n\n"
                    "[Kullanıcı mesajı]\n"
                    f"{user_prompt}"
                )

        self.sessions.add_turn(
            session_id,
            "user",
            user_prompt,
            metadata={"provider": self.provider_name, "context_injected": inject_context},
        )
        try:
            response = self.provider.send(provider_prompt, session_id)
        except Exception as exc:
            self.sessions.record_event(
                session_id,
                "provider_error",
                {"provider": self.provider_name, "error_type": type(exc).__name__, "message": str(exc)},
            )
            self._snapshot_current()
            raise

        self.sessions.add_turn(
            session_id,
            "assistant",
            response.text,
            metadata=response.metadata,
        )
        self._persist_provider_state()
        self._snapshot_current()
        self.sessions.record_event(
            session_id,
            "turn_completed",
            {"provider": self.provider_name, "response_chars": len(response.text)},
        )
        return response
