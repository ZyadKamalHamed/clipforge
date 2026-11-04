"""Basic mode (SPEC 9.4): clips found and packaged without an LLM. The video's signals (replays, chat
bursts, energy, laughter and comments) make an interest curve, its peaks at least 20 s apart become windows
of 30 to 60 s snapped to sentences, and each clip's first sentence becomes its hook title, with generic
captions. Where no signal stands out the windows are spaced evenly. Every criterion scores a placeholder 5
until an LLM reads the clip."""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections import Counter

import numpy as np

from clipforge.brain.context import FOCUS_REACH_S, BrainContext
from clipforge.brain.schemas import Scores
from clipforge.brain.signals import EPS, covered, seconds_mean
from clipforge.brain.snap import DEDUPE_IOU, iou, target_count
from clipforge.brain.text import QUOTE_MARKS, plain_words
from clipforge.brain.wording import INSTAGRAM_TAGS, TIKTOK_TAGS, TITLE_WORDS, credit_line, cut_title, hashtags
from clipforge.models import Candidate, Packaging, Sentence, SentenceSignals, Signals, Transcript

SIGNAL_WEIGHTS = {"replays": 0.35, "chat bursts": 0.30, "energy": 0.25, "laughter": 0.20, "comments": 0.20}
SMOOTH_S = 9  # the curve is averaged over this many seconds
MIN_GAP_S = 20.0
LEAD_S = 8.0  # a window starts on the sentence going on this long before its peak
TAIL_S = 20.0  # and ends on the first sentence ending this long after it
MIN_S = 30.0
MAX_S = 60.0
PLACEHOLDER = 5  # each criterion's score until an LLM reads the clip
NO_SIGNAL_SCORE = 50.0  # a window's llm_score when nothing in the video stands out
MAX_NUMBERS = 3
# Words that only lead into a sentence, left off the start of a Basic mode hook title.
LEAD_WORDS = {"also", "and", "but", "like", "now", "oh", "ok", "okay", "so", "well", "yeah", "yes"}
KEYWORDS = 3
KEYWORD_LETTERS = 4
NUMBER_EDGES = ".,;:!?…()[]" + QUOTE_MARKS
EVEN_REASON = "Basic mode: spaced evenly, as no signal stands out here"
# Words too common to say what a clip is about. English only: in another language Basic mode's keywords
# are its most said words.
STOP_WORDS = {
    "about", "actually", "after", "again", "against", "almost", "already", "also", "although", "always",
    "another", "anyone", "anything", "anyway", "aren", "around", "back", "basically", "because", "been",
    "before", "being", "between", "both", "came", "cannot", "come", "could", "couldn", "didn", "does",
    "doesn", "doing", "done", "down", "during", "each", "else", "even", "ever", "every", "everyone",
    "everything", "from", "gets", "getting", "give", "going", "gonna", "good", "gotta", "great", "guys",
    "hadn", "hasn", "have", "haven", "having", "here", "into", "just", "kind", "kinda", "know", "like",
    "literally", "little", "look", "made", "make", "many", "maybe", "mean", "might", "more", "most", "much",
    "must", "need", "never", "nothing", "obviously", "okay", "only", "onto", "other", "over", "people",
    "pretty", "probably", "quite", "really", "right", "said", "same", "says", "should", "shouldn", "since",
    "some", "someone", "something", "sort", "still", "stuff", "such", "sure", "take", "than", "thank",
    "thanks", "that", "their", "them", "then", "there", "these", "they", "thing", "things", "think", "this",
    "those", "though", "through", "time", "today", "until", "upon", "very", "wanna", "want", "wasn", "well",
    "went", "were", "weren", "what", "when", "where", "whether", "which", "while", "will", "with", "within",
    "without", "would", "wouldn", "yeah", "your", "yours",
}  # fmt: skip
_NOTHING = SentenceSignals(
    energy_pct=0.0, heat_pct=None, comments_n=0, comments_w=0.0, chat_burst=False, events=[], rate_wps=0.0
)


def interest_curve(tr: Transcript, sig: Signals) -> np.ndarray:
    """How interesting each second of the video is, 0 to 1: the signals that vary, weighted (replays 0.35,
    chat bursts 0.30, energy 0.25, laughter and comments 0.20) and shared out over the ones found, then
    averaged over 9 s. It runs to the end of the media or of the last sentence, whichever is later."""
    return _curve(tr, _found(tr, sig))


def pick_peaks(curve: np.ndarray, n: int, min_gap_s: float = MIN_GAP_S) -> list[int]:
    """Up to ``n`` seconds where the curve is highest, highest first and the earliest first among equals,
    each at least ``min_gap_s`` from every one picked before it."""
    picked: list[int] = []
    blocked = np.zeros(len(curve), dtype=bool)
    for t in np.argsort(-curve, kind="stable"):
        if len(picked) >= n:
            break
        if not blocked[t]:
            picked.append(int(t))
            blocked[max(0, math.floor(t - min_gap_s) + 1) : math.ceil(t + min_gap_s)] = True
    return picked


