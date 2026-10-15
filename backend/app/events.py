"""In-process pub/sub used to fan out run events to Server-Sent Event subscribers.

Publishers may be worker threads (the graph runs in a thread pool); delivery is marshalled onto the event loop.
"""
from __future__ import annotations

import asyncio
import json
import threading
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._subs: set[asyncio.Queue[str]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=1000)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        with self._lock:
            self._subs.discard(q)

    def _deliver(self, msg: str) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass

    def publish(self, event: dict[str, Any]) -> None:
        msg = json.dumps(event, default=str)
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._deliver(msg)
        else:
            loop.call_soon_threadsafe(self._deliver, msg)
