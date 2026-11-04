from typing import get_args

import pytest

from clipforge.brain.schemas import RedFlag
from clipforge.brain.score import (
    GATES,
    HARD_FLAGS,
    SOFT_PENALTY,
    WEIGHTS,
    final_score,
    llm_score,
    passes_gates,
    score_candidate,
    signal_score,
    tier,
)
from clipforge.models import Candidate, RangeSignals, SentenceSignals, Signals, Transcript
from tests.fakes import make_transcript, quiet_signals

TENS = {name: 10 for name in ("C1", "C2", "C3", "C4", "C5", "C6", "C7", "C9")}
MIXED = {"C1": 8, "C2": 7, "C3": 6, "C4": 9, "C5": 5, "C6": 6, "C7": 4, "C9": 3}
TR = make_transcript([f"Line number {k}." for k in range(1, 7)], seconds_each=10.0)  # one minute


def stretch(**fields) -> RangeSignals:
    """The signals of a stretch: nothing in it unless given."""
    base = {
        "heat_max_pct": None,
        "comments_weight": 0.0,
        "chat_burst": False,
        "energy_pct": 0.0,
        "laughter": False,
        "applause": False,
    }
    return RangeSignals(**(base | fields))


def source(**fields) -> Signals:
    """A source's signals: none of them unless given."""
    return quiet_signals().model_copy(update=fields)


def per_sentence(tr: Transcript, *, energy: float, heat: float | None = None, laughter=()) -> Signals:
    """Every sentence at ``energy`` and, with a heatmap, ``heat``; laughter after the sentences named."""
    own = {
        s.id: SentenceSignals(
            energy_pct=energy,
            heat_pct=heat,
            comments_n=0,
            comments_w=0.0,
            chat_burst=False,
            events=["laughter"] if s.id in laughter else [],
            rate_wps=2.5,
        )
        for s in tr.sentences
    }
    return source(has_heatmap=heat is not None, has_events=bool(laughter), sentences=own)


def cand(**fields) -> Candidate:
    """A candidate over S1 to S3 of TR, 0 to 30 s."""
    base = {
        "id": "k1",
        "start_sid": "S1",
        "end_sid": "S3",
        "start_s": 0.0,
        "end_s": 30.0,
        "scores": MIXED,
        "llm_score": 66.84,
    }
    return Candidate(**(base | fields))


# The rubric


def test_tables_match_the_spec():
    assert WEIGHTS == {"C1": 20, "C2": 15, "C3": 15, "C4": 15, "C5": 10, "C6": 10, "C7": 5, "C8": 5, "C9": 5}
    assert sum(WEIGHTS.values()) == 100
    assert GATES == {"C1": 6, "C2": 5, "C3": 5}
    assert SOFT_PENALTY == {
        "sponsor_read": 8,
        "crosstalk": 4,
        "early_profanity": 3,
        "long_monologue": 3,
        "jargon": 3,
        "over_90s_instagram": 5,
    }
    extra = {"too_short", "too_long", "incomplete_story", "duplicate"}  # from snapping, review and history
    assert set(HARD_FLAGS) == set(get_args(RedFlag)) | extra


def test_llm_score_values():
    assert llm_score(TENS) == 100.0  # without C8, over the 95 points the LLM scores
    assert llm_score(MIXED) == pytest.approx((160 + 105 + 90 + 135 + 50 + 60 + 20 + 15) * 10 / 95)  # 66.84
    assert llm_score(MIXED | {"C8": 10}) == pytest.approx((635 + 50) / 10)
    assert llm_score({"C1": 7}) == pytest.approx(70.0)
    assert llm_score({}) == 0.0 and llm_score({"C10": 9}) == 0.0


def test_gates():
    assert passes_gates({"C1": 6, "C2": 5, "C3": 5})
    assert not passes_gates({"C1": 5, "C2": 10, "C3": 10})
    assert not passes_gates({"C1": 10, "C2": 4, "C3": 10})
    assert not passes_gates({"C1": 10, "C2": 10, "C3": 4})
    assert not passes_gates({"C2": 10, "C3": 10})  # a criterion never scored doesn't pass


# Signals


