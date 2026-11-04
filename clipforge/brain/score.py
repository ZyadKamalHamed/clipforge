"""Scoring (SPEC 9.3): the rubric's weighted score and its gates, a score from the source's own signals, the
blend of the two less the soft flags' points and times the evidence multipliers, and the tier the card
shows."""

from __future__ import annotations

import logging
from typing import get_args

from clipforge.brain.schemas import RedFlag
from clipforge.brain.signals import range_signals
from clipforge.models import Candidate, RangeSignals, Signals, Transcript

log = logging.getLogger(__name__)

WEIGHTS = {"C1": 20, "C2": 15, "C3": 15, "C4": 15, "C5": 10, "C6": 10, "C7": 5, "C8": 5, "C9": 5}
GATES = {"C1": 6, "C2": 5, "C3": 5}  # the hook, standing alone and the payoff
SOFT_PENALTY = {
    "sponsor_read": 8,
    "crosstalk": 4,
    "early_profanity": 3,
    "long_monologue": 3,
    "jargon": 3,
    "over_90s_instagram": 5,  # a note from snapping rather than a flag from the LLM
}
HARD_FLAGS = frozenset(get_args(RedFlag)) | {"too_short", "too_long", "incomplete_story", "duplicate"}
LLM_SHARE = 0.75  # the signal score has the rest
HEAT_BOOST_PCT = 0.8  # a replay rank this high or higher
HEAT_BOOST = 1.1
NO_LAUGHTER = 0.9  # funny, with caption events, and no laughter
POST_AT = 72.0
TEST_AT = 60.0
MULTIPLIER_NOTES = ("funny_no_laughter", "heat_boost")


def llm_score(scores: dict[str, int]) -> float:
    """The rubric's weighted mean on 0 to 100 over the criteria scored; C8 comes later, from the frames."""
    used = {name: value for name, value in scores.items() if name in WEIGHTS}
    total = sum(WEIGHTS[name] for name in used)
    return sum(WEIGHTS[name] * value for name, value in used.items()) * 10 / total if total else 0.0


def passes_gates(scores: dict[str, int]) -> bool:
    """Whether each gated criterion reaches its floor; one never scored doesn't."""
    return all(scores.get(name, 0) >= floor for name, floor in GATES.items())


def signal_score(rs: RangeSignals, sig: Signals) -> float:
    """The stretch's signals on 0 to 100, with the weights shared out over the signals the source has: the
    replay rank 0.40, a chat burst 0.30, comments 0.25 (against the busiest bucket), energy 0.20 and laughter
    0.15. Energy is always there."""
    parts = [(0.20, 100 * rs.energy_pct)]
    if sig.has_heatmap and rs.heat_max_pct is not None:
        parts.append((0.40, 100 * rs.heat_max_pct))
    if sig.has_chat:
        parts.append((0.30, 100.0 if rs.chat_burst else 0.0))
    if sig.has_comments and sig.max_bucket_weight > 0:
        parts.append((0.25, 100 * min(1.0, rs.comments_weight / sig.max_bucket_weight)))
    if sig.has_events:
        parts.append((0.15, 100.0 if rs.laughter else 0.0))
    return sum(weight * value for weight, value in parts) / sum(weight for weight, _ in parts)


def final_score(c: Candidate, rs: RangeSignals, sig: Signals) -> float:
    """0.75 x the reranked score (the Discover score when there was no rerank) plus 0.25 x the signal score,
    less each soft flag's points once, times the multipliers that apply, within 0 to 100 and rounded to the
    one decimal the card shows."""
    base = c.rerank_score if c.rerank_score is not None else c.llm_score
    penalty = sum(SOFT_PENALTY.get(flag, 0) for flag in {*c.soft_flags, *c.notes})
    score = LLM_SHARE * base + (1 - LLM_SHARE) * signal_score(rs, sig) - penalty
    for _, factor in _multipliers(c, rs, sig):
        score *= factor
    return round(min(100.0, max(0.0, score)), 1)


def tier(final: float, gates_ok: bool, red_flags: list[str]) -> str:
    """The tier: "no" with any red flag, since every one is hard (SPEC 9.3), "weak" when a gate fails, else
    "post" from 72, "test" from 60 and "weak" below. A flag outside HARD_FLAGS is likely misspelt, so it is
    logged, and it blocks all the same."""
    if red_flags:
        unknown = sorted(set(red_flags) - HARD_FLAGS)
        if unknown:
            log.warning("tier: red flags %s aren't known ones, so they block the clip all the same", unknown)
        return "no"
    if not gates_ok:
        return "weak"
    if final >= POST_AT:
        return "post"
    return "test" if final >= TEST_AT else "weak"


def score_candidate(c: Candidate, tr: Transcript, sig: Signals) -> Candidate:
    """A copy with its signal score, gates, final score and tier set, and a note for each multiplier that
    applied; scoring again replaces those notes. Basic mode keeps the gate verdict it came with, since its
    criterion scores are placeholders."""
    rs = range_signals(tr, sig, c.start_s, c.end_s)
    gates_ok = c.passes_gates if c.mode == "basic" else passes_gates(c.scores)
    final = final_score(c, rs, sig)
    kept = [note for note in c.notes if note not in MULTIPLIER_NOTES]
    return c.model_copy(
        update={
            "signal_score": signal_score(rs, sig),
            "passes_gates": gates_ok,
            "final_score": final,
            "tier": tier(final, gates_ok, c.red_flags),
            "notes": kept + [note for note, _ in _multipliers(c, rs, sig)],
        }
    )


def _multipliers(c: Candidate, rs: RangeSignals, sig: Signals) -> list[tuple[str, float]]:
    """The evidence multipliers that apply, each with the note that names it."""
    out = []
    if c.archetype == "funny" and sig.has_events and not rs.laughter:
        out.append(("funny_no_laughter", NO_LAUGHTER))
    if sig.has_heatmap and rs.heat_max_pct is not None and rs.heat_max_pct >= HEAT_BOOST_PCT:
        out.append(("heat_boost", HEAT_BOOST))
    return out
