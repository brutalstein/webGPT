from __future__ import annotations

import unittest

from os_agent.web.security import LocalWebSecurity


class DummyRequest:
    def __init__(self, cookie: str | None):
        self.cookies = {"os_web_session": cookie} if cookie else {}


class DummyWebSocket:
    def __init__(self, cookie: str | None, origin: str):
        self.cookies = {"os_web_session": cookie} if cookie else {}
        self.headers = {"origin": origin}


class LocalWebSecurityTests(unittest.TestCase):
    def test_launch_token_is_one_time_and_cookie_secret_is_separate(self) -> None:
        security = LocalWebSecurity("127.0.0.1", 8765)
        self.assertNotEqual(security.auth_token, security.session_token)
        self.assertTrue(security.consume_auth_token(security.auth_token))
        self.assertFalse(security.consume_auth_token(security.auth_token))
        self.assertTrue(security.request_authorized(DummyRequest(security.session_token)))
        self.assertFalse(security.request_authorized(DummyRequest(security.auth_token)))

    def test_websocket_requires_cookie_and_exact_local_origin(self) -> None:
        security = LocalWebSecurity("127.0.0.1", 8765)
        allowed = DummyWebSocket(security.session_token, "http://127.0.0.1:8765")
        foreign = DummyWebSocket(security.session_token, "http://evil.example")
        self.assertTrue(security.websocket_authorized(allowed))
        self.assertFalse(security.websocket_authorized(foreign))
