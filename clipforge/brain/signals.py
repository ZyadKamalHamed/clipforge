"""Signals (SPEC 7.4): cheap evidence about every sentence, computed before any LLM call and shown to the
person as evidence: how loud the delivery is, how often viewers replay it, what the comments point at,
laughter, applause and music in the captions, live chat bursts and how fast the speaker talks. The tags
reach the LLM inside the transcript."""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from itertools import accumulate

import numpy as np

from clipforge.ingest.youtube_signals import CapEvent, Hotspot
from clipforge.media.audio import percentile_ranks
from clipforge.models import Chapter, HeatBin, RangeSignals, SentenceSignals, Signals, Transcript

ENERGETIC_PCT = 0.85
SUBDUED_PCT = 0.15
MIN_SENTENCES = 8  # fewer sentences are too few to rank by energy
HEAT_PERCENTILE = 80
MIN_BINS = 10
HALO_S = 2.0  # an event this close to a sentence belongs to it: laughter follows the line
MIN_RATE_S = 0.5
EVENT_TAGS = {"laughter": "[Laughter]", "applause": "[Applause]", "music": "[Music]"}
EPS = 1e-6


def _overlaps(a0: float, a1: float, b0: float, b1: float) -> bool:
    """Whether two stretches share time; a stretch with no length is a moment."""
    return a0 < max(b1, b0 + EPS) and b0 < max(a1, a0 + EPS)


class _Spans:
    """Stretches of time sorted by start, so the ones touching a sentence are found without a full scan."""

    def __init__(self, spans: list[tuple[float, float]]) -> None:
        self.order = sorted(range(len(spans)), key=lambda k: spans[k][0])
        self.starts = [spans[k][0] for k in self.order]
        self.ends = [spans[k][1] for k in self.order]
        self.reach = list(
            accumulate((max(e, s + EPS) for s, e in zip(self.starts, self.ends, strict=True)), max)
        )

    def touching(self, a0: float, a1: float) -> list[int]:
        """Positions in the original list of the stretches that share time with [a0, a1]."""
        lo = bisect_right(self.reach, a0)  # every stretch before lo ends by a0
        hi = bisect_left(self.starts, max(a1, a0 + EPS))  # every stretch from hi on starts at or after a1
        return [self.order[k] for k in range(lo, hi) if _overlaps(self.starts[k], self.ends[k], a0, a1)]


def covered(start: float, end: float) -> slice:
    """The seconds a stretch covers, from the second it starts in to the second it ends in; ending right on
    a second's start doesn't cover that second, and a moment covers the second it falls in. A time within a
    microsecond of a whole second counts as on it, and nothing comes before second 0."""
    first = max(math.floor(start + EPS), 0)
    return slice(first, max(math.ceil(end - EPS), first + 1))


def seconds_mean(values: np.ndarray, start: float, end: float) -> float:
    """The mean of per-second ``values`` over the seconds a stretch covers, or 0 when it covers none."""
    part = values[covered(start, end)]
    return float(part.mean()) if len(part) else 0.0


