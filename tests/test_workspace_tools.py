from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from os_agent.errors import ToolPolicyError, WorkspaceError
from os_agent.tools.audit import ToolAuditLog
from os_agent.tools.builtins.filesystem import register_filesystem_tools
from os_agent.tools.builtins.process import register_process_tools
from os_agent.tools.executor import ToolExecutor
from os_agent.tools.models import ApprovalDecision, ToolCall
from os_agent.tools.policy import ToolPolicy
from os_agent.tools.registry import ToolRegistry
from os_agent.tools.workspace import WorkspaceManager


SETTINGS = {
    "default_to_process_cwd": False,
    "backup_writes": True,
    "protected_path_parts": [".git"],
    "ignored_directories": [".git", "__pycache__"],
    "max_file_bytes": 100_000,
    "max_tool_result_chars": 20_000,
    "require_confirmation": True,
    "allowed_tools": [
        "workspace_info",
        "list_directory",
        "read_file",
        "search_text",
        "write_file",
        "append_file",
        "replace_text",
        "create_directory",
        "run_command",
        "git_status",
    ],
    "allowed_executables": ["python", "python.exe", "git", "git.exe"],
    "blocked_command_patterns": [r"\bgit\s+reset\s+--hard\b"],
    "command_timeout_seconds": 10,
    "command_output_chars": 10_000,
}


class WorkspaceToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "workspace"
        self.root.mkdir()
        self.state = Path(self.temp.name) / "state" / "workspace.json"
        self.backups = Path(self.temp.name) / "backups"
        self.workspace = WorkspaceManager(self.state, self.backups, dict(SETTINGS))
        self.workspace.select(self.root)
        self.registry = ToolRegistry()
        register_filesystem_tools(self.registry)
        register_process_tools(self.registry)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_workspace_persists_and_blocks_escape(self) -> None:
        restored = WorkspaceManager(self.state, self.backups, dict(SETTINGS))
        self.assertEqual(restored.root, self.root.resolve())
        with self.assertRaises(WorkspaceError):
            restored.resolve("../outside.txt", for_write=True)

    def test_symlink_escape_is_blocked_when_supported(self) -> None:
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        link = self.root / "outside-link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Bu ortam sembolik bağlantı oluşturmaya izin vermiyor.")
        with self.assertRaises(WorkspaceError):
            self.workspace.resolve("outside-link/secret.txt", for_write=True)

    def test_write_read_replace_and_backup(self) -> None:
        policy = ToolPolicy(dict(SETTINGS))
        executor = ToolExecutor(
            self.registry,
            self.workspace,
            policy,
            ToolAuditLog(Path(self.temp.name) / "audit.jsonl"),
            dict(SETTINGS),
        )
        executor.approval_handler = lambda request: ApprovalDecision(approved=True)

        created = executor.execute(
            ToolCall("1", "write_file", {"path": "hello.txt", "content": "merhaba", "overwrite": False}),
            "session-a",
        )
        self.assertTrue(created.ok)
        self.assertEqual((self.root / "hello.txt").read_text(), "merhaba")

        changed = executor.execute(
            ToolCall(
                "2",
                "replace_text",
                {
                    "path": "hello.txt",
                    "old_text": "merhaba",
                    "new_text": "selam",
                    "expected_replacements": 1,
                    "replace_all": False,
                },
            ),
            "session-a",
        )
        self.assertTrue(changed.ok)
        self.assertEqual((self.root / "hello.txt").read_text(), "selam")
        self.assertTrue(any(self.backups.rglob("hello.txt")))

        read = executor.execute(
            ToolCall("3", "read_file", {"path": "hello.txt"}),
            "session-a",
        )
        self.assertTrue(read.ok)
        self.assertIn("selam", read.content)

    def test_write_requires_approval(self) -> None:
        executor = ToolExecutor(
            self.registry,
            self.workspace,
            ToolPolicy(dict(SETTINGS)),
            ToolAuditLog(Path(self.temp.name) / "audit.jsonl"),
            dict(SETTINGS),
        )
        denied = executor.execute(
            ToolCall("deny", "write_file", {"path": "x.txt", "content": "x"}),
            "session-a",
        )
        self.assertFalse(denied.ok)
        self.assertFalse((self.root / "x.txt").exists())

    def test_command_policy_blocks_destructive_git(self) -> None:
        with self.assertRaises(ToolPolicyError):
            ToolPolicy(dict(SETTINGS)).validate_command(["git", "reset", "--hard"])

    def test_command_policy_rejects_explicit_executable_path(self) -> None:
        with self.assertRaises(ToolPolicyError):
            ToolPolicy(dict(SETTINGS)).validate_command(["./python", "-V"])

    def test_run_command_uses_argument_list_without_shell(self) -> None:
        executor = ToolExecutor(
            self.registry,
            self.workspace,
            ToolPolicy(dict(SETTINGS)),
            ToolAuditLog(Path(self.temp.name) / "audit.jsonl"),
            dict(SETTINGS),
        )
        executor.approval_handler = lambda request: ApprovalDecision(approved=True)
        result = executor.execute(
            ToolCall(
                "cmd",
                "run_command",
                {"command": ["python", "-c", "print('tool-ok')"]},
            ),
            "session-a",
        )
        self.assertTrue(result.ok, result.content)
        self.assertIn("tool-ok", result.content)


if __name__ == "__main__":
    unittest.main()
