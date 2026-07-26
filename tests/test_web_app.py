from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from os_agent.config import load_config
from os_agent.core.memory_store import MemoryStore
from os_agent.core.provider_registry import ProviderRegistry
from os_agent.core.session_store import SessionStore
from os_agent.core.storage import StateDatabase
from os_agent.tools import LocalToolRuntime
from os_agent.web.app import WebAppContext, create_web_app
from os_agent.web.approval import WebApprovalHandler
from os_agent.web.events import EventHub
from os_agent.web.security import LocalWebSecurity
from os_agent.web.worker import AgentWorker
from os_agent.web.workspace_view import WorkspaceViewService


class WebAppSmokeTests(unittest.TestCase):
    def test_one_time_auth_cookie_unlocks_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            static = root / "dist"
            static.mkdir()
            (static / "index.html").write_text("<html>OS</html>", encoding="utf-8")
            config_path = root / "config.json"
            config_path.write_text(
                '{"app_name":"OSWebTest","default_provider":"gemini","providers":{"gemini":{"kind":"gemini_chrome_cdp","enabled":true,"expected_email":"a@b.com","preferred_browser":"chrome","preferred_model":"Pro"}},"local_tools":{"enabled":true,"default_to_process_cwd":false},"web":{"session_limit":10}}',
                encoding="utf-8",
            )
            previous = os.environ.get("LOCALAPPDATA")
            os.environ["LOCALAPPDATA"] = str(root / "data")
            worker = None
            database = None
            try:
                config = load_config(config_path)
                database = StateDatabase(config.database_path)
                sessions = SessionStore(database)
                memory = MemoryStore(database)
                tools = LocalToolRuntime(config)
                workspace = root / "workspace"
                workspace.mkdir()
                tools.workspace.select(workspace)
                registry = ProviderRegistry(config)
                hub = EventHub()
                approval = WebApprovalHandler(hub, timeout_seconds=5)
                worker = AgentWorker(config, registry, sessions, memory, tools, hub, approval)
                security = LocalWebSecurity("127.0.0.1", 8765)
                app = create_web_app(
                    WebAppContext(
                        config=config,
                        database=database,
                        sessions=sessions,
                        memory=memory,
                        tools=tools,
                        workspace=WorkspaceViewService(tools, config.web),
                        hub=hub,
                        approval=approval,
                        worker=worker,
                        security=security,
                        static_dir=static,
                    )
                )
                with TestClient(app, base_url="http://127.0.0.1:8765") as client:
                    self.assertEqual(client.get("/api/bootstrap").status_code, 401)
                    authenticated = client.get(f"/auth/{security.auth_token}", follow_redirects=False)
                    self.assertEqual(authenticated.status_code, 303)
                    self.assertIn(security.cookie_name, client.cookies)
                    response = client.get("/api/bootstrap")
                    self.assertEqual(response.status_code, 200)
                    payload = response.json()
                    self.assertEqual(payload["app"]["provider"], "gemini")
                    self.assertIn("skills", payload)
                    self.assertIn("project_context", payload)
                    self.assertEqual(client.get("/api/skills").status_code, 200)
                    self.assertEqual(client.get("/api/project-context").status_code, 200)
                    self.assertEqual(client.get(f"/auth/{security.auth_token}").status_code, 404)
            finally:
                if worker is not None:
                    worker.close()
                if database is not None:
                    database.checkpoint()
                if previous is None:
                    os.environ.pop("LOCALAPPDATA", None)
                else:
                    os.environ["LOCALAPPDATA"] = previous

    def test_context_refresh_is_forced_and_skills_use_current_session(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = (root / "src/os_agent/web/app.py").read_text(encoding="utf-8")
        worker = (root / "src/os_agent/web/worker.py").read_text(encoding="utf-8")
        self.assertIn("project_context.refresh, force=True", source)
        self.assertIn("session_id=context.worker.current_session_id", source)
        self.assertIn("def current_session_id", worker)
