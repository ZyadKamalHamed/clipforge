"""Shot classes (SPEC 7.6 and 7.7). Each stretch between scene cuts is one of: single (one usable face),
two_shot (two faces together in at least half the shot, at least 0.20 of the width apart, each at least
0.045 of the width), group (three or more), stream (the clip's facecam in at least 60 % of the shot, and no
big face), screen (no face, still) or motion (no face, changing). The facecam is a small face held still at a
corner or a side, in the same spot from shot to shot, for at least a fifth of the clip, without a face its
size beside it as people in a wide shot have."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

import cv2
import numpy as np

from clipforge.models import Shot, Track

MIN_FACE_W = 0.045  # of the frame width
MIN_SEPARATION = 0.20
MIN_TOGETHER = 0.5  # of the shot
MIN_PRESENCE = 0.25
FACECAM_MIN_W, FACECAM_W = 0.025, 0.12  # a facecam's face is at least the first and under the second
FACECAM_PRESENCE = 0.6
FACECAM_SHARE = 0.2  # a facecam is seen in at least this share of the clip's frames
FACECAM_HOLD = 0.03
CORNER_X, CORNER_Y, SIDE_X = 0.3, 0.2, 0.35  # how far from the middle a facecam sits
SPOT = 0.05  # tracks this close, and within 1.5 times each other's width, are in the same spot
BESIDE_DY = 0.12
CHANGING = 0.02  # mean absolute frame difference, 0 to 1
STAT_WIDTH = 320
FACE_KINDS = ("single", "two_shot", "group")
EPS = 1e-6


@dataclass(frozen=True)
class FrameStat:
    diff: float  # mean absolute difference from the previous frame, 0 to 1
    sharp: float  # variance of the Laplacian


class FrameStatter:
    """Measures one frame at a time, as the decoder hands them over, against the frame before."""

    def __init__(self, width: int = STAT_WIDTH):
        self._width = width
        self._prev: np.ndarray | None = None

    def __call__(self, frame: np.ndarray) -> FrameStat:
        grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        h, w = grey.shape
        if w > self._width:
            grey = cv2.resize(
                grey, (self._width, max(1, round(h * self._width / w))), interpolation=cv2.INTER_AREA
            )
        diff = 0.0
        if self._prev is not None and self._prev.shape == grey.shape:
            diff = float(np.mean(cv2.absdiff(grey, self._prev))) / 255
        self._prev = grey
        return FrameStat(diff=diff, sharp=float(cv2.Laplacian(grey, cv2.CV_64F).var()))


def frame_stats(frames: Iterable[np.ndarray]) -> list[FrameStat]:
    statter = FrameStatter()
    return [statter(f) for f in frames]


def classify_shots(
    cuts: list[float],
    tracks: list[Track],
    stats: list[FrameStat],
    times: list[float],
    start_s: float,
    end_s: float,
) -> list[Shot]:
    """One Shot per stretch between ``cuts`` (inside the clip, in order), numbered like ``Track.shot``;
    ``stats`` line up with ``times``."""
    bounds = [start_s, *cuts, end_s]
    t_all = np.asarray(times, dtype=float)
    cam = facecam(tracks, len(times))
    shots = []
    for i in range(len(bounds) - 1):
        a, b = bounds[i], bounds[i + 1]
        st = [stats[k] for k in np.flatnonzero((t_all >= a - EPS) & (t_all < b - EPS))]
        diff = float(np.mean([s.diff for s in st[1:]])) if len(st) > 1 else 0.0  # the first frame is the cut
        sharp = float(np.median([s.sharp for s in st])) if st else 0.0
        kind, ids = _kind([tr for tr in tracks if tr.shot == i], max(1, len(st)), diff, cam)
        shots.append(Shot(start_s=a, end_s=b, kind=kind, tracks=ids, quality=_quality(sharp, diff)))
    return shots


def clip_quality(shots: list[Shot], tracks: list[Track], crop_upscale: float) -> float:
    """C8, 0 to 10: half the shots' sharpness and steadiness, a quarter how often the faces were really
    seen rather than bridged, a quarter how little the crop is enlarged (2.5 times or more scores 0)."""
    total = sum(s.end_s - s.start_s for s in shots)
    if total <= EPS:
        return 0.0
    base = sum((s.end_s - s.start_s) * s.quality for s in shots) / total / 10
    ids = {tid for s in shots if s.kind in FACE_KINDS for tid in s.tracks}
    seen = [score > 0 for tr in tracks if tr.id in ids for score in tr.scores]
    vis = float(np.mean(seen)) if seen else 1.0
    up = 1.0 - min(1.0, max(0.0, crop_upscale - 1.0) / 1.5)
    return round(min(10.0, max(0.0, 10 * (0.5 * base + 0.25 * vis + 0.25 * up))), 1)


def facecam(tracks: list[Track], frames: int) -> set[int]:
    """The ids of the clip's facecam tracks: of the spots where a small face is held still at a corner or a
    side for at least a fifth of the clip's ``frames``, the one seen longest that has no face its size beside
    it for half that time. Empty when none."""
    spots: list[list[Track]] = []
    for tr in sorted((t for t in tracks if _cam_like(t)), key=lambda t: len(t.times), reverse=True):
        spot = next((sp for sp in spots if _same_spot(sp[0], tr)), None)
        if spot is None:
            spots.append([tr])
        else:
            spot.append(tr)
    for spot in sorted(
        (sp for sp in spots if _seen(sp) >= FACECAM_SHARE * frames - EPS), key=_seen, reverse=True
    ):
        ids = {tr.id for tr in spot}
        beside: set[float] = set()
        for tr in spot:
            for other in tracks:
                if other.id not in ids and _beside(tr, other):
                    beside |= set(tr.times) & set(other.times)
        if len(beside) < 0.5 * _seen(spot):
            return ids
    return set()


def _kind(mine: list[Track], n: int, diff: float, cam: set[int]) -> tuple[str, list[int]]:
    present = [tr for tr in mine if len(tr.times) >= MIN_PRESENCE * n - EPS]
    cams = [tr for tr in mine if tr.id in cam]
    big = any(_width(tr) >= FACECAM_W for tr in present)
    if cams and _seen(cams) >= FACECAM_PRESENCE * n - EPS and not big:
        return "stream", [tr.id for tr in cams]
    faces = [tr for tr in present if _width(tr) >= MIN_FACE_W and tr.id not in cam]
    ids = [tr.id for tr in faces]
    if not faces:
        return ("screen" if diff < CHANGING else "motion"), []
    if len(faces) >= 3:
        return "group", ids
    if len(faces) == 2 and _two_shot(faces[0], faces[1], n):
        return "two_shot", ids
    return "single", ids


def _width(tr: Track) -> float:
    return float(np.median([b[2] for b in tr.boxes]))


def _centres(tr: Track) -> tuple[np.ndarray, np.ndarray]:
    boxes = np.asarray(tr.boxes, dtype=float)
    return boxes[:, 0] + boxes[:, 2] / 2, boxes[:, 1] + boxes[:, 3] / 2


def _middle(tr: Track) -> tuple[float, float]:
    cx, cy = _centres(tr)
    return float(np.median(cx)), float(np.median(cy))


def _seen(tracks: list[Track]) -> int:
    """How many samples show at least one of ``tracks``."""
    return len({t for tr in tracks for t in tr.times})


def _cam_like(tr: Track) -> bool:
    """A small face held still at a corner or a side of the frame."""
    cx, cy = _centres(tr)
    held = max(float(np.std(cx)), float(np.std(cy))) <= FACECAM_HOLD
    x, y = (abs(v - 0.5) for v in _middle(tr))
    return (
        FACECAM_MIN_W <= _width(tr) < FACECAM_W and held and (x > SIDE_X or (x > CORNER_X and y > CORNER_Y))
    )


def _same_spot(a: Track, b: Track) -> bool:
    (ax, ay), (bx, by) = _middle(a), _middle(b)
    wa, wb = _width(a), _width(b)
    return abs(ax - bx) <= SPOT and abs(ay - by) <= SPOT and max(wa, wb) <= 1.5 * min(wa, wb)


def _beside(cam: Track, other: Track) -> bool:
    """Whether ``other`` is a face about ``cam``'s size at its height but well across the frame, as the
    other people in a wide shot are."""
    (cx, cy), (ox, oy) = _middle(cam), _middle(other)
    ratio = _width(other) / max(_width(cam), EPS)
    return 0.5 <= ratio <= 2.0 and abs(oy - cy) <= BESIDE_DY and abs(ox - cx) >= MIN_SEPARATION


def _two_shot(p: Track, q: Track, n: int) -> bool:
    q_at = dict(zip(q.times, _centres(q)[0], strict=True))
    seps = [abs(x - q_at[t]) for t, x in zip(p.times, _centres(p)[0], strict=True) if t in q_at]
    return len(seps) >= MIN_TOGETHER * n - EPS and float(np.median(seps)) >= MIN_SEPARATION


def _quality(sharp: float, diff: float) -> float:
    """0 to 10 from sharpness (a Laplacian variance of 20 or less is soft, 400 or more is crisp) and how
    steady the shot is."""
    crisp = (math.log10(max(sharp, 1.0)) - math.log10(20)) / (math.log10(400) - math.log10(20))
    steady = 1.0 - min(1.0, diff / 0.08)
    return round(10 * (0.6 * min(1.0, max(0.0, crisp)) + 0.4 * steady), 2)
