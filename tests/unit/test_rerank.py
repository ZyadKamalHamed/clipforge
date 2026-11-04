import time

import pytest

from clipforge.brain.context import load_prompt
from clipforge.brain.llm.cache import LLMCache
from clipforge.brain.llm.schema import schema_for
from clipforge.brain.rerank import clip_text_for_rerank, rerank
from clipforge.brain.schemas import RerankOut
from clipforge.config import Settings
from clipforge.errors import LLMError, WaitUntil
from clipforge.models import Candidate, ProjectOptions
from tests.fakes import FakeProvider, make_context, make_transcript

TR = make_transcript(
    [
        "So here's the thing nobody tells you about deadlines.",  # S1, 0 to 10 s
        "If there's no deadline, the panic monster never shows up.",  # S2
        "And that's where the real trouble starts.",  # S3
        "I lost four million dollars in one afternoon.",  # S4
        "It was one email.",  # S5
        "Nobody read it before it went out.",  # S6, 50 to 60 s
    ],
    seconds_each=10.0,
)
SCORES = {"C1": 8, "C2": 7, "C3": 6, "C4": 5, "C5": 5, "C6": 5, "C7": 4, "C9": 3}


def cand(cid: str, first: int, last: int, llm_score: float, **fields) -> Candidate:
    """A candidate over sentences S<first> to S<last> of TR."""
    base = {
        "id": cid,
        "start_sid": f"S{first}",
        "end_sid": f"S{last}",
        "start_s": TR.sentences[first - 1].start,
        "end_s": TR.sentences[last - 1].end,
        "scores": SCORES,
        "archetype": "stakes_numbers",
        "hook_titles": ["$4M gone in one afternoon", "The email that cost $4M"],
        "llm_score": llm_score,
    }
    return Candidate(**(base | fields))


def ranking(*pairs):
    """An answer giving each (id, score) pair in turn."""
    return lambda req: {
        "ranking": [{"id": cid, "score": score, "why": "A sharp hook."} for cid, score in pairs]
    }


def run(handler, cands, **fields):
    provider = FakeProvider({"rerank": handler})
    ctx = make_context(TR, provider=provider, **fields)
    return rerank(ctx, cands), provider, ctx


# Scores


def test_rerank_applies_scores():
    cands = [cand("k1", 1, 3, 61.0), cand("k2", 4, 6, 70.0), cand("k3", 2, 3, 55.0)]
    out, provider, _ = run(ranking(("k2", 91), ("k1", 64), ("k3", 40)), cands)
    assert [(c.id, c.rerank_score) for c in out] == [("k1", 64.0), ("k2", 91.0), ("k3", 40.0)]  # order kept
    assert all(type(c.rerank_score) is float for c in out)
    assert [c.llm_score for c in out] == [61.0, 70.0, 55.0] and [c.notes for c in out] == [[], [], []]
    assert cands[0].rerank_score is None  # the candidates given are left alone
    (req,) = provider.requests
    assert (req.pass_name, req.model, req.schema) == ("rerank", "opus", schema_for(RerankOut))
    assert req.timeout_s == Settings().llm_timeout_s


def test_rerank_prompt_lists_every_candidate():
    bare = cand("k2", 4, 6, 70.0, scores={}, hook_titles=[], archetype="other")
    _, provider, _ = run(ranking(("k1", 64), ("k2", 91)), [cand("k1", 1, 3, 61.0), bare])
    (req,) = provider.requests
    assert req.system == load_prompt("rerank")
    for phrase in ("0 to 100", "80 to 95", "under 50", "Spread the scores", "not the title", "exactly once"):
        assert phrase in req.system
    assert (
        "k1 [0:00.0-0:30.0] 30.0 s, stakes_numbers, C1 8 C2 7 C3 6 C4 5 C5 5 C6 5 C7 4 C9 3\n" in req.prompt
    )
    assert '<untrusted kind="title">\n$4M gone in one afternoon\n</untrusted>' in req.prompt  # the first only
    assert "The email that cost" not in req.prompt
    assert f'<untrusted kind="transcript">\n{TR.text(0, 2)}\n</untrusted>' in req.prompt
    assert "k2 [0:30.0-1:00.0] 30.0 s, other, not scored\n" in req.prompt
    assert f'<untrusted kind="transcript">\n{TR.text(3, 5)}\n</untrusted>' in req.prompt
    assert req.prompt.count('<untrusted kind="title">') == 1  # k2 has no title to show
    assert req.prompt.index("k1 [") < req.prompt.index("k2 [")


def test_rerank_missing_and_duplicate_ids():
    cands = [cand("k1", 1, 3, 61.0), cand("k2", 4, 6, 70.0), cand("k3", 2, 3, 55.0), cand("k4", 1, 1, 50.0)]
    answer = ranking(("k9", 99), (" K2 ", 120), ("k2", 10), ("k3", -5), ("k1", 77))
    out, _, _ = run(answer, cands)
    assert [c.rerank_score for c in out] == [77.0, 100.0, 0.0, 50.0]  # k9 isn't there, k2 keeps its first
    assert [c.notes for c in out] == [[], [], [], ["rerank_missing"]]


# The topic asked for