def heuristic_candidates(ctx: BrainContext, t0: float = 0.0, t1: float | None = None) -> list[Candidate]:
    """Basic mode's candidates from the sentences starting from ``t0`` to before ``t1`` (to the end without
    it, or when it is past the media), most interesting first. Each peak of the interest curve there
    becomes a window of whole sentences, and a window overlapping a better one goes; where no signal
    varies, the windows are spaced evenly. As many as the stretch's length calls for (SPEC 7.5), and the
    best one within 3 minutes of the link's timestamp goes first."""
    tr, s = ctx.transcript, ctx.transcript.sentences
    to_end = t1 is None or t1 >= tr.duration
    end = _end(tr) if to_end else t1
    starts, ends = [x.start for x in s], [x.end for x in s]
    a = bisect_left(starts, t0)
    b = (len(s) if to_end else bisect_left(starts, end)) - 1
    if a > b:
        return []
    found = _found(tr, ctx.signals)
    curve = _curve(tr, found)
    n = target_count(end - t0, ctx.options.clip_count)
    lo, hi = ctx.length_range
    lo, hi = max(MIN_S, lo), min(MAX_S, hi)
    first = max(math.floor(t0), 0)
    part = curve[first : math.ceil(end)]
    if len(part) and part.max() > part.min():
        peaks = [first + t for t in pick_peaks(part, len(part))]
        reason = f"Basic mode: picked from the video's {_listed(list(found))}"
    else:
        span = s[b].end - s[a].start
        peaks = [s[a].start + (k + 0.5) * span / n for k in range(n)]
        reason = EVEN_REASON
    top = float(curve.max()) if len(curve) else 0.0
    windows: list[tuple[int, int, float]] = []  # first and last sentence, and the llm_score
    for peak in peaks:
        i0, i1 = _window(s, ends, a, b, peak, lo, hi)
        score = 100 * seconds_mean(curve, s[i0].start, s[i1].end) / top if top > 0 else NO_SIGNAL_SCORE
        windows.append((i0, i1, score))
    windows.sort(key=lambda w: w[2], reverse=True)
    kept: list[tuple[int, int, float]] = []
    for w in windows:
        if all(iou(_span(s, w), _span(s, k)) <= DEDUPE_IOU for k in kept):
            kept.append(w)
    if ctx.focus_s is not None:  # the best kept near the focus goes first, so the cut never loses it
        k = next((k for k, w in enumerate(kept) if _reaches(s, w, ctx.focus_s)), 0)
        kept.insert(0, kept.pop(k))
    return [
        Candidate(
            id=ctx.next_id(),
            start_sid=s[i0].id,
            end_sid=s[i1].id,
            start_s=s[i0].start,
            end_s=s[i1].end,
            scores=dict.fromkeys(Scores.model_fields, PLACEHOLDER),
            reason=reason,
            mode="basic",
            llm_score=score,
        )
        for i0, i1, score in kept[:n]
    ]


def heuristic_packaging(ctx: BrainContext, c: Candidate) -> Packaging:
    """Basic mode's packaging: the clip's first sentence of 3 words or more, fillers and a leading "And",
    "So" or the like cut, as its hook title
    and both captions; the numbers it says as the words to emphasise; its 3 most said words (common English
    ones aside) as keywords and hashtags; and the credit line."""
    tr = ctx.transcript
    sentences = tr.sentences[tr.index(c.start_sid) : tr.index(c.end_sid) + 1]
    spoken = [[w.text for w in tr.words[x.w0 : x.w1 + 1] if not w.filler] for x in sentences]
    title = _title(sentences, spoken)
    words = [word for said in spoken for word in said]
    keywords = _keywords(words)
    return Packaging(
        hook_title=title,
        emphasis=_numbers(words),
        tiktok_caption=title,
        tiktok_hashtags=hashtags(keywords, TIKTOK_TAGS),
        instagram_caption=title,
        instagram_hashtags=hashtags(keywords, INSTAGRAM_TAGS),
        keywords=keywords,
        credit=credit_line(ctx.meta, ctx.rights_basis, ctx.options),
        mode="basic",
    )


# The interest curve


