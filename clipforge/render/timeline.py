"""Source time to output time (SPEC 7.9): the clip's kept stretches end to end, each a whole number of
output frames long, at one speed. A cold open's teaser (SPEC 8.3) plays first; word timing skips it, so the
body's words land where they would without one, moved later by its length."""

from __future__ import annotations

import bisect
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel

FPS_MIN, FPS_MAX = 23.976, 60.0
NTSC = (23.976, 29.97, 59.94)
EPS = 1e-6


def output_fps(src_fps: float, cap_30: bool) -> float:
    """The source's rate kept within 23.976 to 60 (30 when capped and above 30), NTSC rates exact."""
    fps = min(FPS_MAX, max(FPS_MIN, src_fps))
    if cap_30 and fps > 30 + EPS:
        return 30.0
    return next((rate for rate in NTSC if abs(fps - rate) < 0.01), round(fps, 3))


class Segment(BaseModel):
    src_start: float
    src_end: float
    out_start: float
    speed: float = 1.0
    teaser: bool = False  # the cold open, played before the clip's start


@dataclass(frozen=True)
class Timeline:
    segments: list[Segment]
    fps: float

    @classmethod
    def from_ranges(
        cls,
        ranges: Sequence[tuple[float, float]],
        fps: float,
        speed: float = 1.0,
        *,
        teaser: tuple[float, float] | None = None,
    ) -> Timeline:
        """Each source range trimmed to a whole number of output frames; ranges under a frame are dropped.
        ``teaser`` is a source range played first, at the same speed."""
        segments, frames = [], 0
        for k, (a, b) in enumerate([teaser, *ranges] if teaser else ranges):
            n = round((b - a) / speed * fps)
            if n > 0:
                segments.append(
                    Segment(
                        src_start=a,
                        src_end=a + n * speed / fps,
                        out_start=frames / fps,
                        speed=speed,
                        teaser=bool(teaser) and k == 0,
                    )
                )
                frames += n
        return cls(segments, fps)

    def _frames(self, s: Segment) -> int:
        return round((s.src_end - s.src_start) / s.speed * self.fps)

    @property
    def frames(self) -> int:
        return sum(self._frames(s) for s in self.segments)

    @property
    def duration(self) -> float:
        return self.frames / self.fps

    @property
    def teaser_end(self) -> float:
        """Where the cold open's teaser ends in output time: 0 without one."""
        first = self.segments[0] if self.segments else None
        return self._frames(first) / self.fps if first is not None and first.teaser else 0.0

    def _body(self) -> list[Segment]:
        return [s for s in self.segments if not s.teaser]

    def to_output(self, t_src: float) -> float | None:
        """None when ``t_src`` was cut. The teaser is skipped: a word in it is timed where the body says
        it."""
        for s in self._body():
            if s.src_start - EPS <= t_src <= s.src_end + EPS:
                return s.out_start + (min(max(t_src, s.src_start), s.src_end) - s.src_start) / s.speed
        return None

    def nearest_output(self, t_src: float) -> float:
        """Like ``to_output``, but a cut time lands on the closest kept edge."""
        got = self.to_output(t_src)
        if got is not None or not self._body():
            return got or 0.0
        edges = [(abs(t - t_src), out) for s in self._body() for t, out in self._edges(s)]
        return min(edges)[1]

    def _edges(self, s: Segment) -> list[tuple[float, float]]:
        return [(s.src_start, s.out_start), (s.src_end, s.out_start + self._frames(s) / self.fps)]

    def to_source(self, t_out: float) -> float:
        starts = [s.out_start for s in self.segments]
        s = self.segments[max(0, bisect.bisect_right(starts, t_out + EPS) - 1)]
        return min(s.src_end, s.src_start + max(0.0, t_out - s.out_start) * s.speed)
