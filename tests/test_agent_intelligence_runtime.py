from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from os_agent.config import load_config
from os_agent.tools import LocalToolRuntime
from os_agent.tools.models import ToolCall, ToolRisk


class AgentIntelligenceRuntimeTests(unittest.TestCase):
    def test_runtime_injects_project_context_and_progressive_skill_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"LOCALAPPDATA": temp}):
            workspace = Path(temp) / "project"
            workspace.mkdir()
            (workspace / "README.md").write_text("# Navigation\nPure pursuit controller and lane planner.\n", encoding="utf-8")
            skill = workspace / ".agents" / "skills" / "controller-review"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                """---
name: controller-review
description: Otonom araç controller ve yol takip kodu incelemelerinde kullan.
license: MIT
---
# Controller Review
Önce kontrolcü sınırlarını ve testleri doğrula.
""",
                encoding="utf-8",
            )
            config = load_config(ROOT / "config.json")
            runtime = LocalToolRuntime(config)
            runtime.workspace.select(workspace, source="test", trusted=True)
            runtime.workspace_changed()

            prompt = runtime.protocol.initial_prompt(
                "Pure pursuit controller kodunu incele",
                session_id="session-x",
            )
            self.assertIn("OS PROJE BAĞLAMI", prompt)
            self.assertIn("controller-review", prompt)
            self.assertIn("progressive", prompt.casefold())
            self.assertNotIn("Önce kontrolcü sınırlarını", prompt)

            result = runtime.executor.execute(
                ToolCall("activate", "activate_skill", {"name": "controller-review"}),
                "session-x",
            )
            self.assertTrue(result.ok, result.content)
            self.assertIn("Önce kontrolcü sınırlarını", result.content)
            self.assertEqual(runtime.skills.activated("session-x"), ["controller-review"])
            runtime.close()

    def test_network_inspection_and_install_are_confirmation_gated(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"LOCALAPPDATA": temp}):
            config = load_config(ROOT / "config.json")
            runtime = LocalToolRuntime(config)
            inspect_definition = runtime.registry.get("inspect_github_skill").definition
            install_definition = runtime.registry.get("install_inspected_skill").definition
            self.assertEqual(inspect_definition.risk, ToolRisk.EXECUTE)
            self.assertEqual(install_definition.risk, ToolRisk.WRITE)
            denied = runtime.executor.execute(
                ToolCall("inspect", "inspect_github_skill", {"source": "https://github.com/acme/skills"}),
                "session-x",
            )
            self.assertFalse(denied.ok)
            self.assertIn("onayı", denied.content)
            runtime.close()


if __name__ == "__main__":
    unittest.main()
