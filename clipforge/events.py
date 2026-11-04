"""In-process publish and subscribe. Jobs publish progress here; the web UI streams it (SPEC 7)."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Event:
    topic: str
    data: dict[str, Any]
    ts: float


class Subscription:
    def __init__(self, bus: EventBus, maxsize: int):
        self._bus = bus
        self._queue: deque[Event] = deque(maxlen=maxsize)  # when full, appending drops the oldest
        self._ready = threading.Condition()
        self._closed = False
        self.overflowed = False  # an event was dropped since this was last cleared, so some were missed

    def _put(self, event: Event) -> None:
        with self._ready:
            if not self._closed:
                if len(self._queue) == self._queue.maxlen:
                    self.overflowed = True
                self._queue.append(event)
                self._ready.notify()

    def get(self, timeout: float | None = None) -> Event | None:
        """The next event, or None after ``timeout`` seconds or once closed."""
        with self._ready:
            self._ready.wait_for(lambda: self._queue or self._closed, timeout)
            if self._closed or not self._queue:
                return None
            return self._queue.popleft()

    def close(self) -> None:
        self._bus._remove(self)
        with self._ready:
            self._closed = True
            self._queue.clear()
            self._ready.notify_all()


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: list[Subscription] = []
        self._listeners: list[Callable[[Event], None]] = []

    def publish(self, topic: str, data: dict[str, Any]) -> None:
        event = Event(topic, dict(data), time.time())
        with self._lock:
            subs, listeners = list(self._subs), list(self._listeners)
        for sub in subs:
            sub._put(event)
        for listener in listeners:
            try:
                listener(event)
            except Exception:  # a listener's trouble never reaches the one publishing
                log.exception("a listener failed on a %s event", topic)

    def listen(self, listener: Callable[[Event], None]) -> None:
        """Calls ``listener`` with each event as it is published, on the publishing thread."""
        with self._lock:
            self._listeners.append(listener)

    def subscribe(self, maxsize: int = 1000) -> Subscription:
        sub = Subscription(self, maxsize)
        with self._lock:
            self._subs.append(sub)
        return sub

    def _remove(self, sub: Subscription) -> None:
        with self._lock:
            if sub in self._subs:
                self._subs.remove(sub)
