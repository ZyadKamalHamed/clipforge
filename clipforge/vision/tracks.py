"""Face tracks (SPEC 7.6): detections linked by an IoU of at least 0.25 within one shot, gaps up to 0.6 s
bridged, tracks shorter than 0.4 s dropped, and the boxes smoothed."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field

import numpy as np

from clipforge.models import FaceBox, Track

IOU = 0.25
MAX_GAP_S = 0.6
MIN_LEN_S = 0.4
EPS = 1e-6

Box = tuple[float, float, float, float]


def box_iou(a: Box, b: Box) -> float:
    ix = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def _xywh(f: FaceBox) -> Box:
    return (f.x, f.y, f.w, f.h)


def _eyes(f: FaceBox) -> tuple[float, float]:
    if len(f.marks) >= 2:
        return ((f.marks[0][0] + f.marks[1][0]) / 2, (f.marks[0][1] + f.marks[1][1]) / 2)
    return (f.x + f.w / 2, f.y + 0.4 * f.h)


def shot_index(cuts: list[float], t: float) -> int:
    """Shots are numbered from 0; a frame at a cut's time starts the new shot."""
    return bisect_right(cuts, t + EPS)


def smooth(values: np.ndarray, *, median: int = 5, alpha: float = 0.5) -> np.ndarray:
    """A centred running median, then an exponential moving average."""
    n = len(values)
    if n == 0:
        return values
    half = median // 2
    padded = np.pad(values, (half, half), mode="edge")
    med = np.array([np.median(padded[i : i + median]) for i in range(n)])
    out = np.empty(n)
    out[0] = med[0]
    for i in range(1, n):
        out[i] = alpha * med[i] + (1 - alpha) * out[i - 1]
    return out


@dataclass
class _Open:
    shot: int
    hits: list[tuple[int, FaceBox, int]] = field(default_factory=list)  # (sample, face, face index)


def build_tracks(
    samples: list[tuple[float, list[FaceBox]]],
    cuts: list[float],
    *,
    iou: float = IOU,
    max_gap_s: float = MAX_GAP_S,
    min_len_s: float = MIN_LEN_S,
) -> list[Track]:
    """Tracks from the faces found in each analysis frame, numbered in order of their first frame."""
    times = [t for t, _ in samples]
    live: list[_Open] = []
    done: list[_Open] = []
    for si, (t, faces) in enumerate(samples):
        shot = shot_index(cuts, t)
        still = []
        for tr in live:
            ended = tr.shot != shot or t - times[tr.hits[-1][0]] > max_gap_s + EPS
            (done if ended else still).append(tr)
        live = still
        pairs = sorted(
            (
                (box_iou(_xywh(tr.hits[-1][1]), _xywh(f)), ti, fi)
                for ti, tr in enumerate(live)
                for fi, f in enumerate(faces)
            ),
            key=lambda p: p[0],
            reverse=True,
        )
        taken_t: set[int] = set()
        taken_f: set[int] = set()
        for score, ti, fi in pairs:
            if score < iou:
                break
            if ti in taken_t or fi in taken_f:
                continue
            live[ti].hits.append((si, faces[fi], fi))
            taken_t.add(ti)
            taken_f.add(fi)
        live += [_Open(shot, [(si, f, fi)]) for fi, f in enumerate(faces) if fi not in taken_f]
    done += live
    done.sort(key=lambda tr: (tr.hits[0][0], tr.hits[0][2]))
    tracks: list[Track] = []
    for tr in done:
        first, last = tr.hits[0][0], tr.hits[-1][0]
        if times[last] - times[first] < min_len_s - EPS:
            continue
        tracks.append(_finish(tr, times[first : last + 1], first, len(tracks)))
    return tracks


def _finish(tr: _Open, grid: list[float], first: int, track_id: int) -> Track:
    """Fill bridged times by straight lines between detections, then smooth every coordinate."""
    hit_t = np.array([f.t for _, f, _ in tr.hits])
    cols = np.array([[*_xywh(f), *_eyes(f)] for _, f, _ in tr.hits])
    g = np.array(grid)
    filled = np.column_stack([smooth(np.interp(g, hit_t, cols[:, k])) for k in range(cols.shape[1])])
    hit_at = {si: (f, fi) for si, f, fi in tr.hits}
    src: list[tuple[int, int] | None] = []
    scores = []
    for i in range(len(grid)):
        hit = hit_at.get(first + i)
        src.append((first + i, hit[1]) if hit else None)
        scores.append(hit[0].score if hit else 0.0)
    return Track(
        id=track_id,
        shot=tr.shot,
        times=list(grid),
        boxes=[(float(r[0]), float(r[1]), float(r[2]), float(r[3])) for r in filled],
        eyes=[(float(r[4]), float(r[5])) for r in filled],
        scores=scores,
        src=src,
    )
