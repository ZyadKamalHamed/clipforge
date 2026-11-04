import time

import pytest

from clipforge.brain.context import load_prompt
from clipforge.brain.llm.cache import LLMCache
from clipforge.brain.llm.schema import schema_for
from clipforge.brain.package import boundary_options, package
from clipforge.brain.schemas import PackageOut
from clipforge.brain.transcript_view import render_view, wrap_untrusted
from clipforge.brain.wording import credit_line, cut_title, hashtags, post_text
from clipforge.config import Settings
from clipforge.errors import LLMError, WaitUntil
from clipforge.models import Candidate, EmojiCue, FactCard, Packaging, ProjectOptions
from tests.fakes import FakeProvider, make_context, make_meta, make_transcript

LINES = {
    8: "So here's the thing nobody tells you about deadlines.",  # 35 to 40 s
    9: "If there's no deadline, the panic monster never shows up.",
    10: "And that's where the real trouble starts.",
    11: "I lost four million dollars in one afternoon.",
    12: "It was one email to Tim Urban.",
    13: "Nobody read it before it went out.",  # 60 to 65 s
}
TR = make_transcript([LINES.get(k, f"Line number {k} of the talk.") for k in range(1, 31)])  # 5 s each
TITLE = "The email that cost him $4M"
INSTAGRAM = "Tim Urban on the email that cost four million dollars. Send this to a friend who waits."


def cand(cid: str = "k1", first: int = 8, last: int = 13, **fields) -> Candidate:
    """A candidate over sentences S<first> to S<last> of TR."""
    base = {
        "id": cid,
        "start_sid": f"S{first}",
        "end_sid": f"S{last}",
        "start_s": TR.sentences[first - 1].start,
        "end_s": TR.sentences[last - 1].end,
        "scores": {},
        "archetype": "story",
        "llm_score": 70.0,
    }
    return Candidate(**(base | fields))


def item(cid: str = "k1", first: int = 8, last: int = 13, **fields) -> dict:
    """One clip's answer, keeping S<first> to S<last>, with packaging that holds up."""
    base = {
        "id": cid,
        "start_sid": f"S{first}",
        "end_sid": f"S{last}",
        "hook_title": TITLE,
        "hook_alts": ["$4M gone in one afternoon", "Why nobody read the email"],
        "accent_word": "$4M",
        "emphasis": ["four million", "Tim Urban"],
        "emoji_cues": [{"sid": "S11", "word": "dollars", "emoji": "💸"}],
        "fact_cards": [{"sid": "S12", "text": "The email went to Tim Urban"}],
        "cold_open_sid": "S11",
        "mood": "dark",
        "energy": 4,
        "tiktok_caption": "One email cost him four million dollars. Would you have sent it?",
        "tiktok_hashtags": ["#money", "#procrastination", "#ted"],
        "instagram_caption": INSTAGRAM,
        "instagram_hashtags": ["#procrastination", "#deadlines", "#money"],
        "keywords": ["procrastination", "deadlines", "Tim Urban"],
    }
    return base | fields


def answer(*items):
    """A package answer giving these clips."""
    return lambda req: {"clips": list(items)}


def basic(ctx, c: Candidate) -> Packaging:
    """Basic mode's packaging, for a clip the LLM didn't package."""
    return Packaging(hook_title=f"Basic title for {c.id}", mode="basic")


def run(handler, cands, *, fallback=basic, tr=TR, **fields):
    provider = FakeProvider({"package": handler})
    ctx = make_context(tr, provider=provider, **fields)
    return package(ctx, cands, fallback=fallback), provider, ctx


def packaged(**fields) -> Candidate:
    """Clip k1 (S8 to S13) packaged from an answer with these fields."""
    (out,), _, _ = run(answer(item(**fields)), [cand()])
    return out


def units(text: str) -> int:
    """The length TikTok counts: UTF-16 code units, so an emoji counts 2."""
    return len(text.encode("utf-16-le")) // 2


