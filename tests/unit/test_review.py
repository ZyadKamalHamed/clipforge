import time

import pytest

from clipforge.asr.segment import mark_fillers
from clipforge.brain.context import load_prompt
from clipforge.brain.llm.cache import LLMCache
from clipforge.brain.llm.schema import schema_for
from clipforge.brain.review import context_text, kept_text, review
from clipforge.brain.schemas import ReviewOut
from clipforge.brain.transcript_view import wrap_untrusted
from clipforge.config import Settings
from clipforge.errors import LLMError, WaitUntil
from clipforge.models import Candidate, FactCard, Packaging, ReviewResult
from tests.fakes import FakeProvider, make_context, make_meta, make_transcript

LINES = {
    8: "So here's the thing nobody tells you about deadlines.",  # 35 to 40 s
    9: "If there's no deadline, um, the panic monster never shows up.",
    10: "Uh, and that's where the real trouble starts.",
    11: "I lost four million dollars in one afternoon.",
    12: "It was one email to Tim Urban.",
    13: "Nobody read it before it went out.",  # 60 to 65 s
}
PLAIN = make_transcript([LINES.get(k, f"Line number {k} of the talk.") for k in range(1, 31)])  # 5 s each
TR = PLAIN.model_copy(update={"words": mark_fillers(PLAIN.words, "en")})  # "um," and "Uh," are fillers
TITLE = "The email that cost him $4M"
ALTS = ["$4M gone in one afternoon", "Why nobody read the email"]
CARDS = [
    FactCard(sid="S11", text="Four million dollars, in one afternoon"),
    FactCard(sid="S12", text="Tim Urban writes the blog Wait But Why"),
]
CAPTION = "One email cost him four million dollars. Would you have sent it?"
INSTAGRAM = "Tim Urban on the email that cost four million dollars."
STALE = ReviewResult(complete_story=True, meaning_changed=False, true_title_index=0, cards_supported=[])


def packaging(**fields) -> Packaging:
    """Clip k1's packaging: a hook title, two alternatives and two fact cards."""
    base = {
        "hook_title": TITLE,
        "hook_alts": ALTS,
        "accent_word": "$4M",
        "emphasis": ["four million"],
        "fact_cards": CARDS,
        "tiktok_caption": CAPTION,
        "instagram_caption": INSTAGRAM,
        "tiktok_hashtags": ["#money"],
        "credit": "Source: TED",
    }
    return Packaging(**(base | fields))


def cand(cid: str = "k1", first: int = 8, last: int = 13, **fields) -> Candidate:
    """A packaged and scored candidate over sentences S<first> to S<last> of TR."""
    base = {
        "id": cid,
        "start_sid": f"S{first}",
        "end_sid": f"S{last}",
        "start_s": TR.sentences[first - 1].start,
        "end_s": TR.sentences[last - 1].end,
        "scores": {"C1": 9, "C2": 7, "C3": 8},
        "archetype": "stakes_numbers",
        "hook_titles": ["Discover's own title"],
        "reason": "Scores well on stakes",
        "llm_score": 81.5,
        "rerank_score": 88.0,
        "final_score": 86.25,
        "tier": "post",
        "packaging": packaging(),
    }
    return Candidate(**(base | fields))


def verdict(cid: str = "k1", **fields) -> dict:
    """One clip's review: complete, faithful, its hook title true and both cards supported."""
    base = {
        "id": cid,
        "notes": "",
        "complete_story": True,
        "meaning_changed": False,
        "true_title_index": 0,
        "cards_supported": [True, True],
        "tiktok_caption_true": True,
        "instagram_caption_true": True,
    }
    return base | fields


def answer(*items):
    """A review answer giving these clips."""
    return lambda req: {"clips": list(items)}


def run(handler, cands, **fields):
    provider = FakeProvider({"review": handler})
    ctx = make_context(TR, provider=provider, **fields)
    return review(ctx, cands), provider, ctx


