from __future__ import annotations

import asyncio
import dataclasses
import enum
import itertools
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, enum.Enum):
        return value.value
    if dataclasses.is_dataclass(value):
        return _json_safe(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


@dataclass(eq=False, slots=True)
class EventSubscription:
    queue: asyncio.Queue[dict[str, Any]]
    loop: asyncio.AbstractEventLoop


class EventHub:
    """Worker thread'lerinden WebSocket istemcilerine sıralı olay taşır."""

    def __init__(self, *, history_limit: int = 600, queue_size: int = 1000):
        self._history: deque[dict[str, Any]] = deque(maxlen=max(10, history_limit))
        self._queue_size = max(10, queue_size)
        self._subscribers: set[EventSubscription] = set()
        self._lock = threading.RLock()
        self._sequence = itertools.count(1)

    def publish(self, event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        event = {
            "seq": next(self._sequence),
            "type": str(event_type),
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "payload": _json_safe(payload or {}),
        }
        with self._lock:
            self._history.append(event)
            subscribers = tuple(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.loop.call_soon_threadsafe(self._offer, subscriber.queue, event)
            except RuntimeError:
                self.unsubscribe(subscriber)
        return event

    @staticmethod
    def _offer(queue: asyncio.Queue[dict[str, Any]], event: dict[str, Any]) -> None:
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            pass

    def subscribe(self) -> EventSubscription:
        subscription = EventSubscription(
            queue=asyncio.Queue(maxsize=self._queue_size),
            loop=asyncio.get_running_loop(),
        )
        with self._lock:
            self._subscribers.add(subscription)
        return subscription

    def unsubscribe(self, subscription: EventSubscription) -> None:
        with self._lock:
            self._subscribers.discard(subscription)

    def history(self, *, after_seq: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(event) for event in self._history if int(event["seq"]) > after_seq]
