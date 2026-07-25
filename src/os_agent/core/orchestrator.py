from __future__ import annotations

from ..config import AppConfig
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
        self.provider_name = provider_name
        self.session_id = self.sessions.create(provider_name)
        self._started: set[str] = set()

    @property
    def provider(self):
        return self.registry.get(self.provider_name)

    def ensure_started(self) -> None:
        if self.provider_name not in self._started:
            self.provider.start()
            self._started.add(self.provider_name)

    def switch_provider(self, name: str) -> None:
        self.config.provider(name)
        old_provider = self.provider_name
        if old_provider in self._started:
            self.registry.get(old_provider).close()
            self._started.remove(old_provider)

        self.provider_name = name.casefold().strip()
        self.session_id = self.sessions.create(self.provider_name)
        self.ensure_started()

    def new_session(self) -> str:
        self.session_id = self.sessions.create(self.provider_name)
        return self.session_id

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
        return response
