"""Snap (SPEC 9.2 step 2): a candidate's sentences become times at word boundaries, its length is fitted to
the range by moving whole thoughts, overlapping candidates give way to the better one, and the source's
length sets how many clips to aim for (SPEC 7.5)."""

from __future__ import annotations

from clipforge.models import Candidate, Transcript

LEAD_MAX = 0.35  # seconds of room before the first word
TAIL_MAX = 0.45  # and after the last
WORD_GAP_S = 0.02  # never closer than this to a word outside the clip
TOO_SHORT_S = 12.0
TOO_LONG_S = 180.0
INSTAGRAM_MAX_S = 90.0
MAX_CLIPS = 40
DEDUPE_IOU = 0.3  # a candidate overlapping a better one by more than this goes
THOUGHT_SLACK_S = 10.0  # how far past the length range a clip may run to keep its thoughts whole


def snap_times(tr: Transcript, i0: int, i1: int, *, max_s: float | None = None) -> tuple[float, float]:
    """Start and end times for sentences ``i0`` to ``i1``: a little lead and tail, never into a word
    outside the clip and never past the media. When the words fit within ``max_s``, the lead and tail
    shrink alike so the clip does too."""
    w0, w1 = tr.sentences[i0].w0, tr.sentences[i1].w1
    first, last = tr.words[w0], tr.words[w1]
    start = first.start - LEAD_MAX
    if w0 > 0:
        start = max(start, tr.words[w0 - 1].end + WORD_GAP_S)
    start = min(max(start, 0.0), first.start)
    end = last.end + TAIL_MAX
    if w1 + 1 < len(tr.words):
        end = min(end, tr.words[w1 + 1].start - WORD_GAP_S)
    end = max(min(end, tr.duration), last.end)
    excess = end - start - max_s if max_s is not None and last.end - first.start <= max_s else 0.0
    if excess > 0:
        lead, tail = first.start - start, end - last.end
        start += excess * lead / (lead + tail)
        end -= excess * tail / (lead + tail)
    return start, end


def thought_start(tr: Transcript, i: int) -> int:
    """The first sentence of the thought sentence ``i`` belongs to: the one the sentences carrying on
    another lead back to."""
    while i > 0 and tr.sentences[i].cont:
        i -= 1
    return i


def thought_end(tr: Transcript, i: int) -> int:
    """The last sentence of the thought sentence ``i`` belongs to."""
    while i + 1 < len(tr.sentences) and tr.sentences[i + 1].cont:
        i += 1
    return i


def fit_duration(tr: Transcript, i0: int, i1: int, min_s: float, max_s: float) -> tuple[int, int, list[str]]:
    """Sentences ``i0`` to ``i1`` moved until the spoken span is within ``min_s`` to ``max_s``, a whole
    thought at a time: the clip is widened to the thoughts its ends are in, and may run 10 s past
    ``max_s`` to keep them whole unless it was past it already. A long clip loses thoughts from its start,
    which keeps the payoff, or sentences when its last thought alone is too long; a short one gains the
    next thought, or else the one before. The notes say what was done."""
    s = tr.sentences
    notes: list[str] = []

    def note(text: str) -> None:
        if text not in notes:
            notes.append(text)

    def span(a: int, b: int) -> float:
        return s[b].end - s[a].start

    o0, o1 = i0, i1
    limit = max_s + THOUGHT_SLACK_S if span(i0, i1) <= max_s else max_s
    a, b = thought_start(tr, i0), thought_end(tr, i1)
    if span(thought_start(tr, b), b) <= limit:  # the last thought fits, so every thought can stay whole
        while span(a, b) > limit:
            a = thought_end(tr, a) + 1
        i0, i1 = a, b
    else:  # it doesn't, so the clip is cut by sentences, from its start
        while span(i0, i1) > max_s and i0 < i1:
            i0 += 1
    if i0 < o0:
        note("completed_start")
    if i0 > o0:
        note("trimmed_start")
    if i1 > o1:
        note("completed_end")
    while span(i0, i1) < min_s:
        after = thought_end(tr, i1 + 1) if i1 + 1 < len(s) else None
        before = thought_start(tr, i0 - 1) if i0 > 0 else None
        if after is not None and span(i0, after) <= max_s:
            i1 = after
            note("extended_end")
        elif before is not None and span(before, i1) <= max_s:
            i0 = before
            note("extended_start")
        else:
            note("short")
            break
    return i0, i1, notes


def length_flags(start: float, end: float) -> tuple[list[str], list[str]]:
    """The red flags and notes a clip's length earns: under 12 s or over 180 s is a red flag, and over 90 s
    is long for Instagram (a soft flag, SPEC 9.3). The length counts to the millisecond, so a clip cut to
    exactly 90 s isn't over."""
    length = round(end - start, 3)
    red_flags = ["too_short"] if length < TOO_SHORT_S else []
    if length > TOO_LONG_S:
        red_flags.append("too_long")
    return red_flags, ["over_90s_instagram"] if length > INSTAGRAM_MAX_S else []


def iou(a: tuple[float, float], b: tuple[float, float]) -> float:
    """How much two time ranges overlap: their intersection over their union, 0 to 1."""
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    union = (a[1] - a[0]) + (b[1] - b[0]) - inter
    return inter / union if union > 0 else 0.0


def dedupe(cands: list[Candidate], threshold: float = DEDUPE_IOU) -> list[Candidate]:
    """The candidates best ``llm_score`` first, leaving out any that overlaps a better one by more than
    ``threshold`` IoU."""
    kept: list[Candidate] = []
    for c in sorted(cands, key=lambda c: c.llm_score, reverse=True):
        if all(iou((c.start_s, c.end_s), (k.start_s, k.end_s)) <= threshold for k in kept):
            kept.append(c)
    return kept


def target_count(duration_s: float, override: int | None = None) -> int:
    """How many clips to aim for (SPEC 7.5): about one a minute, 4 to 40, fewer under 4 minutes and one
    under 75 s. An override is kept to 1 to 40."""
    if override is not None:
        return min(MAX_CLIPS, max(1, override))
    if duration_s < 75:
        return 1
    per_minute = round(duration_s / 60)
    if duration_s < 240:
        return max(2, per_minute)
    return min(MAX_CLIPS, max(4, per_minute))


def snap_all(cands: list[Candidate], tr: Transcript, min_s: float, max_s: float) -> list[Candidate]:
    """Each candidate fitted to the length range, snapped to word times and flagged when its length won't
    do (``length_flags``). A cold open the fit moved out of the clip is dropped."""
    out = []
    for c in cands:
        i0, i1, notes = fit_duration(tr, tr.index(c.start_sid), tr.index(c.end_sid), min_s, max_s)
        start, end = snap_times(tr, i0, i1, max_s=max_s)
        flags, length_notes = length_flags(start, end)
        red_flags, notes = [*c.red_flags, *flags], [*c.notes, *notes, *length_notes]
        cold_open = c.cold_open_sid if c.cold_open_sid and _inside(tr, c.cold_open_sid, i0, i1) else None
        update = {
            "start_sid": tr.sentences[i0].id,
            "end_sid": tr.sentences[i1].id,
            "start_s": start,
            "end_s": end,
            "red_flags": list(dict.fromkeys(red_flags)),
            "notes": list(dict.fromkeys(notes)),
            "cold_open_sid": cold_open,
        }
        out.append(c.model_copy(update=update))
    return out


def _inside(tr: Transcript, sid: str, i0: int, i1: int) -> bool:
    try:
        return i0 <= tr.index(sid) <= i1
    except KeyError:
        return False
