from __future__ import annotations

import threading
import time
import unittest

from os_agent.tools.models import ApprovalRequest, ToolCall, ToolDefinition, ToolRisk
from os_agent.web.approval import WebApprovalHandler
from os_agent.web.events import EventHub


class WebApprovalTests(unittest.TestCase):
    def test_browser_resolution_releases_blocked_worker(self) -> None:
        hub = EventHub()
        handler = WebApprovalHandler(hub, timeout_seconds=5)
        request = ApprovalRequest(
            call=ToolCall("call-1", "write_file", {"path": "a.txt", "content": "secret"}),
            definition=ToolDefinition(
                name="write_file",
                title="Dosya yaz",
                description="test",
                input_schema={"type": "object"},
                risk=ToolRisk.WRITE,
            ),
            summary="a.txt dosyasını yaz",
        )
        result = []
        worker = threading.Thread(target=lambda: result.append(handler(request)), daemon=True)
        worker.start()

        deadline = time.monotonic() + 2
        while not handler.snapshot() and time.monotonic() < deadline:
            time.sleep(0.01)
        pending = handler.snapshot()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["arguments"]["content"]["redacted"], True)
        self.assertTrue(handler.resolve(pending[0]["approval_id"], approved=True, remember_for_session=True))

        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertTrue(result[0].approved)
        self.assertTrue(result[0].remember_for_session)
