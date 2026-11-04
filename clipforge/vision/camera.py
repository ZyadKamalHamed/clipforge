"""The virtual camera (SPEC 7.8): a crop of fixed shape that follows one face. Eyes sit at 38 % of the crop's
height; a speaker whose 10th to 90th percentile spread fits in 25 % of the crop width gets one fixed crop,
if that keeps the face's box in; otherwise the camera only moves once the target leaves a dead zone of 15 %
of the crop width, at no more than 0.6 crop widths a second and 1.5 a second squared, and stops within 3 %.
Wherever the face's box fits in the crop it stays inside: knowing where the face goes, the camera opens each
shot where it can keep it in and sets off in time to do so at those limits; only a face faster than them
pushes it faster. A jump past 60 % that lasts 8 frames is a hard cut, as are scene cuts and speaker
switches. With no face the camera holds 1 s, then eases to the centre."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

EYE_LINE = 0.38
STILL_SPREAD = 0.25
ENTER = 0.15
EXIT = 0.03
MAX_SPEED = 0.6  # crop widths a second
MAX_ACCEL = 1.5  # crop widths a second squared
JUMP = 0.6
JUMP_FRAMES = 8
LOST_HOLD_S = 1.0
SEEN_S = 0.15  # a face sample stands for this long
PUNCH_GAP_S = 4.0
PUNCH_MIN_S = 2.0
PUNCH_SCALE = 1.08
PUNCH_SCALE_HI = 1.15  # sources of 1440 lines or more
UPSCALE_WARN = 1.9
EPS = 1e-6

Rect = tuple[int, int, int, int]  # x, y, w, h in source pixels


@dataclass(frozen=True)
class CropPath:
    rects: list[Rect]  # one per output frame


def crop_size(src_w: int, src_h: int, out_w: int, out_h: int) -> tuple[int, int]:
    """The largest crop of the output's shape that fits the source, in even pixels."""
    if src_w * out_h > src_h * out_w:
        return min(src_w, 2 * round(src_h * out_w / out_h / 2)), src_h
    return src_w, min(src_h, 2 * round(src_w * out_h / out_w / 2))


def punch_scale(src_h: int) -> float:
    return PUNCH_SCALE_HI if src_h >= 1440 else PUNCH_SCALE


def upscale(crop_w: int, crop_h: int, out_w: int, out_h: int) -> tuple[float, bool]:
    """How much the crop is enlarged to fill the output, and whether that is past the 1.9 warning."""
    factor = max(out_w / crop_w, out_h / crop_h)
    return factor, factor > UPSCALE_WARN + EPS


def punch_ins(
    starts: Sequence[float],
    end_s: float,
    *,
    scale: float,
    gap_s: float = PUNCH_GAP_S,
    min_s: float = PUNCH_MIN_S,
    stops: Sequence[float] | None = None,
) -> list[tuple[float, float, float]]:
    """Zooms that begin on sentence starts: at most one every ``gap_s``, each at least ``min_s`` long and
    ending on the first of ``stops`` (the starts themselves when not given) after that, or the clip's end,
    with an unzoomed stretch between them."""
    cands = sorted(starts)
    ends = cands if stops is None else sorted(stops)
    out: list[tuple[float, float, float]] = []
    for c in cands:
        if out and (c - out[-1][0] < gap_s - EPS or c <= out[-1][1] + EPS):
            continue
        later = [s for s in ends if s >= c + min_s - EPS]
        end = min(later[0], end_s) if later else end_s
        if end - c >= min_s - EPS:
            out.append((c, end, scale))
    return out


