from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from ..config import AppConfig
from ..core.memory_store import MemoryStore
from ..core.session_store import SessionStore
from ..core.storage import StateDatabase
from ..errors import OSErrorBase
from ..tools import LocalToolRuntime
from ..ui.workspace_picker import choose_workspace
from .approval import WebApprovalHandler
from .events import EventHub, EventSubscription
from .security import LocalWebSecurity
from .worker import AgentWorker
from .workspace_view import WorkspaceViewService


@dataclass(slots=True)
class WebAppContext:
    config: AppConfig
    database: StateDatabase
    sessions: SessionStore
    memory: MemoryStore
    tools: LocalToolRuntime
    workspace: WorkspaceViewService
    hub: EventHub
    approval: WebApprovalHandler
    worker: AgentWorker
    security: LocalWebSecurity
    static_dir: Path


def _require_auth(request: Request, context: WebAppContext) -> None:
    if not context.security.request_authorized(request):
        raise HTTPException(status_code=401, detail="Yerel OS web oturumu doğrulanmadı.")


def _serialize_session(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": record.get("session_id"),
        "provider": record.get("provider"),
        "title": record.get("title"),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "last_opened_at": record.get("last_opened_at"),
        "provider_state": record.get("provider_state", {}),
        "message_count": record.get("message_count", len(record.get("turns", []))),
        "turns": record.get("turns", []),
    }


