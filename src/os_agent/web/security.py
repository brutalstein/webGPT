from __future__ import annotations

import hmac
import secrets
import threading
from dataclasses import dataclass, field

from fastapi import Request, WebSocket


@dataclass(slots=True)
class LocalWebSecurity:
    """Tek kullanımlık açılış bileti ve ayrı HttpOnly oturum sırrı."""

    host: str
    port: int
    cookie_name: str = "os_web_session"
    auth_token: str = field(init=False, repr=False)
    session_token: str = field(init=False, repr=False)
    _auth_consumed: bool = field(default=False, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self.auth_token = secrets.token_urlsafe(32)
        self.session_token = secrets.token_urlsafe(32)

    @property
    def auth_url(self) -> str:
        return f"http://{self.host}:{self.port}/auth/{self.auth_token}"

    @staticmethod
    def _matches(value: str | None, expected: str) -> bool:
        return bool(value) and hmac.compare_digest(str(value), expected)

    def consume_auth_token(self, value: str | None) -> bool:
        with self._lock:
            if self._auth_consumed or not self._matches(value, self.auth_token):
                return False
            self._auth_consumed = True
            return True

    def request_authorized(self, request: Request) -> bool:
        return self._matches(request.cookies.get(self.cookie_name), self.session_token)

    def websocket_authorized(self, websocket: WebSocket) -> bool:
        if not self._matches(websocket.cookies.get(self.cookie_name), self.session_token):
            return False
        origin = websocket.headers.get("origin", "")
        allowed = {
            f"http://{self.host}:{self.port}",
            f"http://localhost:{self.port}",
            f"http://127.0.0.1:{self.port}",
        }
        return origin in allowed
