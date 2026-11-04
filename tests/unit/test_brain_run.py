import re

from clipforge.brain.run import run_packaging, run_selection
from clipforge.models import ProjectOptions, Transcript
from tests.fakes import (
    TITLE,
    FakeProvider,
    brain_handlers,
    lines,
    make_context,
    make_transcript,
    rerank_about,
    review_answer,
)


def talk(n: int) -> Transcript:
    """``n`` sentences of 5 s each, back to back."""
    return make_transcript(lines(n))


def distorted(req):
    """A review that finds every clip changes the speaker's meaning."""
    answer = review_answer(req)
    for clip in answer["clips"]:
        clip["meaning_changed"] = True
    return answer


# Selection


def test_run_selection_in_basic_mode():
    ctx = make_context(talk(36))  # 3 minutes, so 3 clips
    out = run_selection(ctx)
    assert [c.id for c in out] == ["k1", "k2", "k3"]
    assert {c.mode for c in out} == {"basic"}
    assert [c.rerank_score for c in out] == [c.llm_score for c in out]  # nothing reranks without the LLM
    assert all(c.final_score is not None and c.tier is not None for c in out)


def test_run_selection_reranks_the_target_plus_10_best():
    fake = FakeProvider(brain_handlers())
    ctx = make_context(talk(252), provider=fake, options=ProjectOptions(clip_count=2))  # 14 candidates
    out = run_selection(ctx)
    (rerank,) = [r for r in fake.requests if r.pass_name == "rerank"]
    assert re.findall(r"^(k\d+) \[", rerank.prompt, re.M) == [f"k{n}" for n in range(1, 13)]
    assert [c.id for c in out] == ["k1", "k2"]  # the rerank put them first
    assert [c.rerank_score for c in out] == [80.0, 75.0]
    assert [c.final_score for c in out] == [60.0, 56.2]
    assert [c.tier for c in out] == ["test", "weak"]


def test_run_selection_puts_the_clips_on_the_topic_first():
    fake = FakeProvider(brain_handlers(rerank=rerank_about("k5", "k9")))
    ctx = make_context(talk(252), provider=fake, options=ProjectOptions(clip_count=4, prompt="the deadline"))
    out = run_selection(ctx)
    assert [c.id for c in out] == ["k5", "k9", "k1", "k2"]  # then the others, best first
    assert [c.on_topic for c in out] == [True, True, False, False]


def test_run_selection_asks_no_rerank_for_one_candidate():
    fake = FakeProvider(brain_handlers())
    ctx = make_context(talk(12), provider=fake)  # a minute: one clip, from S1 to S7
    (clip,) = run_selection(ctx)
    assert [r.pass_name for r in fake.requests] == ["discover"]
    assert (clip.start_sid, clip.end_sid) == ("S1", "S7")
    assert clip.rerank_score == clip.llm_score


def test_run_selection_notes_a_focus_with_nothing_near_it():
    ctx = make_context(talk(252), provider=FakeProvider(brain_handlers()), focus_s=1250.0)
    out = run_selection(ctx)
    assert out and max(c.end_s for c in out) < 1250.0 - 180.0  # the last moment found ends at 1045 s
    assert "focus_missed" in ctx.notes


def test_run_selection_keeps_the_best_clip_near_the_focus():
    fake = FakeProvider(brain_handlers())
    ctx = make_context(talk(252), provider=fake, options=ProjectOptions(clip_count=2), focus_s=812.0)
    out = run_selection(ctx)
    assert [c.start_sid for c in out] == ["S1", "S163"]  # S163 is the best of those within 3 minutes
    assert "focus_missed" not in ctx.notes


def test_run_selection_finds_a_focus_outside_the_rerank_pool():
    fake = FakeProvider(brain_handlers())
    ctx = make_context(talk(252), provider=fake, options=ProjectOptions(clip_count=1), focus_s=1100.0)
    out = run_selection(ctx)  # the pool is the 11 best, k1 to k11, and only k12 to k14 are near 1100 s
    assert [c.start_sid for c in out] == ["S183"]  # k12
    (rerank,) = [r for r in fake.requests if r.pass_name == "rerank"]
    assert re.findall(r"^(k\d+) \[", rerank.prompt, re.M) == [f"k{n}" for n in range(1, 13)]  # ranked too


# Packaging


def test_run_packaging_scores_again_after_the_review():
    fake = FakeProvider(brain_handlers(review=distorted))
    ctx = make_context(talk(36), provider=fake)
    cands = run_selection(ctx)
    assert len(cands) == 2 and "no" not in [c.tier for c in cands]
    out = run_packaging(ctx, cands)
    assert [c.id for c in out] == [c.id for c in cands]
    assert [c.packaging.hook_title for c in out] == [TITLE, TITLE]
    assert all("meaning_distortion" in c.red_flags for c in out)
    assert [c.tier for c in out] == ["no", "no"]  # scored again: a red flag blocks the clip
    assert [r.pass_name for r in fake.requests][-2:] == ["package", "review"]