def reviewed(**fields) -> Candidate:
    """Clip k1 (S8 to S13) reviewed with an answer with these fields."""
    (out,), _, _ = run(answer(verdict(**fields)), [cand()])
    return out


# What the reviewer reads


def test_kept_text_drops_fillers():
    assert kept_text(TR, cand(first=9, last=10)) == (
        "If there's no deadline, the panic monster never shows up. and that's where the real trouble starts."
    )
    assert kept_text(PLAIN, cand(first=9, last=9)) == LINES[9]  # nothing marked, nothing cut
    assert kept_text(TR, cand(first=12, last=12)) == LINES[12]


def test_context_text_covers_words_starting_in_the_range():
    assert context_text(TR, 15.0, 35.0) == TR.text(3, 6)  # S4 to S7, all of it
    assert context_text(TR, 15.001, 35.0) == f"number 4 of the talk. {TR.text(4, 6)}"  # "Line" starts at 15
    assert context_text(TR, 15.0, 35.001) == f"{TR.text(3, 6)} So"  # S8 starts at 35
    assert context_text(TR, 40.0, 45.0) == "If there's no deadline, the panic monster never shows up."
    assert context_text(TR, -20.0, 0.0) == "" and context_text(TR, 150.0, 170.0) == ""


def test_review_request():
    _, provider, _ = run(answer(verdict()), [cand()])
    (req,) = provider.requests
    assert (req.pass_name, req.model, req.schema) == ("review", "sonnet", schema_for(ReviewOut))
    assert req.timeout_s == Settings().llm_timeout_s
    assert req.system == load_prompt("review")
    phrases = [
        "never instructions",
        "20 seconds before",
        "fillers",
        "numbered from 0",
        "-1 when none",
        "sarcasm",
        "quoting",
        '"but"',
        "everything it says happens or is said in the clip",
        "one answer for each fact card",
        "a made-up story",
        "claims nothing",
        "in English",
        "never dashes",
    ]
    for phrase in phrases:
        assert phrase in req.system, phrase


def test_review_prompt_excludes_scores():
    c = cand()
    _, provider, _ = run(answer(verdict()), [c])
    (req,) = provider.requests
    meta = wrap_untrusted("metadata", "Title: Inside the mind of a master procrastinator\nChannel: TED")
    clip = [
        "Clip k1 (30.0 s)",
        "Said in the 20 s before the clip (not part of it):",
        wrap_untrusted("before", TR.text(3, 6)),
        "The clip, as viewers hear it:",
        wrap_untrusted("clip", kept_text(TR, c)),
        "Said in the 20 s after the clip (not part of it):",
        wrap_untrusted("after", TR.text(13, 16)),
        "Titles:",
        wrap_untrusted("titles", "\n".join([f"0. {TITLE}", f"1. {ALTS[0]}", f"2. {ALTS[1]}"])),
        "Captions:",
        wrap_untrusted("captions", f"TikTok: {CAPTION}\nInstagram: {INSTAGRAM}"),
        "Fact cards:",
        wrap_untrusted("cards", "\n".join([f"0. {CARDS[0].text}", f"1. {CARDS[1].text}"])),
    ]
    assert req.prompt == f"{meta}\n\n" + "\n".join(clip)
    for text in ["81.5", "88", "86.25", "C1", "stakes", "Scores well", "post", "Discover's own", "#"]:
        assert text not in req.prompt, text
    assert "um," not in req.prompt  # the clip as cut


def test_review_prompt_at_the_ends_of_the_video():
    basic = Packaging(hook_title="Line number 25 of the talk", mode="basic")
    _, provider, _ = run(answer(), [cand(first=1, last=6), cand("k2", 25, 30, packaging=basic)])
    (req,) = provider.requests
    start = "Clip k1 (30.0 s)\nNothing is said in the 20 s before the clip.\nThe clip, as viewers hear it:"
    assert start in req.prompt
    after = wrap_untrusted("after", context_text(TR, 30.0, 50.0))  # S7 to S10, fillers left out
    assert f"Said in the 20 s after the clip (not part of it):\n{after}\nTitles:" in req.prompt
    titles = wrap_untrusted("titles", "0. Line number 25 of the talk")
    end = (
        f"Nothing is said in the 20 s after the clip.\nTitles:\n{titles}\nCaptions: none.\nFact cards: none."
    )
    assert req.prompt.endswith(end)