def _found(tr: Transcript, sig: Signals) -> dict[str, list[float]]:
    """Each signal the video has, as a value from 0 to 1 for every sentence, in the order of
    ``SIGNAL_WEIGHTS``; comments count against the busiest bucket. A signal that is the same everywhere
    (energy when the audio is flat, say) can't say where to cut, so it is left out."""
    own = [sig.sentences.get(x.id, _NOTHING) for x in tr.sentences]
    weighed = sig.has_comments and sig.max_bucket_weight > 0
    values = {
        "replays": [o.heat_pct or 0.0 for o in own] if sig.has_heatmap else [],
        "chat bursts": [float(o.chat_burst) for o in own] if sig.has_chat else [],
        "energy": [o.energy_pct for o in own],
        "laughter": [float("laughter" in o.events) for o in own] if sig.has_events else [],
        "comments": [min(1.0, o.comments_w / sig.max_bucket_weight) for o in own] if weighed else [],
    }
    return {name: v for name, v in values.items() if v and max(v) > min(v)}


def _curve(tr: Transcript, found: dict[str, list[float]]) -> np.ndarray:
    """Each second at the highest value of every signal found over the sentences covering it, weighted and
    shared out, then averaged over 9 s (as if nothing were said around the media). Rounded, so the seconds
    of one plateau tie exactly."""
    n = max(math.ceil(_end(tr) - EPS), 0)
    raw = np.zeros(n)
    for name, values in found.items():
        part = np.zeros(n)
        for x, value in zip(tr.sentences, values, strict=True):
            seconds = covered(x.start, x.end)
            part[seconds] = np.maximum(part[seconds], value)
        raw += SIGNAL_WEIGHTS[name] * part
    if found:
        raw /= sum(SIGNAL_WEIGHTS[name] for name in found)
    if n == 0:
        return raw
    smooth = np.convolve(raw, np.ones(SMOOTH_S) / SMOOTH_S)[SMOOTH_S // 2 : SMOOTH_S // 2 + n]
    return np.round(smooth, 9)


def _end(tr: Transcript) -> float:
    """The end of the media or of the last sentence, whichever is later."""
    return max(tr.duration, tr.sentences[-1].end) if tr.sentences else tr.duration


def _listed(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


# Windows


def _window(
    s: list[Sentence], ends: list[float], a: int, b: int, peak: float, lo: float, hi: float
) -> tuple[int, int]:
    """The sentences around a peak, within sentences ``a`` to ``b``: from the one going on 8 s before it to
    the first ending 20 s after it and ``lo`` seconds on, then held to ``lo`` to ``hi`` seconds by whole
    sentences, dropped from the end while it stays long enough, or added before the start when the
    sentences run out."""
    i0 = bisect_right(ends, peak - LEAD_S, a, b)  # searching to b gives b at most, the stretch's last
    i1 = bisect_left(ends, max(peak + TAIL_S, s[i0].start + lo), i0, b)
    while i1 > i0 and _length(s, i0, i1) > hi and _length(s, i0, i1 - 1) >= lo:
        i1 -= 1
    while i0 > a and _length(s, i0, i1) < lo:
        i0 -= 1
    return i0, i1


def _length(s: list[Sentence], i0: int, i1: int) -> float:
    return round(s[i1].end - s[i0].start, 3)


def _span(s: list[Sentence], w: tuple[int, int, float]) -> tuple[float, float]:
    return s[w[0]].start, s[w[1]].end


def _reaches(s: list[Sentence], w: tuple[int, int, float], focus: float) -> bool:
    """Whether the window is within 3 minutes of the link's timestamp."""
    return s[w[0]].start - FOCUS_REACH_S <= focus <= s[w[1]].end + FOCUS_REACH_S


# Packaging


def _title(sentences: list[Sentence], spoken: list[list[str]]) -> str:
    """The first sentence of 3 words or more once its fillers are cut (else the first, as said when it is
    nothing but fillers), without the words that only lead into it while 3 are left, cut to a hook title
    and starting with a capital letter."""
    said = list(next((words for words in spoken if len(words) >= TITLE_WORDS[0]), spoken[0]))
    while len(said) > TITLE_WORDS[0] and plain_words(said[0]) in LEAD_WORDS:
        said.pop(0)
    title = cut_title(" ".join(said) or sentences[0].text)
    words = title.split()
    return title[0].upper() + title[1:] if words and words[0].islower() else title


def _numbers(words: list[str]) -> list[str]:
    """Up to 3 of the numbers said, each once and without the punctuation around it, in the order said."""
    out: list[str] = []
    seen: set[str] = set()
    for word in words:
        key = plain_words(word)
        if any(ch.isdigit() for ch in key) and key not in seen:
            seen.add(key)
            out.append(word.strip(NUMBER_EDGES))
    return out[:MAX_NUMBERS]


def _keywords(words: list[str]) -> list[str]:
    """The 3 words said most of 4 letters or more, common English words aside; among equals, the first
    said."""
    tokens = plain_words(" ".join(words)).split()
    counts = Counter(t for t in tokens if len(t) >= KEYWORD_LETTERS and t.isalpha() and t not in STOP_WORDS)
    return [word for word, _ in counts.most_common(KEYWORDS)]
