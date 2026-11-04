"""Who is talking (SPEC 7.6), only where two or more faces share a shot: mouth motion, gated by how loud
the audio is, decides 0.4 s windows; a new speaker has to win 3 windows in a row, turns under 1.2 s are
ignored, and a switch lands 0.15 s early, snapped to a scene cut within 0.3 s."""

from __future__ import annotations

import math
from collections import defaultdict

import cv2
import numpy as np

from clipforge.models import FaceBox, SpeakerTurn, Track

WIN_S = 0.4
AUDIO_FLOOR = 0.18  # of the clip's loudest window
MARGIN = 0.15
HOLD_WINDOWS = 3
MIN_TURN_S = 1.2
LEAD_S = 0.15
SNAP_S = 0.3
PATCH = (32, 16)
EPS = 1e-6


def mouth_patch(frame: np.ndarray, face: FaceBox) -> np.ndarray | None:
    """The mouth as a 32x16 grey patch, centred between the mouth corners, 1.6 times their distance wide
    and 0.8 times that high, brightness and contrast evened out so faces compare."""
    if len(face.marks) < 5:
        return None
    h, w = frame.shape[:2]
    (rx, ry), (lx, ly) = face.marks[3], face.marks[4]
    cx, cy = (rx + lx) / 2 * w, (ry + ly) / 2 * h
    pw = max(8.0, 1.6 * math.hypot((lx - rx) * w, (ly - ry) * h))
    ph = 0.8 * pw
    x0, x1 = max(0, round(cx - pw / 2)), min(w, round(cx + pw / 2))
    y0, y1 = max(0, round(cy - ph / 2)), min(h, round(cy + ph / 2))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    grey = cv2.cvtColor(np.ascontiguousarray(frame[y0:y1, x0:x1]), cv2.COLOR_BGR2GRAY).astype(np.float32)
    grey = cv2.resize(grey, PATCH, interpolation=cv2.INTER_AREA)
    return (grey - grey.mean()) / max(float(grey.std()), 4.0)


def mouth_activity(track: Track, patches: dict[tuple[int, int], np.ndarray]) -> list[float]:
    """Per track time, how much the mouth patch changed since the last one seen: 0 at the first and where
    the track was bridged."""
    out: list[float] = []
    prev = None
    for src in track.src:
        patch = patches.get(src) if src is not None else None
        if patch is None:
            out.append(0.0)
            continue
        out.append(float(np.mean(np.abs(patch - prev))) if prev is not None else 0.0)
        prev = patch
    return out


def frame_loudness(pcm: np.ndarray, sr: int, times: list[float], *, start_s: float, half_s: float = 0.05):
    """RMS of mono ``pcm`` (its first sample at ``start_s``) around each time."""
    out = np.zeros(len(times))
    for i, t in enumerate(times):
        a = max(0, int((t - half_s - start_s) * sr))
        b = min(len(pcm), int((t + half_s - start_s) * sr))
        if b > a:
            out[i] = float(np.sqrt(np.mean(np.square(pcm[a:b], dtype=np.float64))))
    return out


def switch_time(t: float, cuts: list[float], *, lead_s: float = LEAD_S, snap_s: float = SNAP_S) -> float:
    s = t - lead_s
    near = [c for c in cuts if abs(c - s) <= snap_s + EPS]
    return min(near, key=lambda c: abs(c - s)) if near else s


def drop_short_turns(turns: list[SpeakerTurn], min_s: float = MIN_TURN_S) -> list[SpeakerTurn]:
    """A turn under ``min_s`` joins the turn before it (the first one joins the next), and neighbours with
    the same speaker merge."""
    out: list[SpeakerTurn] = []
    for turn in turns:
        if out and (turn.end_s - turn.start_s < min_s - EPS or turn.track == out[-1].track):
            out[-1] = out[-1].model_copy(update={"end_s": turn.end_s})
        else:
            out.append(turn)
    if len(out) > 1 and out[0].end_s - out[0].start_s < min_s - EPS:
        out[1] = out[1].model_copy(update={"start_s": out[0].start_s})
        out.pop(0)
    return out


def speaker_turns(
    tracks: list[Track], loudness: list[float] | np.ndarray, times: list[float], cuts: list[float]
) -> list[SpeakerTurn]:
    """Speaker turns for every shot with two or more tracks. Mouth motion is compared on one scale per shot
    (its 80th percentile), since each patch is already evened out for size and light."""
    if not times:
        return []
    t_all = np.asarray(times)
    loud = np.asarray(loudness, dtype=float)
    step = float(np.median(np.diff(t_all))) if len(t_all) > 1 else 0.1
    k = max(1, round(WIN_S / step))
    peak = float(np.convolve(loud, np.ones(k) / k, "valid").max()) if len(loud) >= k else float(loud.max())
    by_shot: dict[int, list[Track]] = defaultdict(list)
    for tr in tracks:
        by_shot[tr.shot].append(tr)
    turns: list[SpeakerTurn] = []
    for _, group in sorted(by_shot.items()):
        if len(group) >= 2:
            turns += _shot_turns(group, t_all, loud, peak, step, cuts)
    return turns


def _shot_turns(group, t_all, loud, peak, step, cuts) -> list[SpeakerTurn]:
    start = min(tr.times[0] for tr in group)
    end = max(tr.times[-1] for tr in group) + step
    pooled = np.concatenate([np.asarray(tr.mouth, dtype=float) for tr in group if tr.mouth])
    scale = float(np.percentile(pooled, 80)) if pooled.size else 0.0
    if scale <= EPS:
        return []
    votes: list[tuple[float, int | None]] = []
    a = start
    while a < end - EPS:
        b = a + WIN_S
        in_win = (t_all >= a - EPS) & (t_all < b - EPS)
        level = float(loud[in_win].mean()) if in_win.any() else 0.0
        votes.append((a, _winner(group, a, b, scale) if level >= AUDIO_FLOOR * peak else None))
        a = b
    switches: list[tuple[float, int]] = []
    run: tuple[int, int, float] | None = None  # (speaker, windows won, first window's start)
    for a, v in votes:
        if v is None:
            continue
        if not switches:
            switches.append((start, v))
        elif v == switches[-1][1]:
            run = None
        else:
            run = (v, run[1] + 1, run[2]) if run and run[0] == v else (v, 1, a)
            if run[1] >= HOLD_WINDOWS:
                switches.append((run[2], v))
                run = None
    if not switches:
        return []
    raw = [
        SpeakerTurn(start_s=s, end_s=switches[i + 1][0] if i + 1 < len(switches) else end, track=tid)
        for i, (s, tid) in enumerate(switches)
    ]
    kept = drop_short_turns(raw)
    for i in range(1, len(kept)):
        moved = max(kept[i - 1].start_s, switch_time(kept[i].start_s, cuts))
        kept[i - 1] = kept[i - 1].model_copy(update={"end_s": moved})
        kept[i] = kept[i].model_copy(update={"start_s": moved})
    return kept


def _winner(group: list[Track], a: float, b: float, scale: float) -> int | None:
    levels = []
    for tr in group:
        vals = [m for t, m in zip(tr.times, tr.mouth, strict=False) if a - EPS <= t < b - EPS]
        if vals:
            levels.append((float(np.mean(vals)) / scale, tr.id))
    if not levels:
        return None
    levels.sort(reverse=True)
    second = levels[1][0] if len(levels) > 1 else 0.0
    return levels[0][1] if levels[0][0] - second >= MARGIN else None
