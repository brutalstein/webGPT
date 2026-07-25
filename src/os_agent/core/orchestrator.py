from __future__ import annotations

from ..config import AppConfig
from ..errors import ProviderError
from ..models import ProviderResponse
from .memory_store import MemoryStore
from .provider_registry import ProviderRegistry
from .session_store import SessionStore


class Orchestrator:
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
        self._started: set[str] = set()
        self._bound_sessions: dict[str, str] = {}
        self.session_id = self._initial_session_id(self.provider_name)

    @property
    def provider(self):
        return self.registry.get(self.provider_name)

    def _initial_session_id(self, provider_name: str) -> str:
        settings = self.config.provider(provider_name)
        if bool(settings.get("resume_latest_session_on_start", True)):
            latest = self.sessions.latest_for_provider(provider_name)
            if latest is not None:
                return str(latest["session_id"])
        return self.sessions.create(provider_name)

    def _current_record(self) -> dict:
        record = self.sessions.get(self.session_id)
        if record is None:
            raise ProviderError(f"Yerel oturum bulunamadı: {self.session_id}")
        return record

    def _persist_provider_state(self) -> None:
        if self.provider_name not in self._started:
            return
        state = self.provider.session_state()
        self.sessions.update_provider_state(self.session_id, state)

    def ensure_started(self) -> None:
        if self.provider_name not in self._started:
            self.provider.start()
            self._started.add(self.provider_name)

        if self._bound_sessions.get(self.provider_name) != self.session_id:
            record = self._current_record()
            state = record.get("provider_state", {})
            self.provider.resume_session(self.session_id, state if isinstance(state, dict) else {})
            self._bound_sessions[self.provider_name] = self.session_id
            self._persist_provider_state()

    def switch_provider(self, name: str) -> None:
        target = name.casefold().strip()
        self.config.provider(target)
        old_provider = self.provider_name
        if old_provider in self._started:
            self._persist_provider_state()
            self.registry.get(old_provider).close()
            self._started.remove(old_provider)
            self._bound_sessions.pop(old_provider, None)

        self.provider_name = target
        self.session_id = self._initial_session_id(target)
        self.ensure_started()

    def new_session(self) -> str:
        self._persist_provider_state()
        self.session_id = self.sessions.create(self.provider_name)
        if self.provider_name not in self._started:
            self.provider.start()
            self._started.add(self.provider_name)
        self.provider.new_session(self.session_id)
        self._bound_sessions[self.provider_name] = self.session_id
        self._persist_provider_state()
        return self.session_id

    def resume_session(self, session_id: str) -> str:
        record = self.sessions.get(session_id)
        if record is None:
            raise ProviderError(f"Oturum bulunamadı: {session_id}")

        target_provider = str(record.get("provider", "")).casefold().strip()
        self.config.provider(target_provider)
        if target_provider != self.provider_name:
            old_provider = self.provider_name
            if old_provider in self._started:
                self._persist_provider_state()
                self.registry.get(old_provider).close()
                self._started.remove(old_provider)
                self._bound_sessions.pop(old_provider, None)
            self.provider_name = target_provider

        self.session_id = session_id
        self.ensure_started()
        return self.session_id

    def current_session(self) -> dict:
        return self._current_record()

    def send(self, user_prompt: str) -> ProviderResponse:
        self.ensure_started()
        provider_prompt = user_prompt
        if self.config.inject_local_memory:
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

        self.sessions.add_turn(self.session_id, "user", user_prompt)
        response = self.provider.send(provider_prompt, self.session_id)
        self.sessions.add_turn(self.session_id, "assistant", response.text)
        self._persist_provider_state()
        return response
