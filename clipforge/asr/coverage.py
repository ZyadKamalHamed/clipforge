"""The coverage guard (SPEC 7.3): wherever YouTube's captions have words but the transcript has a gap of
more than 2 s, that stretch is heard again with beam search, and caption words fill whatever is still
missing, so a dropped sentence never silently disappears from the transcript.

A word belongs to a stretch of time when its middle is inside it. Where two passes meet, a word can be
heard by both: it is kept once."""

from __future__ import annotations

import logging
from bisect import bisect_left
from pathlib import Path

from clipforge.asr.base import ASRResult, RawWord, SpeechEngine
from clipforge.errors import ClipForgeError
from clipforge.ingest.youtube_signals import CapWord

log = logging.getLogger(__name__)

PAD_S = 3.0  # each gap is heard again with this much audio either side
MAX_WORD_S = 5.0  # no word lasts longer, so a word overlapping another starts at most this long before it
OVERLAP_S = 0.05  # two words sharing more time than this are the same stretch of speech
EPS = 1e-6


def _middle(word: RawWord | CapWord) -> float:
    return (word.start + word.end) / 2


def _inside(word: RawWord | CapWord, window: tuple[float, float]) -> bool:
    return window[0] <= _middle(word) < window[1]


def _letters(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _heard_twice(word: RawWord, kept: list[RawWord], starts: list[float]) -> bool:
    """Whether ``word`` is one of the ``kept`` words heard again: the same letters, sharing more than
    OVERLAP_S of its time. ``starts`` are the kept words' starts, in order."""
    letters = _letters(word.text)
    lo, hi = bisect_left(starts, word.start - MAX_WORD_S), bisect_left(starts, word.end)
    return any(
        _letters(k.text) == letters and min(k.end, word.end) - max(k.start, word.start) > OVERLAP_S
        for k in kept[lo:hi]
    )


def find_gaps(
    words: list[RawWord],
    ref: list[CapWord],
    duration: float,
    *,
    min_gap: float = 2.0,
    min_ref_words: int = 3,
) -> list[tuple[float, float]]:
    """Stretches of more than ``min_gap`` seconds without a transcript word, before the first word and after
    the last included, where the captions have at least ``min_ref_words`` words."""
    if not ref:
        return []
    middles = sorted(_middle(c) for c in ref)
    spans, covered = [], 0.0
    for word in sorted(words, key=lambda w: w.start):
        spans.append((covered, word.start))
        covered = max(covered, word.end)  # a long word covers the words inside it
    spans.append((covered, duration))
    return [
        (a, b)
        for a, b in spans
        if b - a > min_gap + EPS and bisect_left(middles, b) - bisect_left(middles, a) >= min_ref_words
    ]


def merge_words(base: list[RawWord], patch: list[RawWord], window: tuple[float, float]) -> list[RawWord]:
    """``base`` with its words inside ``window`` replaced by the ``patch`` words inside it, in time order,
    leaving out a patch word that is a kept word heard again."""
    kept = sorted((w for w in base if not _inside(w, window)), key=lambda w: w.start)
    starts = [w.start for w in kept]
    new = [w for w in patch if _inside(w, window) and not _heard_twice(w, kept, starts)]
    return sorted(kept + new, key=lambda w: w.start)


def fill_from_captions(
    words: list[RawWord], ref: list[CapWord], gaps: list[tuple[float, float]]
) -> tuple[list[RawWord], list[tuple[float, float]]]:
    """Caption words fill each gap, leaving out any that is a transcript word heard already. The gaps that
    got words come back as the caption-sourced ranges."""
    heard = sorted(words, key=lambda w: w.start)
    starts = [w.start for w in heard]
    filled, ranges = list(words), []
    for gap in gaps:
        inside = [RawWord(text=c.text, start=c.start, end=c.end) for c in ref if _inside(c, gap)]
        inside = [w for w in inside if not _heard_twice(w, heard, starts)]
        if inside:
            filled += inside
            ranges.append(gap)
    return sorted(filled, key=lambda w: w.start), ranges


def _hear_again(
    engine: SpeechEngine, wav: Path, windows: list[tuple[float, float]], language: str | None
) -> list[ASRResult]:
    many = getattr(engine, "transcribe_many", None)
    if many is not None:  # every window in one worker run, so the model loads once
        return many(wav, windows, language=language, beam=True)
    return [engine.transcribe(wav, language=language, window=window, beam=True) for window in windows]


def transcribe_with_guard(
    engine: SpeechEngine,
    wav: Path,
    *,
    language: str | None,
    captions: list[CapWord] | None,
    duration: float,
) -> tuple[ASRResult, list[tuple[float, float]]]:
    """A full pass; then, when there are captions, each gap is heard again with beam search, padded by 3 s
    either side, and merged in, and the gaps still open are filled from the captions. Returns the result
    and the caption-filled ranges."""
    result = engine.transcribe(wav, language=language)
    captions = captions or []
    gaps = find_gaps(result.words, captions, duration)
    if not gaps:
        return result, []
    windows = [(round(max(0.0, a - PAD_S), 3), round(min(duration, b + PAD_S), 3)) for a, b in gaps]
    try:
        patches = _hear_again(engine, wav, windows, language)
    except ClipForgeError as err:
        log.warning(
            "hearing %d gap(s) again failed (%s: %s), so YouTube's captions fill them",
            len(gaps),
            err.code,
            err,
        )
        patches = []
    words = list(result.words)
    for gap, patch in zip(gaps, patches, strict=False):
        words = merge_words(words, patch.words, gap)
    words, ranges = fill_from_captions(words, captions, find_gaps(words, captions, duration))
    return result.model_copy(update={"words": words}), ranges