def compute_signals(
    tr: Transcript,
    rms: np.ndarray,
    *,
    heatmap: list[HeatBin],
    events: list[CapEvent],
    hotspots: list[Hotspot],
    chapters: list[Chapter],
    chat_bursts: list[tuple[float, float]] | tuple = (),
) -> Signals:
    """Every sentence's signals and tags. ``chapters`` isn't used here: apply_tags sets each sentence's."""
    rms = np.asarray(rms, dtype=np.float64)
    sentences = tr.sentences
    energy = percentile_ranks([seconds_mean(rms, s.start, s.end) for s in sentences])
    values = [b.value for b in heatmap]
    heat_ranks = percentile_ranks(values)
    heat_p80 = float(np.percentile(values, HEAT_PERCENTILE)) if heatmap else None
    bins = _Spans([(b.start, b.end) for b in heatmap])
    buckets = _Spans([(h.start, h.end) for h in hotspots])
    sounds = _Spans([(e.start, e.end) for e in events])
    bursts = _Spans([(float(a), float(b)) for a, b in chat_bursts])
    out: dict[str, SentenceSignals] = {}
    for k, s in enumerate(sentences):
        tags = []
        if len(sentences) >= MIN_SENTENCES and energy[k] >= ENERGETIC_PCT - EPS:
            tags.append("{energetic}")
        elif len(sentences) >= MIN_SENTENCES and energy[k] <= SUBDUED_PCT + EPS:
            tags.append("{subdued}")
        heat_pct = None
        if heatmap:
            touched = bins.touching(s.start, s.end)
            heat_pct = max((float(heat_ranks[j]) for j in touched), default=0.0)
            if len(heatmap) >= MIN_BINS and any(values[j] >= heat_p80 - EPS for j in touched):
                tags.append("[replayed]")
        touched = [hotspots[j] for j in buckets.touching(s.start, s.end)]
        comments_n = max((h.count for h in touched if h.hot), default=0)
        if comments_n:
            tags.append(f"[comments: {comments_n}]")
        chat = bool(bursts.touching(s.start, s.end))
        if chat:
            tags.append("[chat burst]")
        heard = {events[j].kind for j in sounds.touching(s.start - HALO_S - EPS, s.end + HALO_S + EPS)}
        kinds = [kind for kind in EVENT_TAGS if kind in heard]
        tags += [EVENT_TAGS[kind] for kind in kinds]
        out[s.id] = SentenceSignals(
            energy_pct=float(energy[k]),
            heat_pct=heat_pct,
            comments_n=comments_n,
            comments_w=sum(h.weight for h in touched),
            chat_burst=chat,
            events=kinds,
            rate_wps=(s.w1 - s.w0 + 1) / max(MIN_RATE_S, s.end - s.start),
            tags=tags,
        )
    return Signals(
        has_heatmap=bool(heatmap),
        has_comments=any(h.hot for h in hotspots),
        has_chat=bool(chat_bursts),
        has_events=bool(events),
        rms=[round(float(x), 5) for x in rms],
        heat_p80=heat_p80,
        max_bucket_weight=max((h.weight for h in hotspots), default=0.0),
        comment_buckets=[(h.start, h.end, h.weight) for h in hotspots],
        sentences=out,
    )


def _chapter(chapters: list[Chapter], t: float) -> str | None:
    inside = [c for c in chapters if c.start <= t < c.end]
    return max(inside, key=lambda c: c.start).title if inside else None


def apply_tags(tr: Transcript, sig: Signals, chapters: list[Chapter]) -> Transcript:
    """A copy of the transcript with every sentence's tags and the chapter its start falls in."""
    sentences = []
    for s in tr.sentences:
        own = sig.sentences.get(s.id)
        update = {"tags": list(own.tags) if own else [], "chapter": _chapter(chapters, s.start)}
        sentences.append(s.model_copy(update=update))
    return tr.model_copy(update={"sentences": sentences})


def range_signals(tr: Transcript, sig: Signals, start_s: float, end_s: float) -> RangeSignals:
    """The signals of a stretch: its sentences' highest replay rank, the weight of every comment bucket it
    touches, any chat burst, the energy rank weighted by each sentence's time inside it, and laughter or
    applause."""
    inside = [(s, sig.sentences[s.id]) for s in tr.sentences if s.id in sig.sentences]
    inside = [(s, own) for s, own in inside if _overlaps(s.start, s.end, start_s, end_s)]
    heat = None
    if sig.has_heatmap:  # then every sentence has a heat_pct
        heat = max((own.heat_pct for _, own in inside), default=0.0)
    seconds = [max(0.0, min(s.end, end_s) - max(s.start, start_s)) for s, _ in inside]
    energies = [own.energy_pct for _, own in inside]
    if sum(seconds) > 0:
        energy = sum(t * e for t, e in zip(seconds, energies, strict=True)) / sum(seconds)
    else:
        energy = sum(energies) / len(energies) if energies else 0.0
    return RangeSignals(
        heat_max_pct=heat,
        comments_weight=sum(w for b0, b1, w in sig.comment_buckets if _overlaps(b0, b1, start_s, end_s)),
        chat_burst=any(own.chat_burst for _, own in inside),
        energy_pct=energy,
        laughter=any("laughter" in own.events for _, own in inside),
        applause=any("applause" in own.events for _, own in inside),
    )
