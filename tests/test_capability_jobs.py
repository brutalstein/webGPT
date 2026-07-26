from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from os_agent.capabilities.adapters import AdapterRegistry
from os_agent.capabilities.github import GitHubCapabilityInspector
from os_agent.capabilities.jobs import CapabilityJobRuntime
from os_agent.capabilities.process import CapabilityProcessRunner


ROOT = Path(__file__).resolve().parents[1]


class CapabilityJobRuntimeTests(unittest.TestCase):
    def test_job_environment_is_project_independent_and_cache_is_shared(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            extension_root = Path(directory) / "extensions"
            runtime = CapabilityJobRuntime(extension_root, {"job_success_cleanup": False})
            first = runtime.create("github-inspection", {"source": "example"})
            second = runtime.create("capability-install", {"name": "demo"})
            first_env = first.environment()
            second_env = second.environment()

            self.assertNotEqual(first.root, second.root)
            self.assertEqual(first_env["UV_CACHE_DIR"], second_env["UV_CACHE_DIR"])
            self.assertEqual(first_env["PIP_CACHE_DIR"], second_env["PIP_CACHE_DIR"])
            self.assertTrue(Path(first_env["HOME"]).is_relative_to(first.root))
            self.assertTrue(Path(first_env["TEMP"]).is_relative_to(first.root))
            self.assertEqual(first_env["GIT_TERMINAL_PROMPT"], "0")
            self.assertTrue(Path(first_env["GIT_CONFIG_GLOBAL"]).is_file())
            self.assertEqual(first_env["UV_PYTHON_DOWNLOADS"], "never")
            self.assertEqual(first_env["UV_SYSTEM_CERTS"], "true")

            runtime.complete(first)
            runtime.complete(second)
            self.assertTrue(first.root.exists())
            self.assertEqual(runtime.status()["active_count"], 0)

    def test_failed_job_is_retained_with_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = CapabilityJobRuntime(Path(directory) / "extensions", {})
            job = runtime.create("test")
            runtime.fail(job, RuntimeError("boom"))
            self.assertTrue(job.manifest.is_file())
            self.assertIn('"status": "failed"', job.manifest.read_text(encoding="utf-8"))
            self.assertIn("boom", job.manifest.read_text(encoding="utf-8"))

    def test_cache_lock_is_exclusive_and_released(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = CapabilityJobRuntime(Path(directory) / "extensions", {})
            with runtime.cache_lock("https://github.com/example/repo.git"):
                locks = list(runtime.lock_root.glob("*.lock"))
                self.assertEqual(len(locks), 1)
            self.assertEqual(list(runtime.lock_root.glob("*.lock")), [])

    @unittest.skipUnless(shutil.which("git"), "git executable gerekli")
    def test_local_git_cache_checkout_is_commit_pinned_and_reused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            upstream = root / "upstream"
            upstream.mkdir()
            subprocess.run(["git", "init", "--quiet"], cwd=upstream, check=True)
            subprocess.run(["git", "config", "user.email", "tests@example.invalid"], cwd=upstream, check=True)
            subprocess.run(["git", "config", "user.name", "OS tests"], cwd=upstream, check=True)
            (upstream / "pyproject.toml").write_text(
                '[project]\nname="demo-cli"\nversion="1.0.0"\n[project.scripts]\ndemo="demo:main"\n',
                encoding="utf-8",
            )
            (upstream / "demo.py").write_text("def main(): return 0\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=upstream, check=True)
            subprocess.run(["git", "commit", "--quiet", "-m", "initial"], cwd=upstream, check=True)
            commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=upstream, text=True).strip()

            settings = {
                "job_success_cleanup": False,
                "git_fetch_retries": 1,
                "max_repository_file_bytes": 1_048_576,
            }
            runtime = CapabilityJobRuntime(root / "extensions", settings)
            inspector = GitHubCapabilityInspector(
                runtime.extension_root / "quarantine",
                settings,
                AdapterRegistry(),
                runner=CapabilityProcessRunner(settings),
                jobs=runtime,
            )
            source = {"clone_url": str(upstream)}

            first = runtime.create("git-cache-test")
            cache, cache_hit = inspector._fetch(source, commit, first.work / "repo", first)
            self.assertFalse(cache_hit)
            self.assertEqual(inspector._preflight_tree(cache, commit, first)["file_count"], 2)
            inspector._checkout(first.work / "repo", commit, first)
            self.assertTrue((first.work / "repo" / "pyproject.toml").is_file())
            self.assertFalse((first.work / "repo" / ".git").exists())
            runtime.complete(first)

            second = runtime.create("git-cache-test")
            _, second_hit = inspector._fetch(source, commit, second.work / "repo", second)
            self.assertTrue(second_hit)
            runtime.complete(second)

    def test_source_contract_uses_bounded_cache_and_uv_smoke_tests(self) -> None:
        github_source = (ROOT / "src/os_agent/capabilities/github.py").read_text(encoding="utf-8")
        manager_source = (ROOT / "src/os_agent/capabilities/manager.py").read_text(encoding="utf-8")
        process_source = (ROOT / "src/os_agent/capabilities/process.py").read_text(encoding="utf-8")
        protocol_source = (ROOT / "src/os_agent/tools/protocol.py").read_text(encoding="utf-8")
        capability_tools = (ROOT / "src/os_agent/tools/builtins/capabilities.py").read_text(encoding="utf-8")
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")

        self.assertIn("blob:limit=", github_source)
        self.assertNotIn('"--filter=blob:none"', github_source)
        self.assertIn('"objects" / "info" / "alternates"', github_source)
        self.assertIn("git_fetch_retries", github_source)
        self.assertIn('str(uv), "pip", "install"', manager_source)
        self.assertIn('"compileall"', manager_source)
        self.assertIn('"--help"', manager_source)
        self.assertIn("JOB_OBJECT_LIMIT_JOB_MEMORY", process_source)
        self.assertIn("manuel git clone", protocol_source)
        self.assertIn('name = str(arguments.get("name", "")).strip()', capability_tools)
        self.assertIn("Capability job runtime", capability_tools)
        self.assertIn("uv>=0.10.8,<1", requirements)


if __name__ == "__main__":
    unittest.main()