# Boundary options


def test_boundary_options_ranges():
    c = cand()  # 35 to 65 s
    assert boundary_options(TR, c) == (
        [f"S{k}" for k in range(2, 13)],  # starting from 5 to 55 s
        [f"S{k}" for k in range(11, 20)],  # ending from 55 to 95 s
    )
    assert boundary_options(TR, c, start_before=5, start_after=0, end_before=0, end_after=5) == (
        ["S7", "S8"],
        ["S13", "S14"],
    )
    snapped = cand(start_s=34.65, end_s=65.45)  # a lead and a tail around the words
    none = {"start_before": 0, "start_after": 0, "end_before": 0, "end_after": 0}
    assert boundary_options(TR, snapped, **none) == (["S8"], ["S13"])  # the current ids, always


def test_boundary_options_skip_sentences_that_carry_on_another():
    # S9 carries on S8 and S12 carries on S11, so no clip starts on S9 or S12 or ends on S8 or S11
    sentences = [s.model_copy(update={"cont": s.id in ("S9", "S12")}) for s in TR.sentences]
    tr = TR.model_copy(update={"sentences": sentences})
    starts, ends = boundary_options(tr, cand())
    assert starts == ["S2", "S3", "S4", "S5", "S6", "S7", "S8", "S10", "S11"]
    assert ends == [f"S{k}" for k in range(12, 20)]
    starts, ends = boundary_options(tr, cand(first=9, last=11))  # 40 to 55 s
    assert "S9" in starts and "S11" in ends  # the current ids, always


# Moving the bounds


def test_package_applies_offered_bounds():
    short = cand(
        last=9, red_flags=["missing_context", "too_short"], notes=["focus", "short"], cold_open_sid="S8"
    )
    (out,), _, _ = run(answer(item(start_sid=" s8 ", end_sid="S12")), [short])  # S12 is an end on offer
    assert (out.start_sid, out.end_sid) == ("S8", "S12")
    assert (out.start_s, out.end_s) == pytest.approx((35.0, 60.0))
    assert out.red_flags == ["missing_context"] and out.notes == ["focus"]  # the length is judged again
    assert out.cold_open_sid == "S8" and out.packaging.cold_open_sid == "S11"
    assert short.end_sid == "S9" and short.packaging is None  # the candidate given is left alone
    (moved,), _, _ = run(answer(item(start_sid="S9", cold_open_sid="S9")), [cand(cold_open_sid="S8")])
    assert (moved.start_sid, moved.end_sid) == ("S9", "S13")
    assert (moved.start_s, moved.end_s) == pytest.approx((40.0, 65.0))
    assert moved.cold_open_sid is None  # Discover's cold open is no longer in the clip
    assert moved.packaging.cold_open_sid is None  # and the clip's own first line is no teaser
    for first, last in [(11, 13), (2, 16)]:  # 15 s and 75 s, the ends of the range, are in it
        (fit,), _, _ = run(answer(item(start_sid=f"S{first}", end_sid=f"S{last}")), [cand()])
        assert (fit.start_sid, fit.end_sid, fit.notes) == (f"S{first}", f"S{last}", [])
    long = cand(first=5, last=23, notes=["over_90s_instagram"])  # 20 to 115 s
    (trimmed,), _, _ = run(
        answer(item(start_sid="S7", end_sid="S23")), [long], options=ProjectOptions(length="long")
    )
    assert (trimmed.start_sid, trimmed.notes) == ("S7", [])  # 85 s now


