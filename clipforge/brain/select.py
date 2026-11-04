"""Discover (SPEC 9.2): the LLM reads the transcript a window at a time and proposes moments, and each one
is checked against the transcript before it becomes a candidate."""

from __future__ import annotations

import logging
import math
from collections.abc import Callable

from clipforge.brain.context import FOCUS_REACH_S, LIMIT_KINDS, BrainContext, ask_all, load_prompt
from clipforge.brain.heuristic import heuristic_candidates
from clipforge.brain.llm.base import LLMRequest
from clipforge.brain.llm.schema import schema_for
from clipforge.brain.schemas import DiscoverItem, DiscoverOut
from clipforge.brain.score import llm_score
from clipforge.brain.text import cleaned, plain_words, unquoted
from clipforge.brain.transcript_view import Window, fmt_time, plan_windows, render_view, wrap_untrusted
from clipforge.errors import LLMError
from clipforge.models import Candidate, Evidence, Transcript

log = logging.getLogger(__name__)

Fallback = Callable[[BrainContext, float, float], list[Candidate]]

QUOTED_FROM = 8  # a score this high needs the clip's own words behind it
UNQUOTED_MAX = 7
DESCRIPTION_CHARS = 1500
MAX_TITLES = 3
MAX_TAGS = 5
CONTENT_TYPES = {
    "auto": "not given",
    "podcast": "podcast or interview",
    "solo": "solo creator",
    "stream": "stream or gaming",
    "other": "other",
}


def per_window_max(window_minutes: float) -> int:
    """How many candidates one window may give (SPEC 9.2): 0.6 a minute, at least 2."""
    return max(2, math.ceil(round(0.6 * window_minutes, 6)))


def discover(ctx: BrainContext, *, fallback: Fallback = heuristic_candidates) -> list[Candidate]:
    """Candidates from every window, in window order and best first within each. Without the LLM the
    fallback (Basic mode) reads the whole video; a window the LLM can't answer goes to the fallback alone.
    A usage limit or a login problem stops the windows that haven't started, then waits or goes Basic."""
    tr = ctx.transcript
    if ctx.provider is None or ctx.llm_disabled:
        return fallback(ctx, 0.0, tr.duration)
    windows = plan_windows(tr)
    message = "Finding the best moments ({done} of {total} parts read)"
    answers = ask_all(ctx, [_request(ctx, w) for w in windows], DiscoverOut, message)
    out: list[Candidate] = []
    for w, answer in zip(windows, answers, strict=True):
        if isinstance(answer, DiscoverOut):
            out.extend(_candidates(ctx, w, answer.candidates))
            continue
        if isinstance(answer, LLMError) and answer.kind not in LIMIT_KINDS:
            log.warning("discover: part %d failed (%s), so Basic mode reads it", w.index + 1, answer.kind)
            ctx.on_llm_error(answer)
        out.extend(fallback(ctx, w.start_s, w.end_s))
    return out


# The question


def _request(ctx: BrainContext, w: Window) -> LLMRequest:
    lo, hi = ctx.length_range
    most = per_window_max((w.end_s - w.start_s) / 60)
    return LLMRequest(
        pass_name="discover",
        system=load_prompt("discover", min_s=f"{lo:g}", max_s=f"{hi:g}", max_n=most),
        prompt=_prompt(ctx, w),
        schema=schema_for(DiscoverOut),
        model=ctx.model("discover"),
        timeout_s=ctx.settings.llm_timeout_s,
    )


