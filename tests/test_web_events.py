from __future__ import annotations

import asyncio
import threading
import unittest

from os_agent.web.events import EventHub


class WebEventHubTests(unittest.IsolatedAsyncioTestCase):
    async def test_cross_thread_publish_reaches_async_subscriber(self) -> None:
        hub = EventHub(history_limit=10, queue_size=10)
        subscription = hub.subscribe()

        thread = threading.Thread(
            target=lambda: hub.publish("tool.started", {"path": "README.md"}),
            daemon=True,
        )
        thread.start()
        event = await asyncio.wait_for(subscription.queue.get(), timeout=2)
        thread.join(timeout=1)

        self.assertEqual(event["type"], "tool.started")
        self.assertEqual(event["payload"]["path"], "README.md")
        self.assertEqual(hub.history()[0]["seq"], event["seq"])
        hub.unsubscribe(subscription)

    async def test_bounded_queue_drops_oldest_visual_event(self) -> None:
        hub = EventHub(history_limit=10, queue_size=10)
        subscription = hub.subscribe()
        for index in range(14):
            hub.publish("generation.snapshot", {"index": index})
        await asyncio.sleep(0)
        values = []
        while not subscription.queue.empty():
            values.append((await subscription.queue.get())["payload"]["index"])
        self.assertEqual(values, list(range(4, 14)))
