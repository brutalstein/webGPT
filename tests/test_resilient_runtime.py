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

from os_agent.errors import ToolProtocolError
from os_agent.models import ProviderResponse
from os_agent.tools.agent import GeminiToolAgent
from os_agent.tools.models import ToolCall, ToolDefinition, ToolResult, ToolRisk
from os_agent.tools.policy import ToolPolicy
from os_agent.tools.protocol import ToolProtocol


class _Registry:
    def validate_arguments(self, name, arguments):
        return None


class _RejectingRegistry:
    def validate_arguments(self, name, arguments):
        raise ValueError("missing required path")


class _Workspace:
    active = False

    def describe(self):
        return {"active": False}


class _AgentProtocol:
    def __init__(self, calls_by_text):
        self.calls_by_text = calls_by_text
        self.workspace = _Workspace()
        self.project_context = None
        self.skills = None
        self.last_parse_repaired = False
        self.result_batches = []

    @staticmethod
    def workspace_preflight(user_prompt):
        return []

    @staticmethod
    def initial_prompt(user_prompt, observations, *, session_id=None):
        return "INITIAL"

    def parse_calls(self, text):
        return self.calls_by_text.get(text)

    def results_prompt(self, results, *, recovery=None):
        self.result_batches.append(results)
        return "RESULTS"

    @staticmethod
    def recovery_prompt(recovery):
        return "RECOVERY"

    @staticmethod
    def exhaustion_prompt(recovery):
        return "EXHAUSTION"


class _Executor:
    def __init__(self, handler):
        self.handler = handler
        self.services = {}
        self.executed = []

    def reset_run(self):
        self.executed.clear()

    def execute_many(self, calls, session_id):
        return [self.execute(call, session_id) for call in calls]

    def execute(self, call, session_id):
        self.executed.append(call)
        return self.handler(call, len(self.executed))


def _response(text):
    return ProviderResponse(text=text, provider="gemini", conversation_id="conversation")