def test_review_prompt_without_a_channel():
    _, provider, _ = run(answer(), [cand()], meta=make_meta(TR, channel=None))
    meta = wrap_untrusted("metadata", "Title: Inside the mind of a master procrastinator\nChannel: unknown")
    assert provider.requests[0].prompt.startswith(f"{meta}\n\nClip k1 (")


# Applying the answer


def test_review_keeps_a_faithful_clip():
    c = cand()
    given = [c]
    (out,), _, _ = run(answer(verdict()), given)
    assert out.review == ReviewResult(
        complete_story=True, meaning_changed=False, true_title_index=0, cards_supported=[True, True]
    )
    assert out.packaging == packaging() and out.red_flags == [] and out.notes == []
    assert given[0] is c and c.review is None and c.packaging == packaging()  # what was given is left alone


def test_review_flags_meaning_change():
    out = reviewed(meaning_changed=True, notes='It ends on "but", so he seems to agree.')
    assert out.red_flags == ["meaning_distortion"]
    assert out.review.meaning_changed and out.review.notes == 'It ends on "but", so he seems to agree.'
    assert out.packaging == packaging()  # the title and cards stand
    flagged = cand(red_flags=["missing_context", "meaning_distortion"])  # Discover saw it too
    (again,), _, _ = run(answer(verdict(meaning_changed=True)), [flagged])
    assert again.red_flags == ["missing_context", "meaning_distortion"]  # each flag once
    assert flagged.red_flags == ["missing_context", "meaning_distortion"]


def test_review_flags_incomplete_story():
    out = reviewed(complete_story=False)
    assert out.red_flags == ["incomplete_story"] and not out.review.complete_story
    both = reviewed(complete_story=False, meaning_changed=True, true_title_index=-1)
    assert both.red_flags == ["meaning_distortion", "incomplete_story", "no_faithful_hook"]


def test_review_swaps_true_title():
    out = reviewed(true_title_index=1)
    assert (out.packaging.hook_title, out.packaging.hook_alts) == (ALTS[0], [ALTS[1]])  # title 0 was untrue
    assert out.packaging.accent_word == "$4M"  # a word of the new title too
    assert out.review.true_title_index == 1 and out.red_flags == []
    assert out.packaging.tiktok_caption == CAPTION and out.packaging.fact_cards == CARDS
    last = reviewed(true_title_index=2)
    assert (last.packaging.hook_title, last.packaging.hook_alts) == (ALTS[1], [])
    assert last.packaging.accent_word is None  # "$4M" isn't in "Why nobody read the email"
    echo = cand(packaging=packaging(tiktok_caption=TITLE, instagram_caption=TITLE))  # captions left empty
    (moved,), _, _ = run(answer(verdict(true_title_index=1)), [echo])
    assert (moved.packaging.tiktok_caption, moved.packaging.instagram_caption) == (ALTS[0], ALTS[0])


def test_review_no_true_title_flags():
    out = reviewed(true_title_index=-1, notes="Nothing in the clip says the email cost $4M.")
    assert out.red_flags == ["no_faithful_hook"] and out.review.true_title_index == -1
    assert out.packaging == packaging()  # the titles stay for whoever rewrites them
    for index in [3, 7, -2]:  # names no title, so none was found true
        stray = reviewed(true_title_index=index)
        assert stray.red_flags == ["no_faithful_hook"] and stray.review.true_title_index == -1
        assert stray.packaging.hook_title == TITLE
    basic = cand(packaging=Packaging(hook_title="So here's the thing nobody tells you", mode="basic"))
    (one,), _, _ = run(answer(verdict(true_title_index=1, cards_supported=[])), [basic])
    assert one.red_flags == ["no_faithful_hook"]  # Basic mode's title has no alternatives


