from __future__ import annotations

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
