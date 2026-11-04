"""The brain's passes in order (SPEC 9.2): selection finds and ranks the moments, packaging writes their
titles and captions and has them reviewed."""

from __future__ import annotations

from clipforge.brain.context import FOCUS_REACH_S, BrainContext
from clipforge.brain.package import package
from clipforge.brain.rerank import rerank
from clipforge.brain.review import review
from clipforge.brain.score import score_candidate
from clipforge.brain.select import discover
from clipforge.brain.snap import dedupe, snap_all, target_count
from clipforge.models import Candidate

RERANK_EXTRA = 10  # the rerank sees this many candidates past the target


def run_selection(ctx: BrainContext) -> list[Candidate]:
    """The best ``target_count`` candidates, best first. Discover (or Basic mode) proposes them, snapping
    fits them to the words, and the rerank scores the best ``target + 10`` side by side. With a focus the
    best moment near it is always ranked, and kept in place of the last clip if none near it made the cut;
    when there is none, the note ``focus_missed`` says so. With a topic, the clips about it come first,
    and Not recommended clips come after the others."""
    tr = ctx.transcript
    cands = dedupe(snap_all(discover(ctx), tr, *ctx.length_range))
    n = target_count(tr.duration, ctx.options.clip_count)
    pool = cands[: n + RERANK_EXTRA]
    near = [c for c in cands if _near(c, ctx.focus_s)]
    if near and near[0].id not in {c.id for c in pool}:
        pool.append(near[0])
    scored = [score_candidate(c, tr, ctx.signals) for c in rerank(ctx, pool)]
    scored.sort(key=rank_key, reverse=True)
    out = scored[:n]
    if ctx.focus_s is None:
        return out
    close = [c for c in scored if _near(c, ctx.focus_s)]
    if not close:
        ctx.note("focus_missed")
    elif not any(_near(c, ctx.focus_s) for c in out):
        out = [*out[: n - 1], close[0]]
    return out


def rank_key(c: Candidate) -> tuple[bool, bool, float]:
    """Best first when sorted in reverse: Not recommended clips last, and with a topic asked for, the clips
    about it before the others; then the higher score."""
    return c.tier != "no", c.on_topic is not False, c.final_score or 0.0


def run_packaging(ctx: BrainContext, cands: list[Candidate]) -> list[Candidate]:
    """The candidates in the order given, packaged, reviewed and scored again, since the review may have
    added red flags and packaging may have moved the bounds."""
    tr = ctx.transcript
    return [score_candidate(c, tr, ctx.signals) for c in review(ctx, package(ctx, cands))]


def _near(c: Candidate, focus_s: float | None) -> bool:
    return focus_s is not None and c.start_s - FOCUS_REACH_S <= focus_s <= c.end_s + FOCUS_REACH_S