def test_package_snaps_moved_bounds_within_the_length_range():
    # 4.5 s sentences 0.5 s apart, so a clip gets a lead and a tail. S2 to S19 is 89.5 s of speech, past 90 s
    # with a full lead and tail, so both shrink
    tr = make_transcript(
        [f"Line number {k}." for k in range(1, 31)], seconds_each=4.5, starts=[5.0 * k for k in range(30)]
    )
    c = Candidate(id="k1", start_sid="S5", end_sid="S19", start_s=19.65, end_s=94.95, scores={})
    (out,), _, _ = run(
        answer(item(start_sid="S2", end_sid="S19")), [c], tr=tr, options=ProjectOptions(length="long")
    )
    assert (out.start_sid, out.end_sid) == ("S2", "S19")
    assert (out.start_s, out.end_s) == pytest.approx((4.78125, 94.78125))
    assert out.notes == []  # 90 s exactly, so not over 90 s for Instagram


def test_package_rejects_bounds_outside_options():
    cases = [
        ("S1", "S13"),  # S1 starts 35 s before the clip, past the 30 s on offer
        ("S8", "S20"),  # S20 ends 35 s after it
        ("S8", "S99"),  # no such sentence
        ("S2", "S19"),  # both on offer, but 90 s of speech is past the 75 s limit
    ]
    for start, end in cases:
        (out,), _, _ = run(answer(item(start_sid=start, end_sid=end)), [cand()])
        assert (out.start_sid, out.end_sid, out.start_s, out.end_s) == ("S8", "S13", 35.0, 65.0), (start, end)
        assert out.notes == ["refine_rejected"] and out.packaging.mode == "llm"
    short = cand(last=9)  # 35 to 45 s, so S7 to S12 are both start and end options
    for start, end in [("S10", "S8"), ("S10", "S10")]:  # backwards, and 5 s of speech
        (out,), _, _ = run(answer(item(start_sid=start, end_sid=end)), [short])
        assert (out.start_sid, out.end_sid, out.notes) == ("S8", "S9", ["refine_rejected"]), (start, end)
    kept = cand(start_s=34.65, end_s=65.45)
    (out,), _, _ = run(answer(item()), [kept])  # the current ids
    assert (out.start_s, out.end_s, out.notes) == (34.65, 65.45, [])  # nothing moved, nothing snapped again
    (again,), _, _ = run(answer(item(start_sid="S1")), [cand(notes=["refine_rejected"])])
    assert again.notes == ["refine_rejected"]  # noted once, when a clip is packaged again


# The hook title


def test_hook_title_rules():
    long_title = "Why procrastinators wait until the very last minute"  # 51 characters
    out = packaged(hook_title=long_title, hook_alts=["Too short", TITLE, "Why nobody read the email"])
    assert (out.packaging.hook_title, out.packaging.hook_alts) == (TITLE, ["Why nobody read the email"])
    assert out.notes == []
    cut = packaged(
        hook_title="Deadlines are hard, so we wait, and wait, and then panic", hook_alts=["Too short", ""]
    )
    assert cut.packaging.hook_title == "Deadlines are hard, so we wait, and wait"  # 8 words, 40 characters
    assert cut.packaging.hook_alts == [] and cut.notes == ["hook_truncated"]
    nine = packaged(hook_title="No deadline, no panic, so he did not act", hook_alts=["Too short"])
    assert nine.packaging.hook_title == "No deadline, no panic, so he did not"  # 9 words fit 42 characters
    assert nine.notes == ["hook_truncated"]
    quoted = packaged(hook_title='  "The email   that cost him $4M"  ')
    assert quoted.packaging.hook_title == TITLE and quoted.notes == []
    alts = [
        "THE EMAIL THAT COST HIM $4M",
        "Why nobody read the email",
        "Nobody read the email first",
        "A third one",
    ]
    assert packaged(hook_alts=alts).packaging.hook_alts == [
        "Why nobody read the email",
        "Nobody read the email first",
    ]
    short = packaged(hook_title="Deadlines.", hook_alts=["Why"])  # too short, and cutting can't help
    assert (short.packaging.hook_title, short.packaging.hook_alts, short.notes) == ("Deadlines.", [], [])


