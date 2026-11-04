"""When something an item on Today's list waits on changed (SPEC 14.10), heard from the engine's events: an
item finished on another page is timed to that change, not to the next time Today is read (review M9)."""

from __future__ import annotations

import threading
from collections.abc import Callable

# The events that can end each kind of item: what its need is read from
WAITS_ON: dict[str, frozenset[str]] = {
    "drafts": frozenset({"drafts", "post", "clip_channel"}),
    "submit": frozenset({"submission", "post", "campaign"}),
    "views": frozenset({"submission", "post"}),
    "decide": frozenset({"submission"}),
    "ends": frozenset({"campaign", "submission"}),
    "payout": frozenset({"submission", "campaign"}),
    "needs_you": frozenset({"post"}),
    "login": frozenset({"account"}),
    "insights": frozenset({"account"}),
    "exported": frozenset({"post"}),
    "briefs": frozenset({"brief"}),
    "source": frozenset({"brief"}),
    "join": frozenset({"campaign"}),
}
TOPICS = frozenset().union(*WAITS_ON.values())
KEEP = 256  # changes kept between two reads of the list, the latest


class Changes:
    """The changes heard since the list was last brought up to date, as (time, topic)."""

    def __init__(self, now: Callable[[], float]):
        self._now = now
        self._lock = threading.Lock()
        self._heard: list[tuple[float, str]] = []

    def heard(self, topic: str) -> None:
        if topic not in TOPICS:
            return
        change = (self._now(), topic)
        with self._lock:
            if not self._heard or self._heard[-1] != change:
                self._heard.append(change)
                del self._heard[:-KEEP]

    def pending(self) -> bool:
        with self._lock:
            return bool(self._heard)

    def take(self) -> list[tuple[float, str]]:
        """The changes heard, forgotten here."""
        with self._lock:
            heard, self._heard = self._heard, []
        return heard


def done_at(kind: str, started_at: float | None, heard: list[tuple[float, str]], now: float) -> float:
    """When an item whose need went was done: the first change it waits on after its start (one at the
    same moment came before it), else ``now``. An item never started is done ``now``, as it has no timing."""
    if started_at is None:
        return now
    topics = WAITS_ON.get(kind, frozenset())
    return min((t for t, topic in heard if topic in topics and started_at < t <= now), default=now)