def test_rerank_says_which_clips_are_about_the_topic_asked_for():
    cands = [cand("k1", 1, 3, 61.0), cand("k2", 4, 6, 70.0), cand("k3", 2, 3, 55.0)]
    answer = {
        "ranking": [
            {"id": "k1", "score": 64, "why": "Deadlines.", "on_topic": True},
            {"id": "k2", "score": 91, "why": "Money.", "on_topic": False},
        ]
    }
    out, provider, _ = run(lambda req: answer, cands, options=ProjectOptions(prompt="deadlines"))
    assert [c.on_topic for c in out] == [True, False, None]  # k3 isn't in the answer, so it isn't known
    assert [c.notes for c in out] == [[], ["off_topic"], ["rerank_missing"]]
    assert [c.rerank_score for c in out] == [64.0, 91.0, 55.0]  # the score is still how well it would do
    (req,) = provider.requests
    assert '<untrusted kind="user_topic">\ndeadlines\n</untrusted>' in req.prompt
    assert req.prompt.index("user_topic") < req.prompt.index("k1 [")
    assert "on_topic" in req.system


def test_rerank_without_a_topic_finds_no_clip_on_or_off_it():
    answer = {
        "ranking": [
            {"id": "k1", "score": 64, "why": "", "on_topic": False},
            {"id": "k2", "score": 91, "why": "", "on_topic": True},
        ]
    }
    out, provider, _ = run(lambda req: answer, [cand("k1", 1, 3, 61.0), cand("k2", 4, 6, 70.0)])
    assert [c.on_topic for c in out] == [None, None] and [c.notes for c in out] == [[], []]
    assert "user_topic" not in provider.requests[0].prompt


def test_rerank_error_falls_back():
    cands = [cand("k1", 1, 3, 61.0, notes=["trimmed_start"]), cand("k2", 4, 6, 70.0)]
    out, provider, ctx = run(lambda req: LLMError("timeout"), cands)
    assert [c.rerank_score for c in out] == [61.0, 70.0]
    assert [c.notes for c in out] == [["trimmed_start"], []]
    assert ctx.notes == ["timeout"] and len(provider.requests) == 2  # asked again once, then given up
    until = time.time() + 900
    with pytest.raises(WaitUntil) as caught:
        run(lambda req: LLMError("usage_limit", retry_at=until), cands)
    assert caught.value.until == until
    out, _, ctx = run(lambda req: LLMError("usage_limit"), cands, settings=Settings(on_llm_limit="basic"))
    assert [c.rerank_score for c in out] == [61.0, 70.0] and ctx.llm_disabled


def test_rerank_without_the_llm_copies_llm_score():
    one = [cand("k1", 1, 3, 61.0)]
    out, provider, _ = run(ranking(("k1", 90)), one)
    assert out[0].rerank_score == 61.0 and provider.requests == []  # one candidate has nothing to compare
    two = [cand("k1", 1, 3, 61.0), cand("k2", 4, 6, 70.0)]
    out, provider, _ = run(ranking(("k1", 90), ("k2", 80)), two, llm_disabled=True)
    assert [c.rerank_score for c in out] == [61.0, 70.0] and provider.requests == []
    basic = make_context(TR)
    assert [c.rerank_score for c in rerank(basic, two)] == [61.0, 70.0]
    assert rerank(basic, []) == []
    assert two[0].rerank_score is None


def test_rerank_answers_a_rerun_from_the_cache_and_logs_the_call(tmp_path):
    cands = [cand("k1", 1, 3, 61.0), cand("k2", 4, 6, 70.0)]
    provider = FakeProvider({"rerank": ranking(("k1", 64), ("k2", 91))})
    for _ in range(2):
        ctx = make_context(
            TR, provider=provider, cache=LLMCache(tmp_path / "cache"), log_dir=tmp_path / "log"
        )
        assert [c.rerank_score for c in rerank(ctx, cands)] == [64.0, 91.0]
    assert len(provider.requests) == 1  # the rerun cost nothing
    assert list((tmp_path / "log").glob("rerank-*.prompt.txt")) != []


# The clip's text


def test_rerank_text_truncates_long_clips():
    words = [f"w{k}" for k in range(1, 301)]
    tr = make_transcript([" ".join(words[k : k + 100]) for k in (0, 100, 200)], seconds_each=30.0)
    whole = Candidate(id="k1", start_sid="S1", end_sid="S3", start_s=0.0, end_s=90.0, scores={})
    assert clip_text_for_rerank(tr, whole) == " ".join(words[:150]) + " ... " + " ".join(words[-60:])
    assert clip_text_for_rerank(tr, whole, head_words=5, tail_words=2) == "w1 w2 w3 w4 w5 ... w299 w300"
    later = whole.model_copy(update={"id": "k2", "start_sid": "S2"})  # 200 words
    assert clip_text_for_rerank(tr, later) == " ".join(words[100:])
    edge = make_transcript([" ".join(words[:110]), " ".join(words[110:220]), "w221"], seconds_each=30.0)
    upto = whole.model_copy(update={"end_sid": "S2"})
    assert clip_text_for_rerank(edge, upto) == " ".join(words[:220])  # 150 + 60 + 10 words stay whole
    assert clip_text_for_rerank(edge, whole) == " ".join(words[:150]) + " ... " + " ".join(words[161:221])
    provider = FakeProvider({"rerank": ranking(("k1", 80), ("k2", 70))})
    rerank(make_context(tr, provider=provider), [whole, later])
    assert f'<untrusted kind="transcript">\n{clip_text_for_rerank(tr, whole)}\n</untrusted>' in (
        provider.requests[0].prompt
    )
