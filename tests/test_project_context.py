from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from os_agent.context import ProjectContextEngine
from os_agent.tools.workspace import WorkspaceManager


class ProjectContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "workspace"
        self.root.mkdir()
        self.workspace = WorkspaceManager(
            base / "state" / "workspace.json",
            base / "backups",
            {"default_to_process_cwd": False, "protected_path_parts": [".git"]},
        )
        self.workspace.select(self.root, source="test", trusted=True)
        self.engine = ProjectContextEngine(
            self.workspace,
            base / "context-cache",
            {
                "enabled": True,
                "max_files": 100,
                "max_file_bytes": 100_000,
                "max_total_text_bytes": 500_000,
                "chunk_chars": 300,
                "chunk_overlap_chars": 40,
                "retrieval_hits": 5,
                "automatic_retrieval_hits": 3,
                "ignored_directories": [".git", "node_modules"],
                "sensitive_file_globs": [".env*", "*secret*"],
            },
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_builds_incremental_context_and_retrieves_relevant_lines(self) -> None:
        (self.root / "README.md").write_text("# Demo\nBu proje lidar sensor fusion yapar.\n", encoding="utf-8")
        (self.root / "AGENTS.md").write_text("Tüm Python değişikliklerinden sonra testleri çalıştır.\n", encoding="utf-8")
        src = self.root / "src"
        src.mkdir()
        (src / "fusion.py").write_text(
            "def fuse_lidar_camera():\n    # Kalman sensor fusion pipeline\n    return 'fused'\n",
            encoding="utf-8",
        )
        first = self.engine.refresh(force=True)
        self.assertTrue(first["indexed"])
        self.assertEqual(first["file_count"], 3)
        hits = self.engine.search("Kalman lidar fusion", limit=3)
        self.assertTrue(hits)
        self.assertEqual(hits[0].path, "src/fusion.py")
        prompt = self.engine.prompt_context("lidar fusion nasıl çalışıyor")
        self.assertIn("Manifestler", prompt["brief"])
        self.assertTrue(prompt["hits"])
        unrelated = self.engine.prompt_context("veritabanı bağlantısını açıkla")
        self.assertTrue(any(item["path"] == "AGENTS.md" for item in unrelated["foundations"]))

        second = self.engine.refresh(force=True)
        self.assertGreaterEqual(second["reused"], 3)


    def test_sensitive_files_are_not_persisted_in_context_cache(self) -> None:
        (self.root / ".env").write_text("API_KEY=do-not-index", encoding="utf-8")
        (self.root / "app.py").write_text("print('safe')", encoding="utf-8")
        self.engine.refresh(force=True)
        self.assertFalse(self.engine.search("do-not-index"))
        cache_bytes = next((Path(self.temp.name) / "context-cache").glob("*.json.gz")).read_bytes()
        self.assertNotIn(b"do-not-index", cache_bytes)

    def test_marks_dirty_after_workspace_change_and_respects_limits(self) -> None:
        (self.root / "main.py").write_text("print('hello')\n", encoding="utf-8")
        self.engine.refresh(force=True)
        self.engine.mark_dirty()
        status = self.engine.status(refresh=False)
        self.assertTrue(status["dirty"])
        self.engine.refresh(force=False)
        self.assertFalse(self.engine.status(refresh=False)["dirty"])


if __name__ == "__main__":
    unittest.main()