def test_signal_score_renormalises_over_available():
    assert signal_score(stretch(energy_pct=0.6), source()) == pytest.approx(60.0)  # energy only
    assert signal_score(stretch(heat_max_pct=0.9, energy_pct=0.6), source()) == pytest.approx(
        60.0
    )  # no heatmap
    full = source(
        has_heatmap=True, has_chat=True, has_comments=True, has_events=True, max_bucket_weight=100.0
    )
    rs = stretch(heat_max_pct=0.9, chat_burst=True, comments_weight=50.0, energy_pct=0.6, laughter=True)
    expected = (0.40 * 90 + 0.30 * 100 + 0.25 * 50 + 0.20 * 60 + 0.15 * 100) / 1.30
    assert signal_score(rs, full) == pytest.approx(expected)
    calm = stretch(heat_max_pct=0.9, comments_weight=500.0, energy_pct=0.6)  # comments capped at the busiest
    expected = (0.40 * 90 + 0.30 * 0 + 0.25 * 100 + 0.20 * 60 + 0.15 * 0) / 1.30
    assert signal_score(calm, full) == pytest.approx(expected)
    unweighted = source(has_comments=True, max_bucket_weight=0.0)
    assert signal_score(stretch(comments_weight=5.0, energy_pct=0.6), unweighted) == pytest.approx(60.0)
    unread = source(has_comments=False, max_bucket_weight=10.0)  # comments count only when they were read
    assert signal_score(stretch(comments_weight=5.0, energy_pct=0.6), unread) == pytest.approx(60.0)
    assert signal_score(stretch(energy_pct=0.6), source(has_heatmap=True)) == pytest.approx(
        60.0
    )  # no rank given


# The final score


def test_final_score_blend_penalty_multipliers():
    heat = source(has_heatmap=True)
    rs = stretch(heat_max_pct=0.85, energy_pct=0.1)  # a signal score of (0.40 * 85 + 0.20 * 10) / 0.60
    assert signal_score(rs, heat) == pytest.approx(60.0)
    assert final_score(cand(rerank_score=80.0, soft_flags=["sponsor_read"]), rs, heat) == 73.7
    plain, calm = source(), stretch(energy_pct=0.6)  # a signal score of 60
    assert final_score(cand(rerank_score=80.0), calm, plain) == 75.0
    assert final_score(cand(rerank_score=80.0, notes=["over_90s_instagram"]), calm, plain) == 70.0
    assert (
        final_score(cand(rerank_score=80.0, soft_flags=["crosstalk", "jargon", "jargon"]), calm, plain)
        == 68.0
    )
    assert final_score(cand(), calm, plain) == round(0.75 * 66.84 + 15, 1)  # no rerank: the Discover score
    assert final_score(cand(rerank_score=0.0), calm, plain) == 15.0  # a rerank of 0 is a score
    hot = stretch(heat_max_pct=0.8, energy_pct=0.6)
    assert final_score(cand(rerank_score=80.0), hot, heat) == round((60 + 0.25 * (32 + 12) / 0.6) * 1.1, 1)
    warm = stretch(heat_max_pct=0.79, energy_pct=0.6)
    assert final_score(cand(rerank_score=80.0), warm, heat) == round(60 + 0.25 * (31.6 + 12) / 0.6, 1)
    assert final_score(cand(rerank_score=80.0), stretch(heat_max_pct=0.9, energy_pct=0.6), plain) == 75.0
    assert final_score(cand(rerank_score=80.0), calm, heat) == 75.0  # a heatmap, but no rank for the stretch


def test_final_score_funny_without_laughter():
    funny, events = cand(rerank_score=80.0, archetype="funny"), source(has_events=True)
    quiet = stretch(energy_pct=0.6)  # a signal score of 0.20 * 60 / 0.35
    assert final_score(funny, quiet, events) == round((60 + 0.25 * 12 / 0.35) * 0.9, 1)
    laughs = stretch(energy_pct=0.6, laughter=True)
    assert final_score(funny, laughs, events) == round(60 + 0.25 * 27 / 0.35, 1)
    assert final_score(funny, quiet, source()) == 75.0  # without caption events there is no telling
    assert final_score(cand(rerank_score=80.0, archetype="story"), quiet, events) == round(
        60 + 0.25 * 12 / 0.35, 1
    )