def test_package_without_a_title_uses_fallback():
    for title, alts in [("  ", [""]), ("🔥🔥🔥", ["...", "!!!"]), ("…" * 45 + " deadlines", [])]:
        out = packaged(hook_title=title, hook_alts=alts)  # no words, or none left once cut
        assert out.packaging == Packaging(hook_title="Basic title for k1", mode="basic"), title
        assert out.notes == ["package_missing"]


def test_cut_title():
    assert (
        cut_title("Deadlines are hard, so we wait, and wait, and then panic")
        == "Deadlines are hard, so we wait, and wait"
    )
    assert (
        cut_title("One two three four five six seven eight nine ten")
        == "One two three four five six seven eight"
    )
    assert (
        cut_title("Procrastination is costing every single one of us")
        == "Procrastination is costing every single"
    )
    assert cut_title("No deadline, no panic, so he did not act") == "No deadline, no panic, so he did not"
    assert cut_title("It was one email.") == "It was one email"
    assert cut_title("Wait for it ...") == "Wait for it"
    assert (
        cut_title("Would you send it?") == "Would you send it?"
        and cut_title("Never again!") == "Never again!"
    )
    assert cut_title("x" * 50) == "x" * 42  # one word longer than a title
    assert cut_title("   ") == ""


def test_cut_title_leaves_no_word_hanging_off_the_end():
    assert (
        cut_title("Now, the panic monster is dormant most of the time,")
        == "Now, the panic monster is dormant"
    )
    assert cut_title("The final thing I would say is a really important one") == "The final thing I would say"
    assert (
        cut_title("You get started maybe a little slowly, but you get enough done")
        == "You get started maybe a little slowly"
    )
    assert cut_title("So it is of the and the a big deal") == "So it is"  # never under 3 words
    assert cut_title("What are you looking at") == "What are you looking at"  # it fits, so nothing was cut


def test_accent_word_must_be_in_title():
    def accent(word, **fields):
        return packaged(
            hook_title="$4M gone in one afternoon", accent_word=word, **fields
        ).packaging.accent_word

    assert accent("$4m") == "$4M"  # written as the title has it
    assert accent("GONE") == "gone" and accent("afternoon.") == "afternoon"
    assert accent("dollars") is None and accent("one afternoon") is None and accent("") is None
    assert accent(None) is None
    marks = packaged(hook_title="Deadlines & panic in one afternoon", accent_word="!").packaging
    assert marks.accent_word is None  # a mark names no word, not even the title's "&"
    long_title = "Why procrastinators wait until the very last minute"
    assert (
        packaged(hook_title=long_title, accent_word="procrastinators").packaging.accent_word is None
    )  # not kept


# On the clip


def test_emphasis_filtered_to_clip_words():
    words = [
        "four million",
        "Tim  Urban",
        "deadline",
        "FOUR MILLION",  # said already
        "billion",  # never said
        "dead",  # only part of a word
        "Line number 20",  # said after the clip
        "",
        "panic monster",
        "trouble",
        "email",
        "afternoon",  # a seventh
    ]
    assert packaged(emphasis=words).packaging.emphasis == [
        "four million",
        "Tim Urban",
        "deadline",
        "panic monster",
        "trouble",
        "email",
    ]


