"""The transcript as the LLM reads it (SPEC 9.1): one line per sentence with its id, times and signal tags,
chapter headings, and the windows a long video is read in (SPEC 9.2)."""

from __future__ import annotations

import re
from bisect import bisect_left
from dataclasses import dataclass

from clipforge.models import Sentence, Transcript

TAG_ORDER = ("{", "[replayed]", "[comments", "[chat burst]")  # then the caption events, such as [Laughter]
# A "<" that could start a closing tag: "<", its fullwidth or small form or an HTML entity for it, then a
# "/" or a look-alike, with only spaces or invisible characters between. Any tag name, so a look-alike
# spelling of "untrusted" closes nothing either.
_LT = r"(?:[<\uff1c\ufe64]|&(?:lt|#0*60|#x0*3c);?)"
_GAP = r"[\s\u00ad\u061c\u180e\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u206f\ufeff]*"
_SLASH = r"(?:[/\uff0f\u2044\u2215]|&(?:sol|#0*47|#x0*2f);?)"
CLOSING_TAG = re.compile(rf"{_LT}(?={_GAP}{_SLASH})", re.I)


def fmt_time(t: float) -> str:
    """``m:ss.s`` below an hour and ``h:mm:ss.s`` from an hour, to the nearest tenth of a second."""
    tenths = round(max(0.0, t) * 10)
    hours, rest = divmod(tenths, 36000)
    minutes, rest = divmod(rest, 600)
    seconds = f"{rest // 10:02d}.{rest % 10}"
    return f"{hours}:{minutes:02d}:{seconds}" if hours else f"{minutes}:{seconds}"


def _tag_rank(tag: str) -> int:
    return next((rank for rank, start in enumerate(TAG_ORDER) if tag.startswith(start)), len(TAG_ORDER))


def sentence_line(s: Sentence) -> str:
    """``S118 [14:02.1-14:07.9] {energetic} [replayed] So here's the thing...``, the tags in SPEC 9.1's
    order."""
    times = f"{s.id} [{fmt_time(s.start)}-{fmt_time(s.end)}]"
    return " ".join([times, *sorted(s.tags, key=_tag_rank), s.text])


def render_view(tr: Transcript, i0: int, i1: int, *, markers: dict[str, str] | None = None) -> str:
    """Sentences ``i0`` to ``i1``, one line each, with ``## Chapter: <title>`` where a chapter starts and
    above the first line when it has one. A marker's text goes after its sentence's line."""
    markers = markers or {}
    lines: list[str] = []
    chapter = None
    for s in tr.sentences[i0 : i1 + 1]:
        if s.chapter is not None and s.chapter != chapter:
            lines.append(f"## Chapter: {' '.join(s.chapter.split())}")
        chapter = s.chapter
        line = sentence_line(s)
        lines.append(f"{line} {markers[s.id]}" if s.id in markers else line)
    return "\n".join(lines)


@dataclass(frozen=True)
class Window:
    """One part of the video the LLM reads in one call: sentences ``i0`` to ``i1``."""

    index: int
    i0: int
    i1: int
    start_s: float
    end_s: float


def plan_windows(
    tr: Transcript,
    *,
    window_s: float = 900.0,
    overlap_s: float = 90.0,
    single_under_s: float = 1200.0,
    min_last_s: float = 300.0,
) -> list[Window]:
    """The parts a transcript is read in (SPEC 9.2). Under 20 minutes it is one part. Longer, the parts are
    15 minutes long and start every 13.5, so neighbours share 90 s, and a last part under 5 minutes joins
    the one before. A sentence belongs to every part its start falls in; a part with none is left out."""
    if window_s <= overlap_s:
        raise ValueError("a window must be longer than its overlap")
    if not tr.sentences:
        return []
    total = max(tr.duration, tr.sentences[-1].end)
    spans = [(0.0, total)]
    if total >= single_under_s:
        spans, start = [], 0.0
        while not spans or spans[-1][1] < total:
            spans.append((start, min(start + window_s, total)))
            start += window_s - overlap_s
        if len(spans) > 1 and spans[-1][1] - spans[-1][0] < min_last_s:
            spans[-2:] = [(spans[-2][0], total)]
    starts = [s.start for s in tr.sentences]
    windows: list[Window] = []
    for k, (t0, t1) in enumerate(spans):
        i0 = bisect_left(starts, t0)
        i1 = (len(starts) if k == len(spans) - 1 else bisect_left(starts, t1)) - 1
        if i0 <= i1:
            windows.append(Window(index=len(windows), i0=i0, i1=i1, start_s=t0, end_s=t1))
    return windows


def wrap_untrusted(kind: str, text: str) -> str:
    """``text`` inside an ``<untrusted kind="...">`` block. Every closing tag inside it, or anything a model
    could read as one, has its "<" swapped for "\u2039", so the block ends only where ClipForge ends it."""
    return f'<untrusted kind="{kind}">\n{CLOSING_TAG.sub("\u2039", text)}\n</untrusted>'
