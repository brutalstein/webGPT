from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from os_agent.tools.models import ToolDefinition, ToolRisk
from os_agent.tools.policy import ToolPolicy
from os_agent.tools.protocol import ToolProtocol


class _Registry:
    def validate_arguments(self, name, arguments):
        return None


class _Workspace:
    pass


class ResilientRuntimeTests(unittest.TestCase):
    def test_protocol_repairs_windows_path_escape(self) -> None:
        protocol = ToolProtocol.__new__(ToolProtocol)
        protocol.registry = _Registry()
        protocol.settings = {"max_calls_per_round": 4}
        text = (
            '<os_tool_calls>{"calls":[{"id":"1","name":"run_command","arguments":'
            '{"command":["python","C:\\Users\\cenke\\test.py"]}}]}</os_tool_calls>'
        )
        calls = protocol.parse_calls(text)
        self.assertEqual(calls[0].arguments["command"][1], r"C:\Users\cenke\test.py")
        self.assertTrue(protocol.last_parse_repaired)

    def test_safe_auto_persists_and_only_skips_low_risk_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "execution-policy.json"
            settings = {
                "require_confirmation": True,
                "allowed_executables": ["git", "python", "npm", "pip"],
                "blocked_command_patterns": [r"\brm\s+-rf\b"],
            }
            policy = ToolPolicy(settings, state)
            policy.set_execution_profile("safe_auto")
            reloaded = ToolPolicy(settings, state)
            execute = ToolDefinition(
                name="run_command", title="Terminal", description="",
                input_schema={"type": "object"}, risk=ToolRisk.EXECUTE, idempotent=False,
            )
            self.assertFalse(reloaded.requires_confirmation(execute, {"command": ["git", "status", "--short"]}))
            self.assertFalse(reloaded.requires_confirmation(execute, {"command": ["python", "-m", "unittest"]}))
            self.assertTrue(reloaded.requires_confirmation(execute, {"command": ["pip", "install", "x"]}))
            self.assertTrue(reloaded.requires_confirmation(execute, {"command": ["git", "reset", "--hard"]}))

    def test_alternates_file_is_written_with_binary_lf(self) -> None:
        source = (ROOT / "src/os_agent/capabilities/github.py").read_text(encoding="utf-8")
        self.assertIn("alternates.write_bytes", source)
        self.assertNotIn("alternates.write_text", source)


if __name__ == "__main__":
    unittest.main()