def test_emoji_cues_need_a_spoken_word_and_room():
    cues = [
        {"sid": "S11", "word": "dollars", "emoji": "💸"},
        {"sid": "S11", "word": "dollars", "emoji": "💸"},  # the same cue again
        {"sid": "S8", "word": "dollars", "emoji": "💰"},  # not said in S8
        {"sid": "S8", "word": "dead", "emoji": "💀"},  # only part of a word said there
        {"sid": "s12", "word": "Email", "emoji": " 📧 "},
        {"sid": "S20", "word": "Line", "emoji": "📏"},  # after the clip
        {"sid": "S99", "word": "deadline", "emoji": "⏰"},  # no such sentence
        {"sid": "S9", "word": "deadline", "emoji": "⏰"},
        {"sid": "S10", "word": "trouble", "emoji": ":)"},  # not emoji
        {"sid": "S10", "word": "trouble", "emoji": "→"},
        {"sid": "S10", "word": "trouble", "emoji": "OK👌"},
        {"sid": "S10", "word": "trouble", "emoji": "😬" * 9},  # longer than 8 characters
        {"sid": "S8", "word": "deadlines", "emoji": "📅"},
        {"sid": "S13", "word": "Nobody", "emoji": "🙈"},
        {"sid": "S10", "word": "trouble", "emoji": "😬"},  # a sixth: 30 s holds five
        {"sid": "S11", "word": "afternoon", "emoji": "🕑"},
    ]
    out = packaged(emoji_cues=cues).packaging
    assert out.emoji_cues == [
        EmojiCue(sid="S8", word="deadlines", emoji="📅"),
        EmojiCue(sid="S9", word="deadline", emoji="⏰"),
        EmojiCue(sid="S11", word="dollars", emoji="💸"),
        EmojiCue(sid="S12", word="Email", emoji="📧"),
        EmojiCue(sid="S13", word="Nobody", emoji="🙈"),
    ]  # in the clip's order
    three = [
        {"sid": "S8", "word": "deadlines", "emoji": "📅"},
        {"sid": "S9", "word": "deadline", "emoji": "⏰"},
    ]
    three.append({"sid": "S10", "word": "trouble", "emoji": "😬"})
    (short,), _, _ = run(answer(item(last=10, emoji_cues=three)), [cand(last=10)])  # 15 s holds two
    assert [cue.sid for cue in short.packaging.emoji_cues] == ["S8", "S9"]
    snapped = cand(start_s=34.6, end_s=64.6)  # 30 s, though the subtraction gives 29.999999999999993
    (noisy,), _, _ = run(answer(item(emoji_cues=cues)), [snapped])
    assert len(noisy.packaging.emoji_cues) == 5
    toned = [{"sid": "S12", "word": "email", "emoji": "👍🏽"}]  # two code points make one emoji
    (thumbs,), _, _ = run(answer(item(emoji_cues=toned)), [cand()])
    assert [cue.emoji for cue in thumbs.packaging.emoji_cues] == ["👍🏽"]


def test_fact_cards_anchored_inside_the_clip():
    cards = [
        {"sid": "S12", "text": "The email went to Tim Urban"},
        {"sid": "S20", "text": "After the clip"},
        {"sid": "S99", "text": "No such sentence"},
        {"sid": "S11", "text": "  "},
        {
            "sid": "S11",
            "text": "He lost $4M in one afternoon, after a single email nobody had read, and it went out",
        },
        {"sid": "s12", "text": "the email   went to TIM URBAN"},  # said already
        {"sid": "S8", "text": "Deadlines bring on the panic monster"},
        {"sid": "S9", "text": "A fourth card"},
    ]
    assert packaged(fact_cards=cards).packaging.fact_cards == [
        FactCard(sid="S8", text="Deadlines bring on the panic monster"),
        FactCard(
            sid="S11", text="He lost $4M in one afternoon, after a single email nobody had read"
        ),  # 66, no comma
        FactCard(sid="S12", text="The email went to Tim Urban"),
    ]


def test_cold_open_is_a_later_line_of_the_clip():
    sids = ["s11", "S13", "S8", "S14", "S99", "", None]  # the first line, after the clip, no such sentence
    assert [packaged(cold_open_sid=sid).packaging.cold_open_sid for sid in sids] == ["S11", "S13"] + [
        None
    ] * 5


def test_mood_energy_and_keywords():
    keywords = [
        "#Procrastination",
        " deadlines ",
        "procrastination",
        "Tim  Urban",
        "",
        "money",
        "email",
        "late",
        "panic",
    ]
    out = packaged(energy=9, keywords=keywords).packaging
    assert (out.mood, out.energy) == ("dark", 5)
    assert out.keywords == ["Procrastination", "deadlines", "Tim Urban", "money", "email", "late"]
    assert packaged(energy=-2).packaging.energy == 1


