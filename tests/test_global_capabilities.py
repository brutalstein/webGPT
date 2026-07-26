from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from os_agent.capabilities.adapters import AdapterRegistry, GraphifyAdapter
from os_agent.capabilities.github import GitHubCapabilityInspector, parse_github_repository
from os_agent.capabilities.manager import CapabilityManager
from os_agent.capabilities.models import CapabilityRecord
from os_agent.capabilities.process import CapabilityProcessRunner
from os_agent.capabilities.state import CapabilityStore
from os_agent.config import load_config


class FakeWorkspace:
    def __init__(self, root: Path):
        self.root = root
        self.active = True
        self.trusted = True

    def require_root(self) -> Path:
        return self.root


class FakeContext:
    def __init__(self):
        self.generation = 7

    def status(self, *, refresh: bool = False):
        return {"generation": self.generation, "indexed": True}


class FakeSkills:
    def __init__(self, root: Path):
        self.install_root = root
        self._active: dict[str, set[str]] = {}
        root.mkdir(parents=True, exist_ok=True)

    def refresh(self):
        return []

    def activated(self, session_id: str):
        return sorted(self._active.get(session_id, set()))


class GlobalCapabilityTests(unittest.TestCase):
    def test_config_loads_global_capability_policy(self) -> None:
        config = load_config(Path(__file__).resolve().parents[1] / "config.json")
        self.assertTrue(config.capabilities["enabled"])
        self.assertIn("inspect_github_extension", config.local_tools["allowed_tools"])
        self.assertFalse(config.capabilities["full_kernel_sandbox"])

    def test_graphify_adapter_requires_official_repo_and_package(self) -> None:
        registry = AdapterRegistry()
        official = registry.detect(
            {"owner": "Graphify-Labs", "repo": "graphify"},
            {"name": "graphifyy"},
        )
        impostor = registry.detect(
            {"owner": "attacker", "repo": "graphify"},
            {"name": "graphifyy"},
        )
        self.assertIsInstance(official, GraphifyAdapter)
        self.assertIsNone(impostor)

    def test_github_url_parser_rejects_credentials(self) -> None:
        parsed = parse_github_repository("https://github.com/Graphify-Labs/graphify")
        self.assertEqual(parsed["owner"], "Graphify-Labs")
        with self.assertRaises(Exception):
            parse_github_repository("https://token@github.com/Graphify-Labs/graphify")

    def test_pyproject_classifier_detects_python_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pyproject.toml").write_text(
                """
[project]
name = "graphifyy"
version = "0.9.26"
license = "Apache-2.0"
requires-python = ">=3.10"
[project.scripts]
graphify = "graphify.__main__:main"
""".strip(),
                encoding="utf-8",
            )
            package = GitHubCapabilityInspector._read_package(root)
            self.assertEqual(package["name"], "graphifyy")
            self.assertEqual(package["module"], "graphify")
            self.assertIn("graphify", package["scripts"])

    def test_store_persists_capability_and_workspace_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = CapabilityStore(root / "capabilities.sqlite3")
            record = CapabilityRecord(
                name="graphify",
                kind="python_cli",
                version="0.9.26",
                commit="a" * 40,
                source={"web_url": "https://github.com/Graphify-Labs/graphify"},
                install_root=root / "package",
                python_executable=Path(sys.executable),
                module="graphify",
                adapter="graphify",
                trusted_adapter=True,
                auto_start=True,
                auto_query=True,
            )
            store.upsert(record)
            store.upsert_workspace_state(
                "graphify", "workspace-key", workspace_root=str(root), status="ready",
                source_generation=7, output_root=str(root / "out"), graph_path=str(root / "out/graph.json"),
            )
            loaded = store.get("graphify")
            state = store.workspace_state("graphify", "workspace-key")
            self.assertIsNotNone(loaded)
            self.assertTrue(loaded.auto_query)
            self.assertEqual(state["source_generation"], 7)
            self.assertEqual(store.quick_check().casefold(), "ok")

    def test_process_environment_removes_credentials(self) -> None:
        env = CapabilityProcessRunner.sanitized_environment(
            {"OPENAI_API_KEY": "secret", "GRAPHIFY_OUT": "safe"}, allow_network=False
        )
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertEqual(env["GRAPHIFY_OUT"], "safe")
        self.assertEqual(env["HTTPS_PROXY"], "http://127.0.0.1:9")

    def test_preflight_inspects_install_url_and_auto_queries_ready_graph(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "project"
            workspace.mkdir()
            skills = FakeSkills(root / "skills")
            manager = CapabilityManager(
                FakeWorkspace(workspace), FakeContext(), skills,
                root / "extensions", root / "state", {"enabled": True},
            )
            install_calls = manager.preflight_calls(
                "https://github.com/Graphify-Labs/graphify bu aracı global kur", "session-1"
            )
            self.assertEqual([call.name for call in install_calls], ["inspect_github_extension"])

            adapter = GraphifyAdapter()
            output = adapter.output_root(manager.data_root, workspace)
            output.mkdir(parents=True)
            (output / "graph.json").write_text("{}", encoding="utf-8")
            record = CapabilityRecord(
                name="graphify", kind="python_cli", version="0.9.26", commit="b" * 40,
                source={"owner": "Graphify-Labs", "repo": "graphify"},
                install_root=root / "package", python_executable=Path(sys.executable), module="graphify",
                adapter="graphify", trusted_adapter=True, auto_start=True, auto_query=True,
            )
            manager.store.upsert(record)
            calls = manager.preflight_calls("Bu projenin mimarisini ve çağrı akışını açıkla", "session-1")
            self.assertEqual([call.name for call in calls], ["activate_skill", "query_capability"])
            self.assertEqual(calls[0].arguments["name"], "graphify-global")
            manager.close()

    def test_generated_skill_is_global_and_contains_runtime_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            (source / "graphify").mkdir(parents=True)
            (source / "graphify/skill-agents.md").write_text("upstream", encoding="utf-8")
            skills = FakeSkills(root / "skills")
            manager = CapabilityManager(
                FakeWorkspace(root), FakeContext(), skills,
                root / "extensions", root / "state", {"enabled": True},
            )
            record = CapabilityRecord(
                name="graphify", kind="python_cli", version="0.9.26", commit="c" * 40,
                source={"web_url": "https://github.com/Graphify-Labs/graphify"},
                install_root=root / "package", python_executable=Path(sys.executable), module="graphify",
                adapter="graphify", trusted_adapter=True, auto_start=True, auto_query=True,
                installed_at="2026-07-26T00:00:00+00:00", metadata={"license": "Apache-2.0", "skill_name": "graphify-global"},
            )
            manager._install_generated_skill(record, GraphifyAdapter(), source)
            skill_root = skills.install_root / "graphify-global"
            body = (skill_root / "SKILL.md").read_text(encoding="utf-8")
            marker = json.loads((skill_root / ".os-capability-skill.json").read_text(encoding="utf-8"))
            self.assertIn("query_capability", body)
            self.assertIn("global", body.casefold())
            self.assertEqual(marker["capability"], "graphify")
            self.assertTrue((skill_root / "references/upstream-skill.md").is_file())
            manager.close()


if __name__ == "__main__":
    unittest.main()