def create_web_app(context: WebAppContext) -> FastAPI:
    app = FastAPI(
        title="OS Local Agent",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        is_auth_route = request.url.path.startswith("/auth/")
        if not is_auth_route and not context.security.request_authorized(request):
            return JSONResponse({"detail": "Yetkisiz yerel web oturumu."}, status_code=401)
        if (
            request.url.path.startswith("/api/")
            and request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and request.headers.get("X-Requested-With") != "OS-Web"
        ):
            return JSONResponse({"detail": "Geçersiz yerel API isteği."}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api/") else "no-cache"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            f"connect-src 'self' ws://127.0.0.1:{context.security.port} ws://localhost:{context.security.port}; "
            "font-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        return response

    @app.get("/auth/{token}")
    async def authenticate(token: str):
        if not context.security.consume_auth_token(token):
            raise HTTPException(status_code=404)
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(
            context.security.cookie_name,
            context.security.session_token,
            httponly=True,
            samesite="strict",
            secure=False,
            path="/",
            max_age=12 * 60 * 60,
        )
        return response

    @app.get("/api/bootstrap")
    async def bootstrap(request: Request):
        _require_auth(request, context)
        session_limit = max(10, int(context.config.web.get("session_limit", 60)))
        sessions = context.sessions.list_recent(
            limit=session_limit,
            provider="gemini",
            include_turns=False,
        )
        return {
            "app": {
                "name": context.config.app_name,
                "provider": "gemini",
                "model": context.config.provider("gemini").preferred_model,
                "capabilities": {
                    "thinking_status": True,
                    "response_snapshots": True,
                    "hidden_chain_of_thought": False,
                    "tool_approvals": True,
                    "file_preview": True,
                },
            },
            "workspace": context.workspace.status(),
            "sessions": [_serialize_session(item) for item in sessions],
            "memory": context.memory.list_entries(),
            "tools": context.tools.status().get("tools", []),
            "database_health": context.database.quick_check(),
            "pending_approvals": context.approval.snapshot(),
            "worker_busy": context.worker.busy,
        }

    @app.get("/api/sessions")
    async def list_sessions(request: Request, search: str | None = None):
        _require_auth(request, context)
        limit = max(10, int(context.config.web.get("session_limit", 60)))
        rows = context.sessions.list_recent(
            limit=limit,
            provider="gemini",
            search=search,
            include_turns=False,
        )
        return [_serialize_session(item) for item in rows]

    @app.get("/api/sessions/{session_id}")
    async def read_session(session_id: str, request: Request):
        _require_auth(request, context)
        record = context.sessions.get(session_id)
        if record is None or str(record.get("provider", "")).casefold() != "gemini":
            raise HTTPException(status_code=404, detail="Gemini oturumu bulunamadı.")
        return _serialize_session(record)

    @app.get("/api/workspace")
    async def workspace_status(request: Request):
        _require_auth(request, context)
        return context.workspace.status()

    @app.post("/api/workspace/select")
    async def select_workspace(request: Request):
        _require_auth(request, context)
        payload = await request.json()
        path = str(payload.get("path", "")).strip()
        if not path:
            raise HTTPException(status_code=422, detail="Klasör yolu gerekli.")
        try:
            selected = await asyncio.to_thread(context.tools.workspace.select, path, source="web_path")
        except OSErrorBase as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        result = context.workspace.status()
        context.hub.publish("workspace.changed", {"root": str(selected), "workspace": result})
        return result

    @app.post("/api/workspace/pick")
    async def pick_workspace(request: Request):
        _require_auth(request, context)
        initial = context.tools.workspace.root or Path.cwd()
        selected = await asyncio.to_thread(choose_workspace, initial, allow_terminal_fallback=False)
        if selected is None:
            return Response(status_code=204)
        resolved = await asyncio.to_thread(context.tools.workspace.select, selected, source="web_picker")
        result = context.workspace.status()
        context.hub.publish("workspace.changed", {"root": str(resolved), "workspace": result})
        return result

    @app.get("/api/workspace/tree")
    async def workspace_tree(
        request: Request,
        path: str = ".",
        depth: int | None = None,
        max_entries: int | None = None,
    ):
        _require_auth(request, context)
        try:
            return await asyncio.to_thread(
                context.workspace.list_tree,
                path,
                depth=depth,
                max_entries=max_entries,
            )
        except OSErrorBase as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/workspace/file")
    async def workspace_file(request: Request, path: str):
        _require_auth(request, context)
        try:
            return await asyncio.to_thread(context.workspace.read_file, path)
        except OSErrorBase as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/memory")
    async def list_memory(request: Request):
        _require_auth(request, context)
        return context.memory.list_entries()

    @app.post("/api/memory")
    async def set_memory(request: Request):
        _require_auth(request, context)
        payload = await request.json()
        key = str(payload.get("key", "")).strip()
        value = str(payload.get("value", "")).strip()
        provider = str(payload.get("provider", "")).strip() or None
        try:
            context.memory.set(key, value, provider=provider)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        context.hub.publish("memory.changed", {"entries": context.memory.list_entries()})
        return context.memory.list_entries()

    @app.delete("/api/memory")
    async def delete_memory(request: Request, key: str, provider: str | None = None):
        _require_auth(request, context)
        deleted = context.memory.delete(key, provider=provider or None)
        context.hub.publish("memory.changed", {"entries": context.memory.list_entries()})
        return {"deleted": deleted, "entries": context.memory.list_entries()}

    @app.post("/api/backup")
    async def backup(request: Request):
        _require_auth(request, context)
        path = await asyncio.to_thread(
            context.database.backup_now,
            context.config.backups_dir,
            keep=max(1, int(context.config.storage.get("backup_keep", 10))),
        )
        context.hub.publish("backup.completed", {"path": str(path)})
        return {"path": str(path)}

    JsonSender = Callable[[dict[str, Any]], Awaitable[None]]

    async def send_subscription(send_json: JsonSender, subscription: EventSubscription) -> None:
        while True:
            event = await subscription.queue.get()
            await send_json(event)

    async def execute_command(send_json: JsonSender, message: dict[str, Any]) -> None:
        command = str(message.get("type", ""))
        request_id = str(message.get("request_id", ""))
        try:
            if command == "session.new":
                result = await context.worker.new_session()
                await send_json({"type": "command.result", "request_id": request_id, "payload": result})
            elif command == "session.open":
                result = await context.worker.resume_session(str(message.get("session_id", "")))
                await send_json({"type": "command.result", "request_id": request_id, "payload": result})
            elif command == "chat.send":
                result = await context.worker.send(str(message.get("prompt", "")))
                await send_json({"type": "command.result", "request_id": request_id, "payload": result})
            else:
                await send_json(
                    {"type": "command.error", "request_id": request_id, "payload": {"error": f"Bilinmeyen komut: {command}"}}
                )
        except Exception as exc:
            await send_json(
                {
                    "type": "command.error",
                    "request_id": request_id,
                    "payload": {"error": str(exc), "error_type": type(exc).__name__},
                }
            )

    @app.websocket("/api/ws")
    async def websocket_endpoint(websocket: WebSocket):
        if not context.security.websocket_authorized(websocket):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        send_lock = asyncio.Lock()

        async def safe_send(payload: dict[str, Any]) -> None:
            async with send_lock:
                await websocket.send_json(payload)

        subscription = context.hub.subscribe()
        sender = asyncio.create_task(send_subscription(safe_send, subscription))
        tasks: set[asyncio.Task[Any]] = set()
        try:
            await safe_send(
                {
                    "type": "socket.ready",
                    "payload": {
                        "history": context.hub.history(),
                        "pending_approvals": context.approval.snapshot(),
                        "busy": context.worker.busy,
                    },
                }
            )
            while True:
                message = await websocket.receive_json()
                command = str(message.get("type", ""))
                if command == "ping":
                    await safe_send({"type": "pong"})
                    continue
                if command == "chat.cancel":
                    await safe_send(
                        {
                            "type": "command.result",
                            "request_id": message.get("request_id"),
                            "payload": {"cancel_requested": context.worker.cancel_current()},
                        }
                    )
                    continue
                if command == "approval.resolve":
                    resolved = context.approval.resolve(
                        str(message.get("approval_id", "")),
                        approved=bool(message.get("approved", False)),
                        remember_for_session=bool(message.get("remember_for_session", False)),
                    )
                    await safe_send(
                        {
                            "type": "command.result",
                            "request_id": message.get("request_id"),
                            "payload": {"resolved": resolved},
                        }
                    )
                    continue
                task = asyncio.create_task(execute_command(safe_send, message))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        except WebSocketDisconnect:
            pass
        finally:
            sender.cancel()
            for task in tasks:
                task.cancel()
            context.hub.unsubscribe(subscription)

    app.mount("/", StaticFiles(directory=context.static_dir, html=True), name="web")
    return app
