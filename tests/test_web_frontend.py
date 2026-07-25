from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from os_agent.web.frontend import FrontendBuilder


class FrontendBuilderTests(unittest.TestCase):
    def test_source_and_dependency_hashes_are_independent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "web"
            (source / "src").mkdir(parents=True)
            (source / "dist").mkdir()
            (source / "node_modules").mkdir()
            (source / "package.json").write_text('{"name":"test"}', encoding="utf-8")
            (source / "src" / "App.jsx").write_text("export default 1", encoding="utf-8")
            (source / "dist" / "index.html").write_text("ok", encoding="utf-8")

            builder = FrontendBuilder(root, {})
            builder.dependencies_marker.write_text(builder._dependencies_hash(), encoding="utf-8")
            builder.build_marker.write_text(builder._source_hash(), encoding="utf-8")
            self.assertTrue(builder.dependencies_ready())
            self.assertTrue(builder.ready())

            (source / "src" / "App.jsx").write_text("export default 2", encoding="utf-8")
            self.assertTrue(builder.dependencies_ready())
            self.assertFalse(builder.ready())

    def test_dependency_readiness_rejects_partial_or_wrong_install(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "web"
            source.mkdir(parents=True)
            (source / "node_modules").mkdir()
            (source / "package.json").write_text(
                json.dumps({"dependencies": {"lucide-react": "0.469.0"}}),
                encoding="utf-8",
            )
            builder = FrontendBuilder(root, {})
            builder.dependencies_marker.write_text(builder._dependencies_hash(), encoding="utf-8")
            self.assertFalse(builder.dependencies_ready())

            package_dir = source / "node_modules" / "lucide-react"
            package_dir.mkdir(parents=True)
            (package_dir / "package.json").write_text('{"version":"0.468.0"}', encoding="utf-8")
            self.assertFalse(builder.dependencies_ready())

            (package_dir / "package.json").write_text('{"version":"0.469.0"}', encoding="utf-8")
            self.assertTrue(builder.dependencies_ready())

    def test_retryable_registry_failures_are_classified(self) -> None:
        self.assertTrue(FrontendBuilder._retryable_install_failure("npm ERR! code ETARGET\nNo matching version"))
        self.assertTrue(FrontendBuilder._retryable_install_failure("npm ERR! code EAI_AGAIN"))
        self.assertFalse(FrontendBuilder._retryable_install_failure("npm ERR! code ERESOLVE"))

    def test_install_command_forces_online_metadata_and_isolated_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "web").mkdir()
            builder = FrontendBuilder(root, {})
            command = builder._install_command("npm", registry="https://registry.npmjs.org/")
            self.assertIn("--prefer-online", command)
            self.assertNotIn("--prefer-offline", command)
            self.assertIn("--registry=https://registry.npmjs.org/", command)
            self.assertTrue(any(item.startswith("--cache=") for item in command))

    def test_partial_install_cleanup_preserves_source_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "web"
            node_modules = source / "node_modules"
            node_modules.mkdir(parents=True)
            (node_modules / "partial.txt").write_text("partial", encoding="utf-8")
            (source / "package.json").write_text("{}", encoding="utf-8")
            builder = FrontendBuilder(root, {})
            builder.dependencies_marker.write_text("stale", encoding="utf-8")
            builder.npm_cache_dir = root / "npm-cache"
            builder.npm_cache_dir.mkdir()
            (builder.npm_cache_dir / "stale.txt").write_text("stale", encoding="utf-8")

            builder._reset_partial_install(clear_cache=True)

            self.assertFalse(node_modules.exists())
            self.assertFalse(builder.dependencies_marker.exists())
            self.assertFalse(builder.npm_cache_dir.exists())
            self.assertTrue((source / "package.json").is_file())