def _sender_sequence(*texts):
    iterator = iter(texts)

    def sender(prompt, session_id):
        return _response(next(iterator))

    return sender


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

    def test_protocol_wraps_registry_validation_as_repairable_error(self) -> None:
        protocol = ToolProtocol.__new__(ToolProtocol)
        protocol.registry = _RejectingRegistry()
        protocol.settings = {"max_calls_per_round": 4}
        text = '<os_tool_calls>{"calls":[{"id":"1","name":"read_file","arguments":{}}]}</os_tool_calls>'
        with self.assertRaises(ToolProtocolError) as raised:
            protocol.parse_calls(text)
        self.assertIn("argümanları geçersiz", str(raised.exception))

    def test_results_prompt_demands_corrective_progress(self) -> None:
        result = ToolResult(call_id="1", name="run_command", ok=False, content="failed", error="failed")
        prompt = ToolProtocol.results_prompt(
            [result],
            recovery={"blocked_call_signatures": ["deadbeef"]},
        )
        self.assertIn("SELF-HEALING", prompt)
        self.assertIn("değişmeden tekrarlama", prompt)
        self.assertIn("deadbeef", prompt)

    def test_identical_failed_call_is_blocked_without_reexecution(self) -> None:
        first = ToolCall(call_id="first", name="run_command", arguments={"command": ["pytest"]})
        repeated = ToolCall(call_id="second", name="run_command", arguments={"command": ["pytest"]})
        protocol = _AgentProtocol({"CALL-1": [first], "CALL-2": [repeated], "DONE": None})
        executor = _Executor(
            lambda call, count: ToolResult(
                call_id=call.call_id,
                name=call.name,
                ok=False,
                content="tests failed",
                error="ProcessError: tests failed",
            )
        )
        agent = GeminiToolAgent(
            protocol,
            executor,
            {
                "max_agent_rounds": 1,
                "max_agent_attempts": 8,
                "max_same_failure_repeats": 1,
                "max_stalled_rounds": 3,
                "max_recovery_cycles": 2,
            },
        )
        result = agent.run(
            _sender_sequence("CALL-1", "CALL-2", "DONE"),
            "testleri düzelt",
            "session",
        )
        self.assertEqual(result.text, "DONE")
        self.assertEqual(len(executor.executed), 1)
        self.assertTrue(any(item.get("loop_guard", {}).get("blocked") for item in result.metadata["tool_trace"]))
        self.assertEqual(result.metadata["recovery_cycles"], 1)

    def test_failed_verification_can_run_again_after_successful_fix(self) -> None:
        verify_one = ToolCall(call_id="verify-1", name="run_command", arguments={"command": ["pytest"]})
        fix = ToolCall(call_id="fix", name="replace_text", arguments={"path": "x.py"})
        verify_two = ToolCall(call_id="verify-2", name="run_command", arguments={"command": ["pytest"]})
        protocol = _AgentProtocol(
            {
                "VERIFY-1": [verify_one],
                "FIX": [fix],
                "VERIFY-2": [verify_two],
                "DONE": None,
            }
        )
        verification_count = 0

        def handler(call, count):
            nonlocal verification_count
            if call.name == "replace_text":
                return ToolResult(call_id=call.call_id, name=call.name, ok=True, content="fixed")
            verification_count += 1
            return ToolResult(
                call_id=call.call_id,
                name=call.name,
                ok=verification_count == 2,
                content="passed" if verification_count == 2 else "failed",
                error=None if verification_count == 2 else "ProcessError: failed",
            )

        executor = _Executor(handler)
        agent = GeminiToolAgent(
            protocol,
            executor,
            {"max_agent_rounds": 2, "max_agent_attempts": 8, "max_recovery_cycles": 1},
        )
        result = agent.run(
            _sender_sequence("VERIFY-1", "FIX", "VERIFY-2", "DONE"),
            "düzelt ve test et",
            "session",
        )
        self.assertEqual(result.text, "DONE")
        self.assertEqual(verification_count, 2)
        self.assertEqual(len(executor.executed), 3)

    def test_hard_limit_returns_safe_summary_instead_of_loop_exception(self) -> None:
        first = ToolCall(call_id="one", name="read_file", arguments={"path": "a"})
        second = ToolCall(call_id="two", name="read_file", arguments={"path": "b"})
        protocol = _AgentProtocol({"ONE": [first], "TWO": [second]})
        executor = _Executor(
            lambda call, count: ToolResult(
                call_id=call.call_id,
                name=call.name,
                ok=False,
                content=f"missing {count}",
                error=f"FileNotFoundError: missing {count}",
            )
        )
        agent = GeminiToolAgent(
            protocol,
            executor,
            {
                "max_agent_rounds": 1,
                "max_agent_attempts": 2,
                "max_stalled_rounds": 99,
                "max_recovery_cycles": 0,
            },
        )
        result = agent.run(
            _sender_sequence("ONE", "TWO", "SAFE SUMMARY"),
            "dosyaları bul",
            "session",
        )
        self.assertEqual(result.text, "SAFE SUMMARY")
        self.assertEqual(result.metadata["termination_reason"], "hard_attempt_limit")
        self.assertEqual(result.metadata["tool_runtime"], "self_healing_stopped")

    def test_duplicate_calls_in_one_batch_execute_once(self) -> None:
        first = ToolCall(call_id="one", name="read_file", arguments={"path": "same.py"})
        duplicate = ToolCall(call_id="two", name="read_file", arguments={"path": "same.py"})
        protocol = _AgentProtocol({"BATCH": [first, duplicate], "DONE": None})
        executor = _Executor(
            lambda call, count: ToolResult(
                call_id=call.call_id,
                name=call.name,
                ok=True,
                content="content",
            )
        )
        agent = GeminiToolAgent(
            protocol,
            executor,
            {
                "max_agent_rounds": 1,
                "max_agent_attempts": 4,
                "max_same_failure_repeats": 99,
            },
        )
        result = agent.run(_sender_sequence("BATCH", "DONE"), "dosyayı oku", "session")
        self.assertEqual(result.text, "DONE")
        self.assertEqual(len(executor.executed), 1)
        self.assertEqual(
            protocol.result_batches[0][1].structured["loop_guard"]["reason"],
            "duplicate_call_in_batch",
        )

    def test_safe_stop_survives_final_provider_failure(self) -> None:
        first = ToolCall(call_id="one", name="read_file", arguments={"path": "a"})
        second = ToolCall(call_id="two", name="read_file", arguments={"path": "b"})
        protocol = _AgentProtocol({"ONE": [first], "TWO": [second]})
        executor = _Executor(
            lambda call, count: ToolResult(
                call_id=call.call_id,
                name=call.name,
                ok=False,
                content="missing",
                error="FileNotFoundError: missing",
            )
        )
        responses = iter(["ONE", "TWO"])

        def sender(prompt, session_id):
            try:
                return _response(next(responses))
            except StopIteration as exc:
                raise RuntimeError("provider unavailable") from exc

        agent = GeminiToolAgent(
            protocol,
            executor,
            {
                "max_agent_rounds": 1,
                "max_agent_attempts": 2,
                "max_stalled_rounds": 99,
                "max_recovery_cycles": 0,
            },
        )
        result = agent.run(sender, "dosyaları bul", "session")
        self.assertIn("güvenli biçimde durduruldu", result.text)
        self.assertEqual(result.provider, "gemini")
        self.assertEqual(result.metadata["termination_reason"], "hard_attempt_limit")

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