def follow(
    track_points: Sequence[tuple[float, float, float]],
    times_out: Sequence[float],
    *,
    src_w: int,
    src_h: int,
    crop_w: int,
    crop_h: int,
    cuts: Sequence[float] = (),
    switches: Sequence[float] = (),
    zoomed: Sequence[tuple[float, float, float]] = (),
    faces: Sequence[tuple[float, float, float, float, float]] = (),
) -> CropPath:
    """One crop per output time. ``track_points`` are (time, eye x, eye y) in source pixels for the face to
    follow, only where it was seen; ``faces`` are (time, x, y, w, h), that face's box, kept inside the crop
    wherever it fits; ``zoomed`` are punch-ins as (start, end, scale)."""
    t = np.asarray(times_out, dtype=float)
    if not len(t):
        return CropPath([])
    dt = float(np.median(np.diff(t))) if len(t) > 1 else 1 / 30
    max_x, max_y = src_w - crop_w, src_h - crop_h
    gx, gy = np.full(len(t), np.nan), np.full(len(t), np.nan)
    if track_points:
        pts = sorted(track_points)
        pt = np.array([p[0] for p in pts])
        k = np.clip(np.searchsorted(pt, t + EPS, side="right") - 1, 0, None)
        ok = (pt[k] <= t + EPS) & (t - pt[k] <= SEEN_S + EPS)
        gx[ok] = np.clip(np.array([p[1] for p in pts])[k[ok]] - crop_w / 2, 0, max_x)
        gy[ok] = np.clip(np.array([p[2] for p in pts])[k[ok]] - EYE_LINE * crop_h, 0, max_y)
    real = ~np.isnan(gx)
    sizes = []
    for ti in t:
        s = next((z for a, b, z in zoomed if a - EPS <= ti < b - EPS), 1.0)
        sizes.append((crop_w, crop_h) if s == 1.0 else (round(crop_w / s), round(crop_h / s)))
    w, h = (np.array(v, dtype=float) for v in zip(*sizes, strict=True))
    keep = _keep(faces, t, w, h, crop_w=crop_w, crop_h=crop_h, max_x=max_x, max_y=max_y)
    x, y = np.empty(len(t)), np.empty(len(t))
    hard = sorted({c for c in (*cuts, *switches) if t[0] + EPS < c <= t[-1] + EPS})
    bounds = [-math.inf, *hard, math.inf]
    for a, b in zip(bounds, bounds[1:], strict=False):
        seg = (t >= a - EPS) & (t < b - EPS)
        if seg.any():
            x[seg] = _axis(gx[seg], real[seg], dt, crop_w, max_x, keep[0][seg], keep[1][seg])
            y[seg] = _axis(gy[seg], real[seg], dt, crop_h, max_y, keep[2][seg], keep[3][seg])
    rects: list[Rect] = []
    for xi, yi, (w, h) in zip(x, y, sizes, strict=True):
        rx = min(max(round(xi + crop_w / 2 - w / 2), 0), src_w - w)
        ry = min(max(round(yi + EYE_LINE * crop_h - EYE_LINE * h), 0), src_h - h)
        rects.append((int(rx), int(ry), int(w), int(h)))
    return CropPath(rects)


