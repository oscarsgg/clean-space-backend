import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any


class RealtimeBroker:
    """Thread-safe asyncio.Queue pub/sub broker for Server-Sent Events."""

    def __init__(self, max_queue_size: int = 200):
        self._subscribers: set[asyncio.Queue[str]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._max_queue_size = max_queue_size

    @asynccontextmanager
    async def subscription(self):
        self._loop = asyncio.get_running_loop()
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=self._max_queue_size)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    def _dispatch_nowait(self, frame: str):
        for queue in list(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(frame)
            except asyncio.QueueFull:
                pass

    def publish(self, event_type: str, payload: dict[str, Any]):
        if not self._subscribers or not self._loop or self._loop.is_closed():
            return
        body = {
            "event": event_type,
            "data": payload,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        frame = f"event: {event_type}\ndata: {json.dumps(body, default=str)}\n\n"
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop is self._loop:
            self._dispatch_nowait(frame)
        else:
            self._loop.call_soon_threadsafe(self._dispatch_nowait, frame)


realtime_broker = RealtimeBroker()


def publish_event(event_type: str, payload: dict[str, Any]):
    realtime_broker.publish(event_type, payload)
