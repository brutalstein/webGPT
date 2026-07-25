from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..models import ProviderResponse
from .agent import GeminiToolAgent, RawSender
from .audit import ToolAuditLog
from .builtins.filesystem import register_filesystem_tools
from .builtins.process import register_process_tools
from .executor import ActivityHandler, ApprovalHandler, ToolExecutor
from .policy import ToolPolicy
from .protocol import ToolProtocol
from .registry import ToolRegistry
from .workspace import WorkspaceManager


class LocalToolRuntime:
    """Provider'dan bağımsız, çalışma alanı sınırlandırılmış yerel araç çalışma zamanı."""

    def __init__(self, app_config: AppConfig):
        self.settings = dict(app_config.local_tools)
        state_name = str(self.settings.get("workspace_state_file", "workspace.json"))
        self.workspace = WorkspaceManager(
            app_config.state_dir / Path(state_name).name,
            app_config.data_dir / "tool-backups",
            self.settings,
        )
        self.registry = ToolRegistry()
        register_filesystem_tools(self.registry)
        register_process_tools(self.registry)
        self.policy = ToolPolicy(self.settings)
        self.audit = ToolAuditLog(app_config.logs_dir / "tool-audit.jsonl")
        self.executor = ToolExecutor(
            self.registry,
            self.workspace,
            self.policy,
            self.audit,
            self.settings,
        )
        self.protocol = ToolProtocol(self.registry, self.workspace, self.settings)
        self.agent = GeminiToolAgent(self.protocol, self.executor, self.settings)

    @property
    def enabled(self) -> bool:
        return bool(self.settings.get("enabled", False))

    def provider_enabled(self, provider_name: str) -> bool:
        allowlist = {
            str(item).casefold().strip()
            for item in self.settings.get("provider_allowlist", ["gemini"])
        }
        return self.enabled and provider_name.casefold().strip() in allowlist

    def set_approval_handler(self, handler: ApprovalHandler | None) -> None:
        self.executor.approval_handler = handler

    def set_activity_handler(self, handler: ActivityHandler | None) -> None:
        self.executor.activity_handler = handler

    def run(
        self,
        provider_name: str,
        sender: RawSender,
        prompt: str,
        session_id: str,
    ) -> ProviderResponse:
        if not self.provider_enabled(provider_name):
            return sender(prompt, session_id)
        return self.agent.run(sender, prompt, session_id)

    def status(self) -> dict[str, Any]:
        allowed = {str(item) for item in self.settings.get("allowed_tools", [])}
        return {
            "enabled": self.enabled,
            "providers": list(self.settings.get("provider_allowlist", ["gemini"])),
            "workspace": self.workspace.describe(),
            "tools": [
                item.name
                for item in self.registry.definitions()
                if not allowed or item.name in allowed
            ],
        }
