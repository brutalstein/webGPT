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

    def test_assistant_messages_use_gfm_and_syntax_highlighting(self) -> None:
        component = self.read("web/src/components/MarkdownMessage.jsx")
        package = self.read("web/package.json")
        protocol = self.read("src/os_agent/tools/protocol.py")
        self.assertIn("remarkGfm", component)
        self.assertIn("rehypeHighlight", component)
        self.assertIn('"remark-gfm": "4.0.1"', package)
        self.assertIn('"rehype-highlight": "7.0.2"', package)
        self.assertIn("temiz Markdown", protocol)
        self.assertNotIn("rehype-raw", component)

    def test_interactive_controls_have_explicit_async_and_connection_guards(self) -> None:
        app = self.read("web/src/App.jsx")
        composer = self.read("web/src/components/Composer.jsx")
        sidebar = self.read("web/src/components/Sidebar.jsx")
        approval = self.read("web/src/components/ApprovalModal.jsx")
        api_source = self.read("web/src/lib/api.js")
        self.assertIn("sessionTransition", app)
        self.assertIn("workspaceSelecting", app)
        self.assertIn("cancelPending", app)
        self.assertIn("setValue((current) => current.trim() ? current : prompt)", composer)
        self.assertIn("sessionActionsBlocked", sidebar)
        self.assertIn("resolving || !connected", approval)
        self.assertIn("DEFAULT_REQUEST_TIMEOUT_MS", api_source)

    def test_empty_files_can_be_copied_and_copy_failures_are_visible(self) -> None:
        workspace = self.read("web/src/components/WorkspacePanel.jsx")
        markdown = self.read("web/src/components/MarkdownMessage.jsx")
        self.assertIn("typeof selectedFile.content !== 'string'", workspace)
        self.assertNotIn("if (!selectedFile?.content) return", workspace)
        self.assertIn("copyState === 'error'", workspace)
        self.assertIn("copyState === 'error'", markdown)

    def test_global_capabilities_are_visible_in_web_intelligence_panel(self) -> None:
        app = self.read("web/src/App.jsx")
        panel = self.read("web/src/components/SkillsPanel.jsx")
        backend = self.read("src/os_agent/web/app.py")
        self.assertIn("/api/capabilities", app)
        self.assertIn("Global capabilities", panel)
        self.assertIn('"/api/capabilities"', backend)

    def test_render_errors_and_stacked_modals_have_safe_recovery(self) -> None:
        main = self.read("web/src/main.jsx")
        boundary = self.read("web/src/components/ErrorBoundary.jsx")
        dialog = self.read("web/src/hooks/useDialogFocus.js")
        self.assertIn("ErrorBoundary", main)
        self.assertIn("window.location.reload()", boundary)
        self.assertIn("modalStack", dialog)
        self.assertIn("bodyLockCount", dialog)

    def test_sidebar_can_collapse_and_sessions_can_be_deleted(self) -> None:
        app = self.read("web/src/App.jsx")
        sidebar = self.read("web/src/components/Sidebar.jsx")
        modal = self.read("web/src/components/DeleteSessionModal.jsx")
        css = self.read("web/src/styles.css")
        backend = self.read("src/os_agent/web/app.py")
        worker = self.read("src/os_agent/web/worker.py")
        self.assertIn("sidebarCollapsed", app)
        self.assertIn("sidebar-collapsed", css)
        self.assertNotIn("sidebar-close mobile-only", sidebar)
        self.assertIn("onDeleteSession", sidebar)
        self.assertIn("role="alertdialog"", modal)
        self.assertIn("method: 'DELETE'", app)
        self.assertIn('@app.delete("/api/sessions/{session_id}")', backend)
        self.assertIn("def _delete_session_sync", worker)

    def test_project_brain_exposes_continuous_structural_health(self) -> None:
        runtime = self.read("src/os_agent/tools/runtime.py")
        engine = self.read("src/os_agent/context/engine.py")
        config = self.read("config.json")
        self.assertIn("self.project_context.start()", runtime)
        self.assertIn("ProjectFileWatcher", engine)
        self.assertIn("ContextIndexStore", engine)
        self.assertIn("search_project_symbols", config)
        self.assertIn("project_impact", config)


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