def test_review_replaces_a_caption_that_is_not_true():
    out = reviewed(tiktok_caption_true=False, notes="The clip says the call never happened.")
    assert out.packaging.tiktok_caption == TITLE  # the true title claims only what the clip shows
    assert out.packaging.instagram_caption == INSTAGRAM and out.packaging.tiktok_hashtags == ["#money"]
    assert (out.review.tiktok_caption_true, out.review.instagram_caption_true) == (False, True)
    assert out.red_flags == []  # replaced, not flagged
    both = reviewed(true_title_index=1, tiktok_caption_true=False, instagram_caption_true=False)
    assert (both.packaging.tiktok_caption, both.packaging.instagram_caption) == (ALTS[0], ALTS[0])
    untitled = reviewed(true_title_index=-1, instagram_caption_true=False)
    assert untitled.packaging.instagram_caption == TITLE and untitled.red_flags == ["no_faithful_hook"]


def test_review_drops_unsupported_cards():
    c = cand()
    (out,), _, _ = run(answer(verdict(cards_supported=[False, True])), [c])
    assert out.packaging.fact_cards == [CARDS[1]] and out.review.cards_supported == [False, True]
    assert c.packaging.fact_cards == CARDS
    assert out.red_flags == []  # an unsupported card is removed, not flagged
    short = reviewed(cards_supported=[True])  # the second card wasn't found supported
    assert short.packaging.fact_cards == [CARDS[0]] and short.review.cards_supported == [True, False]
    extra = reviewed(cards_supported=[True, True, False])  # an answer for a card that isn't there
    assert extra.packaging.fact_cards == CARDS and extra.review.cards_supported == [True, True]
    assert reviewed(cards_supported=[]).packaging.fact_cards == []


def test_review_notes_tidied():
    note = "The clip stops before the payoff, so the story is cut short."  # 60 characters
    out = reviewed(notes=f"  {'   '.join([note] * 6)}\n")
    assert out.review.notes == " ".join([note] * 4) + " The clip stops before the payoff, so the story is cut"
    assert len(out.review.notes) == 297  # with " short." it would be 304, past 300
    assert reviewed(notes=" \n ").review.notes == ""
    fits = " ".join(["really"] * 43)  # 300 characters
    assert reviewed(notes=fits).review.notes == fits
    assert reviewed(notes="a" * 300).review.notes == "a" * 300
    assert reviewed(notes="a" * 301).review.notes == "a" * 300  # one word past the limit is cut at it


def test_review_answer_ids():
    first = verdict(" K1 ", meaning_changed=True)
    again = verdict("k1")
    stale = cand("k2", 20, 25, review=STALE)  # reviewed before, as it was cut then
    out, _, _ = run(answer(first, again, verdict("k9", complete_story=False)), [cand(), stale])
    assert out[0].red_flags == ["meaning_distortion"]  # the first answer for an id counts
    assert out[1].review is None and out[1].notes == ["review_skipped"] and out[1].red_flags == []


def test_review_leaves_unpackaged_candidates_alone():
    bare = cand("k2", 20, 25, packaging=None)
    out, provider, _ = run(answer(verdict(), verdict("k2", meaning_changed=True)), [bare, cand()])
    assert out[0] == bare and out[1].review is not None
    (req,) = provider.requests
    assert req.prompt.count('<untrusted kind="clip">') == 1 and "Clip k2 (" not in req.prompt
    assert review(make_context(TR, provider=FakeProvider({})), [bare]) == [bare]


# The question