def _prompt(ctx: BrainContext, w: Window) -> str:
    meta, tr = ctx.meta, ctx.transcript
    facts = [
        f"Title: {meta.title}",
        f"Channel: {meta.channel or 'unknown'}",
        f"Content type: {CONTENT_TYPES[ctx.options.content_type]}",
        f"Duration: {fmt_time(meta.duration_s or tr.duration)}",
        f"Language: {tr.language or meta.language or 'unknown'}",
    ]
    if description := meta.description.strip()[:DESCRIPTION_CHARS]:
        facts.append(f"Description: {description}")
    parts = [
        wrap_untrusted("metadata", "\n".join(facts)),
        f"This part of the video runs from {fmt_time(w.start_s)} to {fmt_time(w.end_s)}.",
    ]
    if ctx.options.prompt:
        parts.append(f"{wrap_untrusted('user_topic', ctx.options.prompt)}\nPrefer moments about this topic.")
    focus = ctx.focus_s
    if focus is not None and w.start_s - FOCUS_REACH_S <= focus <= w.end_s + FOCUS_REACH_S:
        mark = fmt_time(round(focus))[:-2]  # whole seconds
        parts.append(f"The user marked {mark}. Include the best moment within 3 minutes of it.")
    parts.append(wrap_untrusted("transcript", render_view(tr, w.i0, w.i1)))
    return "\n\n".join(parts)


# Checking the answer


def _candidates(ctx: BrainContext, w: Window, items: list[DiscoverItem]) -> list[Candidate]:
    """The items that hold up, as candidates, at most ``per_window_max`` of them."""
    kept = []
    for item in items:
        span = _span(ctx.transcript, w, item.start_sid, item.end_sid)
        if span is None:
            log.info("discover: dropped %s to %s, not in part %d", item.start_sid, item.end_sid, w.index + 1)
            continue
        kept.append((item, span))
    most = per_window_max((w.end_s - w.start_s) / 60)
    return [_candidate(ctx, w, item, i0, i1) for item, (i0, i1) in kept[:most]]


def _sid(text: str) -> str:
    return text.strip().upper()


def _span(tr: Transcript, w: Window, start_sid: str, end_sid: str) -> tuple[int, int] | None:
    """Where the clip starts and ends, when both ids are sentences of the window in that order."""
    try:
        i0, i1 = tr.index(_sid(start_sid)), tr.index(_sid(end_sid))
    except KeyError:
        return None
    return (i0, i1) if w.i0 <= i0 <= i1 <= w.i1 else None


def _candidate(ctx: BrainContext, w: Window, item: DiscoverItem, i0: int, i1: int) -> Candidate:
    tr = ctx.transcript
    clip = f" {plain_words(tr.text(i0, i1))} "
    evidence: list[Evidence] = []
    for ev in item.evidence:
        quote = ev.quote.strip()
        words = plain_words(quote)
        found = Evidence(criterion=ev.criterion, quote=quote)
        if words and f" {words} " in clip and found not in evidence:
            evidence.append(found)
    quoted = {ev.criterion for ev in evidence}
    scores = {}
    for name, value in item.scores.model_dump().items():
        value = min(10, max(0, value))
        scores[name] = UNQUOTED_MAX if value >= QUOTED_FROM and name not in quoted else value
    return Candidate(
        id=ctx.next_id(),
        start_sid=tr.sentences[i0].id,
        end_sid=tr.sentences[i1].id,
        start_s=tr.sentences[i0].start,
        end_s=tr.sentences[i1].end,
        scores=scores,
        evidence=evidence,
        archetype=item.archetype,
        red_flags=list(dict.fromkeys(item.red_flags)),
        soft_flags=list(dict.fromkeys(item.soft_flags)),
        hook_titles=cleaned(item.hook_titles, unquoted)[:MAX_TITLES],
        topic_tags=cleaned(item.topic_tags, lambda t: t.strip().lstrip("#").lower())[:MAX_TAGS],
        cold_open_sid=_cold_open(tr, item.cold_open_sid, i0, i1),
        reason=" ".join(item.reason.split()),
        window=w.index,
        mode="llm",
        llm_score=llm_score(scores),
    )


def _cold_open(tr: Transcript, sid: str | None, i0: int, i1: int) -> str | None:
    """The cold open's sentence id when it is a sentence of the clip, else None."""
    if not sid:
        return None
    try:
        k = tr.index(_sid(sid))
    except KeyError:
        return None
    return tr.sentences[k].id if i0 <= k <= i1 else None