def test_final_score_stays_within_0_and_100():
    heat, top = source(has_heatmap=True), stretch(heat_max_pct=1.0, energy_pct=1.0)
    assert final_score(cand(rerank_score=100.0), top, heat) == 100.0
    flagged = cand(rerank_score=0.0, soft_flags=["sponsor_read", "crosstalk"])
    assert final_score(flagged, stretch(), source()) == 0.0


def test_tier_boundaries():
    assert [tier(f, True, []) for f in (72.0, 71.9, 60.0, 59.9)] == ["post", "test", "test", "weak"]
    assert tier(90.0, False, []) == "weak"  # a failed gate
    assert [tier(90.0, True, [flag]) for flag in sorted(HARD_FLAGS)] == ["no"] * len(HARD_FLAGS)
    assert tier(90.0, False, ["missing_context", "too_short"]) == "no"


def test_tier_blocks_a_red_flag_it_does_not_know(caplog):
    assert tier(90.0, True, ["too-short"]) == "no"  # misspelt by some later pass, and still a red flag
    assert "too-short" in caplog.text
    caplog.clear()
    assert tier(90.0, True, ["too_short"]) == "no" and caplog.text == ""


# All together


def test_score_candidate_sets_every_field():
    sig = per_sentence(TR, energy=0.6, heat=0.85)
    c = cand(rerank_score=80.0, soft_flags=["sponsor_read"])
    out = score_candidate(c, TR, sig)
    assert out.signal_score == pytest.approx((0.40 * 85 + 0.20 * 60) / 0.60)
    assert out.final_score == round((0.75 * 80 + 0.25 * out.signal_score - 8) * 1.1, 1)
    assert (out.passes_gates, out.tier) == (True, "post")
    assert c.final_score is None and c.tier is None  # the candidate given is left alone
    weak_hook = score_candidate(cand(scores=MIXED | {"C1": 5}, rerank_score=95.0), TR, sig)
    assert (weak_hook.passes_gates, weak_hook.tier) == (False, "weak")
    assert score_candidate(cand(rerank_score=95.0, red_flags=["too_short"]), TR, sig).tier == "no"
    edge = score_candidate(
        cand(rerank_score=75.95), TR, per_sentence(TR, energy=0.6)
    )  # 71.9625 before rounding
    assert (edge.final_score, edge.tier) == (72.0, "post")  # the tier goes by the score the card shows


def test_score_candidate_notes_the_multipliers():
    hot = per_sentence(TR, energy=0.6, heat=0.85)
    once = score_candidate(cand(rerank_score=80.0, notes=["trimmed_start"]), TR, hot)
    assert once.notes == ["trimmed_start", "heat_boost"]
    assert score_candidate(once, TR, hot).notes == ["trimmed_start", "heat_boost"]  # scored again, noted once
    assert score_candidate(once, TR, per_sentence(TR, energy=0.6, heat=0.5)).notes == ["trimmed_start"]
    funny = cand(rerank_score=80.0, archetype="funny")
    late = per_sentence(TR, energy=0.6, laughter=("S5",))  # laughter, but long after the clip
    assert score_candidate(funny, TR, late).notes == ["funny_no_laughter"]
    assert score_candidate(score_candidate(funny, TR, late), TR, late).notes == ["funny_no_laughter"]
    assert score_candidate(funny, TR, per_sentence(TR, energy=0.6, laughter=("S2",))).notes == []
    assert score_candidate(funny, TR, per_sentence(TR, energy=0.6)).notes == []  # no caption events at all


def test_score_candidate_leaves_basic_mode_gates_alone():
    basic = cand(mode="basic", scores=dict.fromkeys(TENS, 5), llm_score=80.0)  # Basic mode's placeholder 5s
    sig = per_sentence(TR, energy=0.6)
    out = score_candidate(basic, TR, sig)
    assert (out.passes_gates, out.final_score, out.tier) == (True, 75.0, "post")
    assert score_candidate(basic.model_copy(update={"passes_gates": False}), TR, sig).tier == "weak"
