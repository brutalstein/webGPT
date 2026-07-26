from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WebUiRegressionTests(unittest.TestCase):
    def read(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_chat_layout_uses_bounded_dynamic_viewport(self) -> None:
        css = self.read("web/src/styles.css")
        self.assertIn("height: 100dvh", css)
        self.assertIn(".chat-column", css)
        self.assertIn("min-height: 0", css)
        self.assertIn(".message-scroll", css)
        self.assertIn("overflow-y: auto", css)
        self.assertIn("overscroll-behavior: contain", css)

    def test_streaming_scroll_is_user_pinned_not_forced(self) -> None:
        app = self.read("web/src/App.jsx")
        hook = self.read("web/src/hooks/usePinnedScroll.js")
        self.assertIn("usePinnedScroll", app)
        self.assertIn("jump-latest", app)
        self.assertIn("pinnedRef", hook)
        self.assertIn("ResizeObserver", hook)
        self.assertNotIn("scrollIntoView", app)

    def test_websocket_has_heartbeat_and_reconnect_jitter(self) -> None:
        source = self.read("web/src/lib/api.js")
        self.assertIn("heartbeat timeout", source)
        self.assertIn("Math.random()", source)
        self.assertIn("visibilitychange", source)
        self.assertIn("navigator.onLine", source)


    def test_reconnect_resets_event_sequence_by_stream_identity(self) -> None:
        app = self.read("web/src/App.jsx")
        socket = self.read("web/src/lib/api.js")
        events = self.read("src/os_agent/web/events.py")
        backend = self.read("src/os_agent/web/app.py")
        self.assertIn("eventStreamIdRef", app)
        self.assertIn("streamChanged", app)
        self.assertIn("generation += 1", socket)
        self.assertIn("self._stream_id = uuid.uuid4().hex", events)
        self.assertIn('"stream_id": context.hub.stream_id', backend)

    def test_modals_trap_focus_and_support_escape(self) -> None:
        hook = self.read("web/src/hooks/useDialogFocus.js")
        approval = self.read("web/src/components/ApprovalModal.jsx")
        settings = self.read("web/src/components/SettingsModal.jsx")
        self.assertIn("event.key === 'Escape'", hook)
        self.assertIn("event.key !== 'Tab'", hook)
        self.assertIn('role="alertdialog"', approval)
        self.assertIn('aria-modal="true"', settings)

    def test_large_content_has_overflow_protection(self) -> None:
        css = self.read("web/src/styles.css")
        markdown = self.read("web/src/components/MarkdownMessage.jsx")
        self.assertIn("overflow-wrap:anywhere", css)
        self.assertIn("table-scroll", markdown)
        self.assertIn("code-copy", markdown)
        self.assertIn("content-visibility:auto", css)


if __name__ == "__main__":
    unittest.main()

class AgentIntelligenceUiTests(unittest.TestCase):
    def test_skills_and_context_panel_is_wired_to_backend(self) -> None:
        app = (ROOT / "web/src/App.jsx").read_text(encoding="utf-8")
        panel = (ROOT / "web/src/components/SkillsPanel.jsx").read_text(encoding="utf-8")
        backend = (ROOT / "src/os_agent/web/app.py").read_text(encoding="utf-8")
        self.assertIn("SkillsPanel", app)
        self.assertIn("/api/skills", app)
        self.assertIn("skill.activated", app)
        self.assertIn("Project context", panel)
        self.assertIn('"/api/project-context"', backend)
        self.assertIn('"/api/skills"', backend)
