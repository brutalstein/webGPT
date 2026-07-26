from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from os_agent.context import ProjectContextEngine
from os_agent.context.analyzers import StructuralAnalyzer
from os_agent.context.store import ContextIndexStore
from os_agent.tools.models import ToolCall, ToolResult
from os_agent.tools.workspace import WorkspaceManager


class StructuralAnalyzerTests(unittest.TestCase):
    def test_regex_fallback_extracts_symbols_imports_and_calls(self) -> None:
        analyzer = StructuralAnalyzer({"tree_sitter_enabled": False})
        analysis = analyzer.analyze(
            "service.py",
            "from core.worker import Worker\n\nclass Service:\n    def run(self):\n        return Worker()\n",
        )
        self.assertEqual(analysis.backend, "regex")
        names = {item["name"] for item in analysis.symbols}
        self.assertIn("Service", names)
        self.assertIn("run", names)
        self.assertTrue(any("core.worker" in item["target"] for item in analysis.imports))
        self.assertTrue(any(item["name"].endswith("Worker") for item in analysis.references))


class ContextIndexStoreTests(unittest.TestCase):
    def test_fts_symbols_and_impact_graph(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = ContextIndexStore(Path(temp), "workspace")
            records = [
                {
                    "path": "core/worker.py",
                    "size": 64,
                    "mtime_ns": 1,
                    "language": "Python",
                    "chunks": [{"line_start": 1, "line_end": 3, "text": "class Worker:\n    def execute_task(self):\n        return True"}],
                    "analysis": {
                        "backend": "regex",
                        "parse_errors": 0,
                        "symbols": [
                            {"name": "Worker", "qualified_name": "Worker", "kind": "class", "line_start": 1, "line_end": 3, "signature": "class Worker"},
                            {"name": "execute_task", "qualified_name": "execute_task", "kind": "function", "line_start": 2, "line_end": 3, "signature": "def execute_task"},
                        ],
                        "imports": [],
                        "references": [],
                    },
                },
                {
                    "path": "app.py",
                    "size": 48,
                    "mtime_ns": 2,
                    "language": "Python",
                    "chunks": [{"line_start": 1, "line_end": 2, "text": "from core.worker import Worker\nWorker().execute_task()"}],
                    "analysis": {
                        "backend": "regex",
                        "parse_errors": 0,
                        "symbols": [],
                        "imports": [{"target": "core.worker", "target_path": "core/worker.py", "line": 1}],
                        "references": [{"name": "execute_task", "target_path": None, "kind": "call", "line": 2}],
                    },
                },
            ]
            sync = store.sync(records, analyzer_version=2)
            self.assertEqual(sync["files"], 2)
            self.assertTrue(store.search_chunks("execute task"))
            self.assertTrue(any(item["name"] == "execute_task" for item in store.search_symbols("execute_task")))
            impact = store.symbol_impact("core/worker.py")
            self.assertIn("app.py", impact["related_paths"])
            self.assertEqual(store.health(check_integrity=True)["integrity"], "ok")
            store.close()


class ContinuousProjectBrainTests(unittest.TestCase):
    def test_background_worker_reindexes_dirty_paths_before_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "workspace"
            root.mkdir()
            source = root / "worker.py"
            source.write_text("def old_value():\n    return 'old'\n", encoding="utf-8")
            workspace = WorkspaceManager(
                base / "state" / "workspace.json",
                base / "backups",
                {"default_to_process_cwd": False, "protected_path_parts": [".git"]},
            )
            workspace.select(root, source="test", trusted=True)
            engine = ProjectContextEngine(
                workspace,
                base / "context",
                {
                    "enabled": True,
                    "background_watch_enabled": True,
                    "watch_debounce_ms": 30,
                    "freshness_wait_ms": 2000,
                    "verification_interval_seconds": 30,
                    "tree_sitter_enabled": False,
                    "max_files": 100,
                    "max_file_bytes": 100_000,
                    "max_total_text_bytes": 500_000,
                },
            )
            try:
                engine.start()
                engine.refresh(force=True)
                source.write_text("def fresh_context_value():\n    return 'synchronized'\n", encoding="utf-8")
                engine.mark_dirty(paths={"worker.py"}, reason="unit-test")
                self.assertTrue(engine.wait_until_fresh(timeout_ms=2500))
                self.assertTrue(engine.search("fresh context synchronized"))
                self.assertTrue(engine.status(refresh=False)["background_worker"])
            finally:
                engine.close()

    def test_prompt_capsule_tracks_structure_working_set_and_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "workspace"
            root.mkdir()
            (root / "README.md").write_text("# Payments\nThe service processes invoices.\n", encoding="utf-8")
            (root / "billing.py").write_text(
                "class InvoiceService:\n    def calculate_total(self, amount):\n        return amount\n",
                encoding="utf-8",
            )
            workspace = WorkspaceManager(
                base / "state" / "workspace.json",
                base / "backups",
                {"default_to_process_cwd": False, "protected_path_parts": [".git"]},
            )
            workspace.select(root, source="test", trusted=True)
            engine = ProjectContextEngine(
                workspace,
                base / "context",
                {
                    "enabled": True,
                    "background_watch_enabled": False,
                    "tree_sitter_enabled": False,
                    "max_files": 100,
                    "max_file_bytes": 100_000,
                    "max_total_text_bytes": 500_000,
                    "automatic_context_max_chars": 10_000,
                    "sensitive_file_globs": [".env*", "*secret*"],
                },
            )
            try:
                status = engine.refresh(force=True)
                self.assertGreaterEqual(status["symbols"], 2)
                symbols = engine.search_symbols("InvoiceService")
                self.assertTrue(symbols)
                result = ToolResult("read-1", "read_file", True, "ok", {"path": "billing.py"}, duration_ms=1)
                engine.record_tool_activity("session-a", ToolCall("read-1", "read_file", {"path": "billing.py"}), result)
                engine._working_sets.clear()  # process-memory loss simulation; SQLite must restore it
                capsule = engine.prompt_context("calculate_total nasıl çalışıyor", session_id="session-a")
                self.assertIn("billing.py", capsule["working_set"])
                self.assertTrue(capsule["symbols"])
                self.assertIn(capsule["plan"]["intent"], {"lookup", "architecture"})
                engine.mark_dirty(paths={"billing.py"}, reason="test-change")
                self.assertTrue(engine.status(refresh=False)["dirty"])
                engine.refresh(force=False)
                self.assertFalse(engine.status(refresh=False)["dirty"])
                self.assertTrue(engine.health(integrity_check=True)["store"]["integrity"] == "ok")
            finally:
                engine.close()


if __name__ == "__main__":
    unittest.main()
