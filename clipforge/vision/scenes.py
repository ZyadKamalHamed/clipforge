"""Scene cuts (SPEC 7.6) from ffmpeg scdet's score of every frame on a 480 px copy: a score of 10 or more is a
cut, and so is a quieter change of 3 or more that stands alone, at least 15 times the mean score in the half
second each side and 3 times every other score within 0.1 s, as a cut between two similar shots of a dark
stage does. Cuts closer than 0.4 s are merged into the first."""

from __future__ import annotations

import re

import numpy as np

SCORE_RE = re.compile(r"pts_time:(-?[0-9.]+)\s*\n\s*lavfi\.scd\.score=(-?[0-9.]+)")
MIN_GAP_S = 0.4
CUT_SCORE = 10.0  # always a cut
QUIET_SCORE = 3.0  # the least a quieter cut scores
QUIET_RATIO = 15.0  # times the mean score within LEVEL_S
ALONE_RATIO = 3.0  # times every other score within ALONE_S
LEVEL_S = 0.5
ALONE_S = 0.1


def parse_scdet(text: str, *, offset_s: float) -> list[float]:
    """Cut times in source seconds from the scores ffmpeg's metadata filter printed. scdet counts from the
    start of the decode, which is ``offset_s`` into the source."""
    pairs = SCORE_RE.findall(text)
    cuts = find_cuts([float(t) for t, _ in pairs], [float(v) for _, v in pairs])
    return [round(offset_s + t, 6) for t in cuts]


def find_cuts(times: list[float], scores: list[float]) -> list[float]:
    """The times (in order) of the frames that are cuts, from each frame's scdet score."""
    if not scores:
        return []
    t, v = np.asarray(times, dtype=float), np.asarray(scores, dtype=float)
    total = np.concatenate([[0.0], np.cumsum(v)])
    lo, hi = np.searchsorted(t, t - LEVEL_S, "left"), np.searchsorted(t, t + LEVEL_S, "right")
    level = (total[hi] - total[lo] - v) / np.maximum(hi - lo - 1, 1)
    cuts = []
    for i in np.flatnonzero(v >= QUIET_SCORE):
        if v[i] >= CUT_SCORE:
            cuts.append(float(t[i]))
            continue
        near = np.abs(t - t[i]) <= ALONE_S
        near[i] = False
        alone = not near.any() or v[i] >= ALONE_RATIO * float(v[near].max())
        if alone and v[i] >= QUIET_RATIO * level[i]:
            cuts.append(float(t[i]))
    return cuts


def merge_cuts(cuts: list[float], min_gap_s: float = MIN_GAP_S) -> list[float]:
    """Sorted, dropping any cut within ``min_gap_s`` of the cut kept before it."""
    kept: list[float] = []
    for t in sorted(cuts):
        if not kept or t - kept[-1] >= min_gap_s:
            kept.append(t)
    return kept