def test_package_keeps_a_good_answer():
    out = packaged()
    assert out.packaging == Packaging(
        hook_title=TITLE,
        hook_alts=["$4M gone in one afternoon", "Why nobody read the email"],
        accent_word="$4M",
        emphasis=["four million", "Tim Urban"],
        emoji_cues=[EmojiCue(sid="S11", word="dollars", emoji="💸")],
        fact_cards=[FactCard(sid="S12", text="The email went to Tim Urban")],
        cold_open_sid="S11",
        mood="dark",
        energy=4,
        tiktok_caption="One email cost him four million dollars. Would you have sent it?",
        tiktok_hashtags=["#money", "#procrastination", "#ted"],
        instagram_caption=INSTAGRAM,
        instagram_hashtags=["#procrastination", "#deadlines", "#money"],
        keywords=["procrastination", "deadlines", "Tim Urban"],
        credit="Source: TED",
        mode="llm",
    )
    assert (out.start_sid, out.end_sid, out.start_s, out.end_s, out.notes) == ("S8", "S13", 35.0, 65.0, [])


# Captions and hashtags


def test_hashtags_normalised_and_limited():
    tags = [
        "#Money",
        "money",
        "personal finance",
        "##AI_tools",
        "#2024",
        "",
        "  ",
        "don't",
        "#café",
        "#Money!",
    ]
    assert hashtags(tags, 10) == ["#money", "#personalfinance", "#ai_tools", "#dont", "#café"]
    assert hashtags(tags, 3) == ["#money", "#personalfinance", "#ai_tools"]
    assert hashtags(["#بودكاست", "हिन्दी", "ＡＩ"], 5) == ["#بودكاست", "#हिन्दी", "#ai"]
    assert hashtags(["#" + "a" * 41, "#" + "b" * 40], 5) == [
        "#" + "b" * 40
    ]  # past 40 characters it isn't a tag
    out = packaged(
        tiktok_hashtags=["#one", "#two", "#three", "#four", "#five"],
        instagram_hashtags=["#one", "#two", "#three", "#four", "#five", "#six"],
    ).packaging
    assert out.tiktok_hashtags == ["#one", "#two", "#three", "#four"]
    assert out.instagram_hashtags == ["#one", "#two", "#three", "#four", "#five"]


def test_captions_keep_hashtags_out_of_the_text():
    out = packaged(
        tiktok_caption="#Ted Nobody read the #email before it went out. Would you? #Money\n#deadlines",
        tiktok_hashtags=["#procrastination"],
        instagram_caption="The #1 mistake: one email nobody read. #procrastination",
        instagram_hashtags=["#procrastination", "#deadlines"],
    ).packaging
    assert out.tiktok_caption == "Nobody read the email before it went out. Would you?"
    assert out.tiktok_hashtags == ["#procrastination", "#ted", "#email", "#money"]  # the answer's own first
    assert out.instagram_caption == "The #1 mistake: one email nobody read."  # "#1" is no hashtag
    assert out.instagram_hashtags == ["#procrastination", "#deadlines"]
    blank = packaged(tiktok_caption=" #money #ted ", instagram_caption="").packaging
    assert blank.tiktok_caption == blank.instagram_caption == TITLE
    assert blank.tiktok_hashtags == ["#money", "#procrastination", "#ted"]


def test_post_text_assembly():
    assert (
        post_text("One email.", ["#money", "#ted"], "Source: TED")
        == "One email.\n\nSource: TED\n\n#money #ted"
    )
    assert post_text("One email.", [], None) == "One email."
    assert post_text("One email.", ["#money"], None) == "One email.\n\n#money"