def _keep(
    faces: Sequence[tuple[float, float, float, float, float]],
    t: np.ndarray,
    w: np.ndarray,
    h: np.ndarray,
    *,
    crop_w: int,
    crop_h: int,
    max_x: int,
    max_y: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Per output time, where the crop's corner may go on each axis (x from, x to, y from, y to) to keep the
    face's box inside the crop as it is cut then (``w`` by ``h``, punched in or not); no limit where the
    box was not seen or does not fit."""
    out = [np.full(len(t), v) for v in (-math.inf, math.inf, -math.inf, math.inf)]
    if not faces:
        return out[0], out[1], out[2], out[3]
    fs = sorted(faces)
    ft = np.array([f[0] for f in fs])
    k = np.clip(np.searchsorted(ft, t + EPS, side="right") - 1, 0, None)
    seen = (ft[k] <= t + EPS) & (t - ft[k] <= SEEN_S + EPS)
    bx, by, bw, bh = np.array([f[1:] for f in fs], dtype=float)[k].T
    axes = (((crop_w - w) / 2, bx, bw, w, max_x), (EYE_LINE * (crop_h - h), by, bh, h, max_y))
    for n, (offset, start, length, cut, room) in enumerate(axes):
        lo = np.maximum(start + length - cut - offset, 0)
        hi = np.minimum(start - offset, room)
        fits = seen & (lo <= hi + EPS)
        out[2 * n][fits], out[2 * n + 1][fits] = lo[fits], np.maximum(lo, hi)[fits]
    return out[0], out[1], out[2], out[3]


def _axis(
    target: np.ndarray, real: np.ndarray, dt: float, size: float, room: float, lo: np.ndarray, hi: np.ndarray
) -> np.ndarray:
    """One axis of one stretch between hard cuts: ``target`` is NaN where no face is seen, ``room`` is how
    far the crop can go, and from ``lo`` to ``hi`` it keeps the face inside."""
    if not real.any():
        return np.full(len(target), room / 2)
    goal = _fill_lost(target, real, dt, room / 2)
    p10, p90 = np.percentile(goal, [10, 90])
    still = float(np.median(goal))
    near = np.abs(goal - still) <= JUMP * size  # a blip far off is no reason to give up a fixed crop
    if p90 - p10 <= STILL_SPREAD * size + EPS and ((lo <= still + EPS) & (still <= hi + EPS) | ~near).all():
        return np.full(len(goal), still)
    # far enough ahead to turn round and cross the whole room, so nothing later can catch the camera out
    ahead = math.ceil((2 * MAX_SPEED / MAX_ACCEL + room / (MAX_SPEED * size)) / dt) + 2
    return _dead_zone(goal, real, dt, size, lo, hi, ahead)


def _fill_lost(target: np.ndarray, real: np.ndarray, dt: float, rest: float) -> np.ndarray:
    """Before the face first shows, the camera already sits on it; after losing it, it holds for 1 s and
    then heads for ``rest`` (the dead zone follower does the easing)."""
    goal = target.copy()
    first = int(np.argmax(real))
    goal[:first] = target[first]
    hold = max(1, round(LOST_HOLD_S / dt))
    last, since = float(target[first]), 0
    for i in range(first, len(goal)):
        if real[i]:
            last, since = float(goal[i]), 0
        else:
            since += 1
            goal[i] = last if since <= hold else rest
    return goal


def _dead_zone(
    goal: np.ndarray, real: np.ndarray, dt: float, size: float, lo: np.ndarray, hi: np.ndarray, ahead: int
) -> np.ndarray:
    """The heavy tripod, which also keeps the face's box inside ``lo`` to ``hi``: looking ``ahead`` frames,
    never past a jump, it opens where it can keep the box in from rest, sets off at full effort once waiting
    a frame longer would let the box out, and only a face faster than it pushes it faster."""
    vmax, acc = MAX_SPEED * size, MAX_ACCEL * size
    ramp = np.arange(1, ahead + 1) * acc * dt
    part = np.cumsum(np.abs(np.diff(goal, prepend=goal[0])) > JUMP * size)
    ends = np.searchsorted(part, part, side="right")
    bound = np.concatenate([[0], np.cumsum(np.isfinite(lo) | np.isfinite(hi))])
    first = min(int(ends[0]), ahead + 1)
    reach = np.concatenate([[0.0], dt * np.cumsum(np.minimum(ramp, vmax))[: first - 1]])
    low, high = float(np.max(lo[:first] - reach)), float(np.min(hi[:first] + reach))
    out = np.empty(len(goal))
    p, v, moving, far = float(goal[0]), 0.0, False, 0
    if low <= high + EPS:  # a cut can open anywhere, so open where the coming moves can be kept up with
        p = min(max(p, low), high)
    for i, g in enumerate(goal):
        err = float(g) - p
        if real[i] and abs(err) > JUMP * size:
            far += 1
            if far >= JUMP_FRAMES:  # it lasted, so cut from the frame where the jump began
                p, v, moving, far = float(g), 0.0, False, 0
                out[i - JUMP_FRAMES + 1 : i + 1] = p
            else:
                out[i] = p
            continue
        far = 0
        if not moving and abs(err) > ENTER * size:
            moving = True
        q, u = p, v
        if moving:
            want = math.copysign(min(vmax, math.sqrt(2 * acc * abs(err))), err)
            u = v + max(-acc * dt, min(acc * dt, want - v))
            if abs(u * dt) >= abs(err):
                q, u, moving = float(g), 0.0, False
            elif abs(err) < EXIT * size and abs(u) <= acc * dt:
                u, moving = 0.0, False
            else:
                q = p + u * dt
        end = min(int(ends[i]), i + 1 + ahead)
        if bound[end] > bound[i + 1]:
            push = _behind(q, u, lo[i + 1 : end], hi[i + 1 : end], ramp, dt, vmax)
            if push:  # full effort now, or the box leaves the crop later
                u = max(-vmax, min(vmax, v + push * acc * dt))
                q, moving = p + u * dt, True
        if not lo[i] <= q <= hi[i]:  # a face faster than the camera pushes it
            q, moving = min(max(q, lo[i]), hi[i]), True
        p, v = q, u
        out[i] = p
    return out


def _behind(
    p: float, v: float, lo: np.ndarray, hi: np.ndarray, ramp: np.ndarray, dt: float, vmax: float
) -> int:
    """-1 or 1 when even full effort that way from ``p`` at ``v`` would let the box out over the coming
    frames, ``lo`` to ``hi`` being where the box keeps in then (the side it would leave by first), else 0."""
    r = ramp[: len(lo)]
    over = np.flatnonzero(p + dt * np.cumsum(np.maximum(v - r, -vmax)) > hi + EPS)
    under = np.flatnonzero(p + dt * np.cumsum(np.minimum(v + r, vmax)) < lo - EPS)
    if not under.size:
        return -1 if over.size else 0
    return -1 if over.size and over[0] <= under[0] else 1
