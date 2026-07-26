from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from os_agent.errors import SkillInstallError, SkillValidationError
from os_agent.skills.github import parse_github_source
from os_agent.skills.manager import SkillManager
from os_agent.skills.parser import parse_skill_directory
from os_agent.tools.workspace import WorkspaceManager


SKILL_TEXT = """---
name: python-review
description: Python kod inceleme, güvenlik ve test görevlerinde kullan.
license: MIT
allowed-tools:
  - read_file
  - search_project_context
metadata:
  domain: python
---
# Python Review

Önce proje bağlamını ara, sonra ilgili dosyaları doğrula.
"""


class AgentSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "workspace"
        self.root.mkdir()
        self.workspace = WorkspaceManager(
            base / "state" / "workspace.json",
            base / "workspace-backups",
            {"default_to_process_cwd": False, "protected_path_parts": [".git"]},
        )
        self.workspace.select(self.root, source="test", trusted=True)
        self.install_root = base / "skills"
        self.quarantine = base / "quarantine"
        self.manager = SkillManager(
            self.workspace,
            self.install_root,
            self.quarantine,
            base / "skill-backups",
            {
                "enabled": True,
                "project_skill_directories": [".agents/skills"],
                "max_skill_body_chars": 20_000,
                "max_resource_bytes": 100_000,
                "inspection_ttl_seconds": 3600,
            },
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _make_skill(self, parent: Path, name: str = "python-review") -> Path:
        root = parent / name
        root.mkdir(parents=True)
        (root / "SKILL.md").write_text(SKILL_TEXT.replace("python-review", name), encoding="utf-8")
        refs = root / "references"
        refs.mkdir()
        (refs / "checklist.md").write_text("- tests\n- typing\n", encoding="utf-8")
        return root

    def test_parser_activation_and_resource_progressive_disclosure(self) -> None:
        root = self._make_skill(self.install_root)
        parsed = parse_skill_directory(root, scope="global")
        self.assertEqual(parsed.name, "python-review")
        self.manager.refresh()
        catalog = self.manager.catalog(session_id="s1")
        self.assertEqual(catalog[0]["name"], "python-review")
        self.assertNotIn("instructions", catalog[0])

        with self.assertRaises(SkillValidationError):
            self.manager.read_resource(
                "python-review", "references/checklist.md", session_id="s1"
            )

        activated = self.manager.activate("python-review", "s1")
        self.assertIn("Önce proje bağlamını ara", activated["instructions"])
        self.assertEqual(self.manager.activated("s1"), ["python-review"])
        resource = self.manager.read_resource(
            "python-review", "references/checklist.md", session_id="s1"
        )
        self.assertIn("typing", resource["content"])
        with self.assertRaises(SkillValidationError):
            self.manager.read_resource(
                "python-review", "../secret.txt", session_id="s1"
            )

    def test_project_skills_require_explicit_workspace_trust(self) -> None:
        project_root = self._make_skill(self.root / ".agents" / "skills", name="project-rules")
        self.manager.refresh()
        self.assertIn("project-rules", [item["name"] for item in self.manager.catalog()])
        self.workspace.select(self.root, source="process_cwd", trusted=False)
        self.manager.refresh()
        self.assertNotIn("project-rules", [item["name"] for item in self.manager.catalog()])
        self.assertTrue(project_root.exists())

    def test_inspected_skill_installs_atomically_with_manifest(self) -> None:
        inspection_id = "a" * 32
        inspection_root = self.quarantine / inspection_id
        repo_root = inspection_root / "repo"
        skill_root = self._make_skill(repo_root / "skills")
        hashes = {}
        for path in skill_root.rglob("*"):
            if path.is_file():
                hashes[path.relative_to(skill_root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        source = {
            "owner": "example",
            "repo": "skills",
            "web_url": "https://github.com/example/skills",
            "clone_url": "https://github.com/example/skills.git",
            "ref": "main",
            "commit": "1" * 40,
            "skill_path": "skills/python-review",
        }
        report = {
            "source": source,
            "skill": {"name": "python-review"},
            "license": {"status": "declared", "value": "MIT"},
            "risk": {"contains_scripts": False, "findings": [], "automatic_script_execution": False},
            "file_hashes": hashes,
        }
        metadata = {
            "inspection_id": inspection_id,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "candidate_relative": skill_root.relative_to(inspection_root).as_posix(),
            "source": source,
            "report": report,
        }
        (inspection_root / "inspection.json").write_text(json.dumps(metadata), encoding="utf-8")

        installed = self.manager.install_inspection(inspection_id)
        self.assertEqual(installed["name"], "python-review")
        manifest = json.loads((self.install_root / "python-review" / ".os-skill.json").read_text())
        self.assertEqual(manifest["source"]["commit"], "1" * 40)
        self.assertFalse(manifest["risk"]["automatic_script_execution"])
        with self.assertRaises(SkillInstallError):
            self.manager.install_inspection(inspection_id)


    def test_install_rejects_quarantine_tampering(self) -> None:
        inspection_id = "b" * 32
        inspection_root = self.quarantine / inspection_id
        skill_root = self._make_skill(inspection_root / "repo")
        expected = {
            path.relative_to(skill_root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in skill_root.rglob("*") if path.is_file()
        }
        source = {"commit": "2" * 40, "skill_path": "python-review"}
        report = {
            "source": source,
            "license": {"status": "declared", "value": "MIT"},
            "risk": {"contains_scripts": False, "findings": [], "automatic_script_execution": False},
            "file_hashes": expected,
        }
        (inspection_root / "inspection.json").write_text(json.dumps({
            "inspection_id": inspection_id,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "candidate_relative": skill_root.relative_to(inspection_root).as_posix(),
            "source": source,
            "report": report,
        }), encoding="utf-8")
        (skill_root / "SKILL.md").write_text(SKILL_TEXT + "\nmalicious change", encoding="utf-8")
        with self.assertRaises(SkillInstallError):
            self.manager.install_inspection(inspection_id)
        self.assertFalse((self.install_root / "python-review").exists())

    def test_github_source_rejects_non_github_and_parses_tree(self) -> None:
        parsed = parse_github_source("https://github.com/acme/skills/tree/main/python-review")
        self.assertEqual(parsed["ref"], "main")
        self.assertEqual(parsed["skill_path"], "python-review")
        blob = parse_github_source("https://github.com/acme/skills/blob/main/python-review/SKILL.md")
        self.assertEqual(blob["skill_path"], "python-review")
        with self.assertRaises(SkillInstallError):
            parse_github_source("https://gitlab.com/acme/skills")
        with self.assertRaises(SkillInstallError):
            parse_github_source("https://github.com/acme/skills", ref="--upload-pack=evil")

    def test_git_tree_preflight_discovers_multiple_skills_and_rejects_symlink(self) -> None:
        source_repo = Path(self.temp.name) / "source-repo"
        source_repo.mkdir()
        commands = [
            ["git", "init", "--quiet"],
            ["git", "config", "user.email", "tests@example.com"],
            ["git", "config", "user.name", "OS Tests"],
        ]
        for command in commands:
            subprocess.run(command, cwd=source_repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self._make_skill(source_repo / "skills", "python-review")
        self._make_skill(source_repo / "skills", "project-rules")
        (source_repo / "LICENSE").write_text("MIT License\n", encoding="utf-8")
        link = source_repo / "skills" / "python-review" / "references" / "outside-link"
        try:
            os.symlink("../checklist.md", link)
        except (OSError, NotImplementedError):
            self.skipTest("Symlink oluşturma desteklenmiyor")
        subprocess.run(["git", "add", "."], cwd=source_repo, check=True)
        subprocess.run(["git", "commit", "--quiet", "-m", "skills"], cwd=source_repo, check=True)
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=source_repo, check=True, text=True, stdout=subprocess.PIPE
        ).stdout.strip()

        checkout = Path(self.temp.name) / "fetched"
        source = {"clone_url": source_repo.as_uri(), "ref": commit}
        inspector = self.manager.inspector
        inspector._fetch_commit(source, commit, checkout)
        with self.assertRaises(SkillValidationError):
            inspector._discover_skill_path(checkout, "")
        selected = inspector._discover_skill_path(checkout, "skills/python-review")
        self.assertEqual(selected, "skills/python-review")
        with self.assertRaises(SkillValidationError):
            inspector._preflight_tree(checkout, selected)


if __name__ == "__main__":
    unittest.main()
