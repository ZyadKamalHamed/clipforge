"""Keeping the Mac awake for a post that is due soon (SPEC 10.8): one ``caffeinate -i`` child while a post is
due within the hour, none otherwise, so a post isn't missed to idle sleep and the Mac isn't kept up for
nothing."""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from clipforge import children

AHEAD = timedelta(minutes=60)  # held while a post is due within this
MARGIN = timedelta(minutes=15)  # and for this long after its time
SLACK = timedelta(minutes=5)  # a child that covers all but this much of what is needed stays

RunCmd = Callable[[list[str]], Any]  # starts a command and gives back a Popen-like handle


def launch(args: list[str]) -> subprocess.Popen:
    """Start ``args`` in the background, its output dropped, tracked so quitting ends it: the engine's
    ``run_cmd`` outside tests."""
    return children.popen(
        args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


class PowerAssertion:
    def __init__(self, run_cmd: RunCmd):
        self.run_cmd = run_cmd
        self._proc: Any = None
        self._until: datetime | None = None  # when the child's time runs out
        self._lock = threading.Lock()

    def update(self, next_due: datetime | None, now: datetime) -> None:
        """Hold the Mac awake until 15 minutes after ``next_due`` (15 minutes from now when it has passed)
        when it is within the hour; let go when it isn't or there is none."""
        with self._lock:
            if next_due is None or next_due - now > AHEAD:
                self._end()
                return
            end = max(next_due, now) + MARGIN
            alive = self._proc is not None and self._proc.poll() is None
            if alive and self._until is not None and self._until >= end - SLACK:
                return
            self._end()
            seconds = round((end - now).total_seconds())
            self._proc = self.run_cmd(["caffeinate", "-i", "-t", str(seconds)])
            self._until = end

    def release(self) -> None:
        """Let the Mac sleep again: the child ends."""
        with self._lock:
            self._end()

    def _end(self) -> None:
        proc, self._proc, self._until = self._proc, None, None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
