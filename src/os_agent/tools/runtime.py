from __future__ import annotations

from pathlib import Path
from typing import Any

from ..capabilities import CapabilityManager
from ..config import AppConfig
from ..context import ProjectContextEngine
from ..models import ProviderResponse
from ..skills import SkillManager
from .agent import GeminiToolAgent, RawSender
from .audit import ToolAuditLog
from .builtins.capabilities import register_capability_tools
from .builtins.context import register_context_tools
from .builtins.filesystem import register_filesystem_tools
from .builtins.process import register_process_tools
from .builtins.skills import register_skill_tools
from .executor import ActivityHandler, ApprovalHandler, ToolExecutor
from .policy import ToolPolicy
from .protocol import ToolProtocol
from .registry import ToolRegistry
from .workspace import WorkspaceManager


class LocalToolRuntime:
    """Provider'dan bağımsız, çalışma alanı sınırlandırılmış agent çalışma zamanı."""

    def __init__(self, app_config: AppConfig):
        self.settings = dict(app_config.local_tools)
        state_name = str(self.settings.get("workspace_state_file", "workspace.json"))
        self.workspace = WorkspaceManager(
            app_config.state_dir / Path(state_name).name,
            app_config.data_dir / "tool-backups",
            self.settings,
        )
        context_settings = dict(app_config.project_context)
        # Local tool ignore ayarını context motoruna da miras bırak.
        context_settings.setdefault("ignored_directories", self.settings.get("ignored_directories", []))
        context_settings.setdefault("sensitive_file_globs", self.settings.get("sensitive_file_globs", []))
        self.project_context = ProjectContextEngine(
            self.workspace,
            app_config.state_dir / "project-context",
            context_settings,
        )
        self.skills = SkillManager(
            self.workspace,
            app_config.data_dir / "skills",
            app_config.data_dir / "skill-quarantine",
            app_config.data_dir / "skill-backups",
            dict(app_config.skills),
        )
        self.capabilities = CapabilityManager(
            self.workspace,
            self.project_context,
            self.skills,
            app_config.data_dir / "extensions",
            app_config.state_dir,
            dict(app_config.capabilities),
        )
        self.services: dict[str, Any] = {
            "project_context": self.project_context,
            "skills": self.skills,
            "capabilities": self.capabilities,
        }

        self.registry = ToolRegistry()
        register_filesystem_tools(self.registry)
        register_process_tools(self.registry)
        register_capability_tools(self.registry)
        register_context_tools(self.registry)
        register_skill_tools(self.registry)
        self.policy = ToolPolicy(self.settings)
        self.audit = ToolAuditLog(app_config.logs_dir / "tool-audit.jsonl")
        self.executor = ToolExecutor(
            self.registry,
            self.workspace,
            self.policy,
            self.audit,
            self.settings,
            services=self.services,
        )
        self.protocol = ToolProtocol(
            self.registry,
            self.workspace,
            self.settings,
            project_context=self.project_context,
            skills=self.skills,
        )
        self.agent = GeminiToolAgent(self.protocol, self.executor, self.settings)
        self.project_context.start()
        self.capabilities.start()

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
        self.agent.activity_handler = handler
        self.project_context.set_activity_handler(handler)
        self.skills.set_activity_handler(handler)
        self.capabilities.set_activity_handler(handler)

    def workspace_changed(self) -> None:
        self.project_context.workspace_changed()
        self.skills.refresh()
        self.capabilities.workspace_changed()

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

    def close(self) -> None:
        self.capabilities.close()
        self.project_context.close()

    def status(self, *, session_id: str | None = None) -> dict[str, Any]:
        allowed = {str(item) for item in self.settings.get("allowed_tools", [])}
        return {
            "enabled": self.enabled,
            "providers": list(self.settings.get("provider_allowlist", ["gemini"])),
            "workspace": self.workspace.describe(),
            "project_context": self.project_context.status(refresh=False),
            "skills": self.skills.status(session_id=session_id),
            "capabilities": self.capabilities.status(),
            "tools": [
                item.name
                for item in self.registry.definitions()
                if not allowed or item.name in allowed
            ],
        }