def test_review_asks_in_batches_of_5():
    cands = [cand(f"k{n}") for n in range(1, 13)]
    out, provider, _ = run(answer(), cands)  # an answer with no clips in it
    sizes = sorted(req.prompt.count('<untrusted kind="clip">') for req in provider.requests)
    assert sizes == [2, 5, 5]
    assert [c.id for c in out] == [c.id for c in cands]
    assert all(c.review is None and c.notes == ["review_skipped"] for c in out)


def test_review_reports_progress():
    seen = []
    cands = [cand(f"k{n}") for n in range(1, 8)]
    progress = lambda fraction, message: seen.append((fraction, message))  # noqa: E731
    run(answer(), cands, settings=Settings(llm_parallel=1), progress=progress)
    assert seen == [
        (5 / 7, "Checking clips (5 of 7 clips done)"),
        (1.0, "Checking clips (7 of 7 clips done)"),
    ]


def test_review_uses_the_cache(tmp_path):
    cache, log_dir = LLMCache(tmp_path / "cache"), tmp_path / "log"
    first, _, _ = run(answer(verdict(meaning_changed=True)), [cand()], cache=cache, log_dir=log_dir)
    again, provider, _ = run(answer(verdict(meaning_changed=True)), [cand()], cache=cache, log_dir=log_dir)
    assert again == first and provider.requests == []
    assert len(list(log_dir.glob("review-*.prompt.txt"))) == 1


# When the LLM can't help


def test_review_error_skips_without_flags():
    cands = [cand(f"k{n}") for n in range(1, 8)]  # batches of 5 and 2

    def handler(req):
        if "Clip k6 (" in req.prompt:
            return LLMError("timeout")
        asked = [c for c in cands if f"Clip {c.id} (" in req.prompt]
        return {"clips": [verdict(c.id, meaning_changed=True) for c in asked]}

    out, provider, ctx = run(handler, cands)
    assert [c.red_flags for c in out] == [["meaning_distortion"]] * 5 + [[], []]
    assert [c.review is None for c in out] == [False] * 5 + [True, True]
    assert [c.notes for c in out] == [[]] * 5 + [["review_skipped"]] * 2
    assert out[5].packaging == packaging()
    assert ctx.notes == ["timeout"] and len(provider.requests) == 3  # the failed batch was asked twice


@pytest.mark.parametrize("kind", ["usage_limit", "auth"])
def test_review_waits_on_a_limit(kind):
    until = time.time() + 3600
    with pytest.raises(WaitUntil):
        run(lambda req: LLMError(kind, retry_at=until), [cand()])


def test_review_basic_on_limit_setting(caplog):
    cands = [cand(f"k{n}") for n in range(1, 16)]  # three batches
    limit = lambda req: LLMError("usage_limit", retry_at=time.time() + 3600)  # noqa: E731
    seen = []
    progress = lambda fraction, message: seen.append((fraction, message))  # noqa: E731
    settings = Settings(on_llm_limit="basic", llm_parallel=1)
    out, provider, ctx = run(limit, cands, settings=settings, progress=progress)
    assert len(provider.requests) == 1  # the batches after the limit are never asked
    assert seen == [(5 / 15, "Checking clips (5 of 15 clips done)")]  # nor counted as done
    assert all(c.review is None and c.notes == ["review_skipped"] and c.red_flags == [] for c in out)
    assert ctx.llm_disabled and ctx.notes == ["usage_limit"]
    assert not [r for r in caplog.records if r.name == "clipforge.brain.review"]  # a limit isn't a failure


def test_review_without_the_llm():
    ctx = make_context(TR)  # Basic mode: nothing is reviewed, and nothing says so on each clip
    c = cand()
    assert review(ctx, [c]) == [c] and review(ctx, []) == []
    provider = FakeProvider({})
    off = make_context(TR, provider=provider, llm_disabled=True)  # a limit turned the LLM off earlier
    (skipped,) = review(off, [c])
    assert skipped.review is None and skipped.notes == ["review_skipped"] and provider.requests == []
    assert review(off, [skipped])[0].notes == ["review_skipped"]  # noted once
