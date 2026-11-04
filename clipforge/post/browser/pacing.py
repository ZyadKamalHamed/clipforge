"""A short random pause between a browser route's steps (SPEC 17). It gives a page time to react, as a
person's pace would; it is for reliability, not to hide anything."""

from __future__ import annotations

import random
import time
from collections.abc import Callable


class Pacer:
    def __init__(
        self,
        rng: random.Random | None = None,
        low: float = 0.4,
        high: float = 1.5,
        sleep: Callable[[float], object] = time.sleep,
    ):
        self.rng = rng or random.Random()
        self.low = low
        self.high = high
        self.sleep = sleep

    def pause(self) -> float:
        """Waits a random time from ``low`` to ``high`` seconds and gives it back."""
        wait = self.rng.uniform(self.low, self.high)
        self.sleep(wait)
        return wait