def test_post_text_stays_within_2200_utf16_units():
    words = " ".join(["procrastination"] * 200)
    exact = packaged(tiktok_caption=f"Unbelievable! {words}").packaging
    assert exact.tiktok_caption == " ".join(["Unbelievable!", *["procrastination"] * 134])
    assert units(post_text(exact.tiktok_caption, exact.tiktok_hashtags, exact.credit)) == 2200
    over = packaged(tiktok_caption=f"Unbelievable!! {words}").packaging  # one unit more
    assert over.tiktok_caption == " ".join(["Unbelievable!!", *["procrastination"] * 133])
    bare = item(tiktok_caption=f"Amazing! {words}", tiktok_hashtags=[])
    (alone,), _, _ = run(answer(bare), [cand()], options=ProjectOptions(credit=False))
    assert alone.packaging.tiktok_caption == " ".join(["Amazing!", *["procrastination"] * 137])  # all 2200
    out = packaged(instagram_caption="💸 " * 1000).packaging
    assert units(post_text(out.instagram_caption, out.instagram_hashtags, out.credit)) <= 2200
    assert out.instagram_caption == " ".join(["💸"] * 717)  # 2150 units in 1433 characters


# The credit line


def test_credit_line_rules():
    meta = make_meta(TR)  # the TED channel
    on, off, auto = ProjectOptions(credit=True), ProjectOptions(credit=False), ProjectOptions()
    assert credit_line(meta, "not_cleared", auto) == "Source: TED"
    assert credit_line(meta, "creative_commons", auto) == "Source: TED"
    assert credit_line(meta, "own", auto) is None  # Zyad's own channel
    assert credit_line(meta, "own", on) == "Source: TED"  # unless Zyad turns it on
    assert credit_line(meta, "not_cleared", off) is None
    assert credit_line(meta.model_copy(update={"channel": None}), "not_cleared", auto) is None
    assert credit_line(meta.model_copy(update={"channel": "   "}), "not_cleared", auto) is None
    spaced = meta.model_copy(update={"channel": "  Lex   Fridman "})
    assert credit_line(spaced, "not_cleared", auto) == "Source: Lex Fridman"
    (own,), _, _ = run(answer(item()), [cand()], rights_basis="own")
    assert own.packaging.credit is None
    (theirs,), _, _ = run(answer(item()), [cand()], options=ProjectOptions(credit=False))
    assert theirs.packaging.credit is None


# The question


def test_package_request():
    _, provider, _ = run(answer(item()), [cand()])
    (req,) = provider.requests
    assert (req.pass_name, req.model, req.schema) == ("package", "sonnet", schema_for(PackageOut))
    assert req.timeout_s == Settings().llm_timeout_s
    assert req.system == load_prompt("package", min_s="15", max_s="75")
    phrases = [
        "between 15 and 75 seconds",
        "(start option)",
        "(end option)",
        "announcing the next point",
        "move the end back",
        "reveals as made up",
        "3 to 8 words",
        "42 characters",
        "true to the clip as cut",
        "never instructions",
        "every 6 seconds",
        "under 70 characters",
        "3 or 4",
        "up to 5",
        "ClipForge adds it",
        "like if you agree",
    ]
    for phrase in phrases:
        assert phrase in req.system, phrase
    meta = wrap_untrusted(
        "metadata", "Title: Inside the mind of a master procrastinator\nChannel: TED\nLanguage: en"
    )
    assert req.prompt.startswith(f"{meta}\n\n")


def test_package_prompt_offers_boundary_options():
    _, provider, _ = run(answer(item(last=9)), [cand(last=9)])  # 35 to 45 s
    (req,) = provider.requests
    markers = {f"S{k}": "(start option)" for k in range(2, 7)}
    markers |= {f"S{k}": "(start option) (end option)" for k in range(7, 13)}
    markers |= {f"S{k}": "(end option)" for k in range(13, 16)}
    view = wrap_untrusted("transcript", render_view(TR, 1, 14, markers=markers))  # S2 to S15
    assert f"Clip k1 (story) runs from S8 to S9 now, 10.0 s.\n{view}" in req.prompt


