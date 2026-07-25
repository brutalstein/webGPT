from __future__ import annotations

import socket
import threading
import webbrowser
from pathlib import Path
from typing import Any

import uvicorn
from rich.console import Console

from ..config import AppConfig
from ..core.memory_store import MemoryStore
from ..core.provider_registry import ProviderRegistry
from ..core.session_store import SessionStore
from ..core.storage import StateDatabase
from ..errors import ConfigurationError
from ..tools import LocalToolRuntime
from ..tools.approval import TerminalApprovalHandler
from .app import WebAppContext, create_web_app
from .approval import WebApprovalHandler
from .events import EventHub
from .frontend import FrontendBuilder
from .security import LocalWebSecurity
from .worker import AgentWorker
from .workspace_view import WorkspaceViewService


def _available_port(host: str, preferred: int) -> int:
    for port in range(preferred, preferred + 25):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((host, port))
            except OSError:
                continue
            return port
    raise ConfigurationError(f"{preferred}-{preferred + 24} aralığında boş web portu bulunamadı.")


def run_web_server(
    config: AppConfig,
    registry: ProviderRegistry,
    database: StateDatabase,
    sessions: SessionStore,
    memory: MemoryStore,
    tools: LocalToolRuntime,
    root: Path,
    console: Console | None = None,
) -> int:
    settings: dict[str, Any] = dict(config.web)
    if not bool(settings.get("enabled", True)):
        raise ConfigurationError("Web arayüzü config.json içinde kapalı.")
    host = str(settings.get("host", "127.0.0.1")).strip()
    if host not in {"127.0.0.1", "localhost"}:
        raise ConfigurationError("Güvenlik nedeniyle web arayüzü yalnızca 127.0.0.1 üzerinde çalışabilir.")
    host = "127.0.0.1"
    port = _available_port(host, max(1024, int(settings.get("port", 8765))))
    static_dir = FrontendBuilder(root, settings).ensure_built()

    hub = EventHub(
        history_limit=max(50, int(settings.get("event_history_limit", 600))),
        queue_size=max(50, int(settings.get("websocket_queue_size", 1000))),
    )
    approval = WebApprovalHandler(
        hub,
        timeout_seconds=max(5, int(settings.get("approval_timeout_seconds", 600))),
    )
    worker = AgentWorker(config, registry, sessions, memory, tools, hub, approval)
    security = LocalWebSecurity(host=host, port=port)
    context = WebAppContext(
        config=config,
        database=database,
        sessions=sessions,
        memory=memory,
        tools=tools,
        workspace=WorkspaceViewService(tools, settings),
        hub=hub,
        approval=approval,
        worker=worker,
        security=security,
        static_dir=static_dir,
    )
    app = create_web_app(context)
    output = console or Console(highlight=False)
    output.print(f"[bold cyan]OS Web[/bold cyan] · http://{host}:{port}")
    output.print("[dim]Sunucu yalnızca bu bilgisayardan erişilebilir. Kapatmak için Ctrl+C.[/dim]")

    if bool(settings.get("open_browser", True)):
        threading.Timer(0.8, lambda: webbrowser.open(security.auth_url, new=1)).start()

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="warning",
            access_log=False,
            server_header=False,
            date_header=False,
        )
    )
    try:
        server.run()
        return 0
    finally:
        worker.close()
        tools.set_activity_handler(None)
        tools.set_approval_handler(TerminalApprovalHandler())
        provider = registry.peek("gemini")
        if provider is not None:
            set_handler = getattr(provider, "set_event_handler", None)
            if callable(set_handler):
                set_handler(None)
        database.checkpoint()
