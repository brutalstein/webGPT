from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from os_agent.config import load_config
from os_agent.tools import LocalToolRuntime
from os_agent.web.workspace_view import WorkspaceViewService


class WorkspaceViewTests(unittest.TestCase):
    def test_tree_and_utf8_preview_remain_inside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = root / "config.json"
            project = root / "project"
            project.mkdir()
            (project / "src").mkdir()
            (project / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
            config_path.write_text(
                '{"app_name":"OSTest","default_provider":"gemini","providers":{"gemini":{"kind":"x","enabled":true,"expected_email":"a@b.com","preferred_browser":"chrome","preferred_model":"x"}},"local_tools":{"enabled":true,"default_to_process_cwd":false,"ignored_directories":[".git"]}}',
                encoding="utf-8",
            )
            previous = os.environ.get("LOCALAPPDATA")
            os.environ["LOCALAPPDATA"] = str(root / "data")
            try:
                runtime = LocalToolRuntime(load_config(config_path))
                runtime.workspace.select(project)
                view = WorkspaceViewService(runtime, {"workspace_tree_depth": 3})
                tree = view.list_tree()
                preview = view.read_file("src/main.py")
            finally:
                if previous is None:
                    os.environ.pop("LOCALAPPDATA", None)
                else:
                    os.environ["LOCALAPPDATA"] = previous

            self.assertIn("src/main.py", [entry["path"] for entry in tree["entries"]])
            self.assertEqual(preview["language"], "python")
            self.assertIn("print", preview["content"])