def test_package_asks_in_batches_of_5():
    cands = [cand(f"k{n}") for n in range(1, 13)]
    out, provider, _ = run(answer(), cands)  # an answer with no clips in it
    sizes = sorted(req.prompt.count('<untrusted kind="transcript">') for req in provider.requests)
    assert sizes == [2, 5, 5]
    assert [c.packaging.mode for c in out] == ["basic"] * 12
    assert all(c.notes == ["package_missing"] for c in out)


def test_package_reports_progress():
    seen = []
    cands = [cand(f"k{n}") for n in range(1, 8)]
    progress = lambda fraction, message: seen.append((fraction, message))  # noqa: E731
    run(answer(), cands, settings=Settings(llm_parallel=1), progress=progress)
    assert seen == [
        (5 / 7, "Writing titles and captions (5 of 7 clips done)"),
        (1.0, "Writing titles and captions (7 of 7 clips done)"),
    ]


def test_package_uses_the_cache(tmp_path):
    cache, log_dir = LLMCache(tmp_path / "cache"), tmp_path / "log"
    first, _, _ = run(answer(item()), [cand()], cache=cache, log_dir=log_dir)
    again, provider, _ = run(answer(item()), [cand()], cache=cache, log_dir=log_dir)
    assert again == first and provider.requests == []
    assert len(list(log_dir.glob("package-*.prompt.txt"))) == 1


# When the LLM can't help


def test_package_error_uses_fallback():
    cands = [cand(f"k{n}") for n in range(1, 8)]  # batches of 5 and 2

    def handler(req):
        if "Clip k6 (" in req.prompt:
            return LLMError("timeout")
        return {"clips": [item(c.id) for c in cands if f"Clip {c.id} (" in req.prompt]}

    out, provider, ctx = run(handler, cands)
    assert [c.packaging.mode for c in out] == ["llm"] * 5 + ["basic"] * 2
    assert [c.packaging.hook_title for c in out[5:]] == ["Basic title for k6", "Basic title for k7"]
    assert ctx.notes == ["timeout"] and len(provider.requests) == 3  # the failed batch was asked twice
    assert all(c.notes == [] for c in out)


def test_package_answer_ids():
    first = item(" K1 ")
    again = item("k1", hook_title="A second answer for it")
    out, _, _ = run(answer(first, again, item("k9")), [cand(), cand("k2", 20, 25)])
    assert out[0].packaging.hook_title == TITLE and out[0].notes == []  # the first answer for an id counts
    assert out[1].packaging.mode == "basic" and out[1].notes == ["package_missing"]


def test_package_waits_on_usage_limit():
    until = time.time() + 3600
    with pytest.raises(WaitUntil):
        run(lambda req: LLMError("usage_limit", retry_at=until), [cand()])


def test_package_basic_on_limit_setting():
    cands = [cand(f"k{n}") for n in range(1, 16)]  # three batches
    limit = lambda req: LLMError("usage_limit", retry_at=time.time() + 3600)  # noqa: E731
    seen = []
    progress = lambda fraction, message: seen.append((fraction, message))  # noqa: E731
    settings = Settings(on_llm_limit="basic", llm_parallel=1)
    out, provider, ctx = run(limit, cands, settings=settings, progress=progress)
    assert len(provider.requests) == 1  # the batches after the limit are never asked
    assert seen == [(5 / 15, "Writing titles and captions (5 of 15 clips done)")]  # nor counted as done
    assert [c.packaging.mode for c in out] == ["basic"] * 15
    assert ctx.llm_disabled and ctx.notes == ["usage_limit"]


def test_package_without_the_llm_uses_fallback():
    ctx = make_context(TR)  # Basic mode
    (out,) = package(ctx, [cand()], fallback=basic)
    assert out.packaging == Packaging(hook_title="Basic title for k1", mode="basic") and out.notes == []
    provider = FakeProvider({})
    off = make_context(TR, provider=provider, llm_disabled=True)
    assert package(off, [cand()], fallback=basic)[0].packaging.mode == "basic" and provider.requests == []
    assert package(ctx, [], fallback=basic) == []
