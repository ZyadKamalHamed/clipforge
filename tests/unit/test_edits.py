import math

import pytest
from pydantic import ValidationError

from clipforge.brain.wording import POST_CHARS, post_text
from clipforge.config import Settings
from clipforge.edits import (
    COPY_KEYS,
    CUT_KEYS,
    LOOK_KEYS,
    TEXT_KEYS,
    ClipEdits,
    EditError,
    apply_packaging_edits,
    apply_range,
    render_key,
    validate_edits,
)
from clipforge.models import ClipRow, FactCard, Packaging, Sentence, Transcript, Word
from clipforge.render.styles import Style, style_for

PACK = Packaging(
    hook_title="The grey test",
    accent_word="grey",
    tiktok_caption="A caption",
    tiktok_hashtags=["#one"],
    instagram_caption="An IG caption",
    instagram_hashtags=["#two"],
)
TOO_LONG = "The {app} post can be at most 2,200 characters, counting its hashtags and credit line."
COLOUR = "The accent colour must look like #FFD60A."


def transcript(n=60):
    """``n`` words "w0", "w1", ... starting every half second, each 0.4 s long, in sentences of ten."""
    words = [Word(i=i, text=f"w{i}", start=i / 2, end=round(i / 2 + 0.4, 2)) for i in range(n)]
    bounds = [(w0, min(w0 + 9, n - 1)) for w0 in range(0, n, 10)]
    sentences = [
        Sentence(id=f"S{k + 1}", start=words[w0].start, end=words[w1].end, w0=w0, w1=w1, text="t")
        for k, (w0, w1) in enumerate(bounds)
    ]
    return Transcript(
        engine="t", engine_version="1", language="en", duration=n / 2, words=words, sentences=sentences
    )


def clip(**fields):
    """Words 10 to 29 of the transcript: sentences S2 and S3."""
    row = {
        "id": "c1",
        "project_id": "p1",
        "start_s": 5.0,
        "end_s": 14.9,
        "sentence_ids": ["S2", "S3"],
        "packaging": PACK.model_dump(mode="json"),
    }
    return ClipRow(**(row | fields))


def check(patch, current=None, *, c=None, library_ids=(), music_ids=(), credit=None):
    return validate_edits(
        c or clip(),
        transcript(),
        current or {},
        patch,
        library_ids=set(library_ids),
        music_ids=set(music_ids),
        credit=credit,
    )


def refused(patch, current=None, **kw):
    with pytest.raises(EditError) as err:
        check(patch, current, **kw)
    return err.value.field, err.value.message


def moved(new, c=None, *, n=60):
    return apply_range(c or clip(), transcript(n), new, duration_s=n / 2)


def range_refused(new, c=None, *, n=60):
    with pytest.raises(EditError) as err:
        moved(new, c, n=n)
    return err.value.field, err.value.message


# What may be edited


def test_edits_reject_unknown_keys():
    assert refused({"sparkles": True}) == ("sparkles", "ClipForge doesn't have that edit.")
    assert refused({"orig_range": [1.0, 5.0]}) == (
        "orig_range",
        "ClipForge keeps the clip's original range itself, so it can't be edited.",
    )
    assert refused({"punch_ins": "yes"}) == ("punch_ins", "Punch-ins must be on or off.")
    assert refused({"layout": "sideways"}) == (
        "layout",
        "The layout must be auto, face, motion, stacked, gameplay, stream or fit.",
    )
    assert refused({"encoder": "slow"}) == ("encoder", "The encoder must be fast or best.")
    assert check({"layout": "auto", "emoji": False, "encoder": "best"}) == {
        "layout": "auto",
        "emoji": False,
        "encoder": "best",
    }
    with pytest.raises(ValidationError):
        ClipEdits.model_validate({"sparkles": True})
    assert ClipEdits().model_dump(exclude_none=True) == {}
    groups = [*LOOK_KEYS, *CUT_KEYS, *TEXT_KEYS, *COPY_KEYS]
    assert sorted(groups) == sorted(ClipEdits.model_fields) and len(groups) == len(set(groups))
    assert set(CUT_KEYS) == {"struck", "orig_range"}
    assert set(TEXT_KEYS) == {"hook_title", "accent_word", "fact_cards", "word_fixes"}
    assert set(COPY_KEYS) == {"tiktok_caption", "tiktok_hashtags", "instagram_caption", "instagram_hashtags"}
    clip_only = {"layout", "gameplay_item", "gameplay_seed", "voiceover", "voiceover_at"}
    assert set(LOOK_KEYS) - clip_only <= set(Style.model_fields)


def test_cold_open_edit_validates():
    assert refused({"cold_open": "yes"}) == ("cold_open", "The cold open must be on or off.")
    assert check({"cold_open": True}) == {"cold_open": True}
    assert check({"cold_open": None}, {"cold_open": True}) == {}


def test_hook_title_limits():
    assert check({"hook_title": "  A   new\ttitle  "}) == {"hook_title": "A new title"}
    assert check({"hook_title": "x" * 42}) == {"hook_title": "x" * 42}
    assert check({"hook_title": "Bad" + chr(27) + "[1m" + chr(0x202E) + "title"}) == {
        "hook_title": "Bad [1mtitle"
    }
    assert refused({"hook_title": "x" * 43}) == ("hook_title", "The hook title can be at most 42 characters.")
    assert refused({"hook_title": "   "}) == ("hook_title", "The hook title can't be empty.")
    assert refused({"hook_title": "Two\nlines"}) == ("hook_title", "The hook title can't have line breaks.")
    assert refused({"hook_title": 42}) == ("hook_title", "The hook title must be text.")


def test_accent_word_must_be_in_the_title():
    assert check({"accent_word": "TEST"}) == {"accent_word": "test"}  # kept as the title writes it
    assert check({"accent_word": ""}) == {"accent_word": ""}  # no accent word at all
    assert refused({"accent_word": "blue"}) == (
        "accent_word",
        "The accent word must be one of the hook title's words.",
    )
    both = {"hook_title": "A blue test", "accent_word": "blue"}  # checked against the title saved with it
    assert check(both) == both
    assert check({"accent_word": "blue"}, {"hook_title": "A blue test"}) == both


def test_title_edit_drops_an_accent_word_it_lacks():
    assert check({"hook_title": "A new hook"}, {"accent_word": "grey"}) == {"hook_title": "A new hook"}
    assert check({"hook_title": "Grey skies"}, {"accent_word": "grey"}) == {
        "accent_word": "grey",
        "hook_title": "Grey skies",
    }
    assert check({"hook_title": "A new hook"}, {"accent_word": ""}) == {
        "accent_word": "",
        "hook_title": "A new hook",
    }
    # back to the packaging's own title, which has no "blue"
    assert check({"hook_title": None}, {"hook_title": "A blue test", "accent_word": "blue"}) == {}


def test_fact_cards_limits_and_sentences():
    cards = [{"sid": "S2", "text": "  A fact\nabout  it "}, {"sid": "S3", "text": "x" * 70}]
    assert check({"fact_cards": cards}) == {
        "fact_cards": [{"sid": "S2", "text": "A fact about it"}, {"sid": "S3", "text": "x" * 70}]
    }
    assert check({"fact_cards": []}) == {"fact_cards": []}  # no cards at all is an edit too
    four = [{"sid": "S2", "text": f"Fact {n}"} for n in range(4)]
    assert refused({"fact_cards": four}) == ("fact_cards", "A clip can have at most 3 fact cards.")
    assert refused({"fact_cards": [cards[0], {"sid": "S3", "text": " "}]}) == (
        "fact_cards.1",
        "Fact card 2 can't be empty.",
    )
    assert refused({"fact_cards": [{"sid": "S2", "text": "x" * 71}]}) == (
        "fact_cards.0",
        "Fact card 1 can be at most 70 characters.",
    )
    assert refused({"fact_cards": [{"sid": "S4", "text": "Later on"}]}) == (
        "fact_cards.0",
        "Fact card 1's sentence isn't in the clip.",
    )
    assert refused({"fact_cards": [{"text": "No sentence"}]}) == (
        "fact_cards",
        "Fact cards must be a list of cards, each with a sentence and its text.",
    )


def test_word_fixes_limits():
    assert check({"word_fixes": {"10": " Hello ", "29": "going  to"}}) == {
        "word_fixes": {"10": "Hello", "29": "going to"}
    }
    assert check({"word_fixes": {"12": "w12"}}) == {}  # a fix that changes nothing isn't kept
    assert check({"word_fixes": {"11": "Hi"}}, {"word_fixes": {"10": "Yo"}}) == {"word_fixes": {"11": "Hi"}}
    assert check({"word_fixes": {}}, {"word_fixes": {"10": "Yo"}}) == {}
    assert refused({"word_fixes": {"9": "Early"}}) == ("word_fixes.9", "That word isn't in the clip.")
    assert refused({"word_fixes": {"ten": "Hi"}}) == ("word_fixes.ten", "That word isn't in the clip.")
    assert refused({"word_fixes": {"10": "x" * 31}}) == (
        "word_fixes.10",
        "A word fix can be at most 30 characters.",
    )
    assert refused({"word_fixes": {"10": " "}}) == ("word_fixes.10", "A word fix can't be empty.")
    assert refused({"word_fixes": {"10": "two\nlines"}}) == (
        "word_fixes.10",
        "A word fix can't have line breaks.",
    )


def test_post_copy_counts_utf16():
    credit = "Source: TED"
    room = POST_CHARS - len(post_text("", ["#one"], credit)) - 2  # the caption and the blank line after it
    fire = chr(0x1F525)
    tiktok = ("tiktok_caption", TOO_LONG.format(app="TikTok"))
    assert check({"tiktok_caption": "a" * room}, credit=credit) == {"tiktok_caption": "a" * room}
    assert refused({"tiktok_caption": "a" * (room + 1)}, credit=credit) == tiktok
    assert refused({"tiktok_caption": "a" * (room - 1) + fire}, credit=credit) == tiktok  # an emoji is 2
    assert check({"tiktok_caption": "a" * (room - 2) + fire}, credit=credit)
    assert check({"tiktok_caption": "a" * (room + 1)})  # no credit line, so there is room
    full = {"instagram_caption": "c" * (POST_CHARS - len(post_text("", ["#two"], credit)) - 2)}
    assert refused({"instagram_hashtags": ["#two", "#three"]}, full, credit=credit) == (
        "instagram_hashtags",
        TOO_LONG.format(app="Instagram"),
    )
    assert check({"tiktok_caption": " Line one\r\nLine two" + chr(7) + "! "}) == {
        "tiktok_caption": "Line one\nLine two !"
    }
    assert check({"instagram_caption": ""}) == {"instagram_caption": ""}  # no caption at all
    assert refused({"tiktok_caption": 5}) == ("tiktok_caption", "The TikTok caption must be text.")


def test_hashtags_are_cleaned():
    tags = [" #Big Win ", "big win", "##stairs", "", "#", "snake_case", "Caf" + chr(0xE9)]
    assert check({"tiktok_hashtags": tags}) == {
        "tiktok_hashtags": ["#BigWin", "#stairs", "#snake_case", "#Caf" + chr(0xE9)]
    }
    assert check({"instagram_hashtags": ["#" + "x" * 40]}) == {"instagram_hashtags": ["#" + "x" * 40]}
    assert refused({"instagram_hashtags": ["x" * 41]}) == (
        "instagram_hashtags",
        "A hashtag can be at most 40 characters.",
    )
    assert refused({"tiktok_hashtags": ["#no-dashes"]}) == (
        "tiktok_hashtags",
        "A hashtag can only have letters, numbers and underscores.",
    )
    assert refused({"tiktok_hashtags": ["#2024"]}) == (
        "tiktok_hashtags",
        "A hashtag needs at least one letter.",
    )
    assert check({"tiktok_hashtags": []}) == {"tiktok_hashtags": []}  # no hashtags at all is an edit too
    six = [f"#tag{n}" for n in range(6)]
    assert refused({"instagram_hashtags": six}) == (
        "instagram_hashtags",
        "Instagram allows at most 5 hashtags.",
    )
    assert check({"instagram_hashtags": [*six[:5], "#TAG0"]}) == {"instagram_hashtags": six[:5]}
    assert check({"tiktok_hashtags": six}) == {"tiktok_hashtags": six}  # TikTok has no such cap
    assert refused({"tiktok_hashtags": "#one"}) == (
        "tiktok_hashtags",
        "The TikTok hashtags must be a list of words.",
    )


def test_colour_must_be_hex():
    assert check({"accent": "#ff0000"}) == {"accent": "#FF0000"}
    for bad in ("red", "#FF00", "#GG0000", "FF0000", "#FF0000\n", 255):
        assert refused({"accent": bad}) == ("accent", COLOUR)


def test_gameplay_item_must_exist():
    assert check({"gameplay_item": "g1"}, library_ids={"g1"}) == {"gameplay_item": "g1"}
    assert refused({"gameplay_item": "g2"}, library_ids={"g1"}) == (
        "gameplay_item",
        "That gameplay video isn't in the library.",
    )
    assert check({"gameplay_seed": 3}) == {"gameplay_seed": 3}
    for bad in (-1, True, 1.5):
        assert refused({"gameplay_seed": bad}) == (
            "gameplay_seed",
            "The gameplay seed must be a whole number of 0 or more.",
        )


def test_music_edits_are_checked():
    assert "music" in LOOK_KEYS and "music_db" in LOOK_KEYS
    assert check({"music": "auto", "music_db": -18}) == {"music": "auto", "music_db": -18}
    assert check({"music": "lm1", "music_db": -6.5}, music_ids={"lm1"}) == {"music": "lm1", "music_db": -6.5}
    for bad in ("lm2", 5, ""):
        assert refused({"music": bad}, music_ids={"lm1"}) == (
            "music",
            "That track isn't in your music library.",
        )
    for bad in (-30, -5, -24.5, True, "-12", math.nan):
        assert refused({"music_db": bad}) == ("music_db", "Music sits between 24 and 6 dB under the speech.")
    assert check({"music": None}, {"music": "auto", "preset": "beast"}) == {"preset": "beast"}  # off again
    settings = Settings()
    assert (style_for(settings, {}).music, style_for(settings, {}).music_db) == (
        None,
        -12.0,
    )  # off by default
    mine = style_for(settings, {"music": "lm1", "music_db": -18})
    assert (mine.music, mine.music_db) == ("lm1", -18.0)


def test_none_removes_an_edit():
    current = {"emoji": False, "hook_title": "Old title", "preset": "beast", "struck": [11]}
    assert check({"emoji": None, "hook_title": None, "struck": None}, current) == {"preset": "beast"}
    assert current == {"emoji": False, "hook_title": "Old title", "preset": "beast", "struck": [11]}
    assert check({"struck": [12, 11, 12]}) == {"struck": [11, 12]}
    assert check({"struck": []}, {"struck": [11]}) == {}
    for bad in ([60], [-1]):
        assert refused({"struck": bad}) == ("struck", "That word isn't in the transcript.")
    assert refused({"struck": ["11"]}) == ("struck", "Struck words must be a list of word numbers.")


# Moving the clip's range


def test_range_limits():
    inside = "The clip must start and end inside the video."
    assert range_refused((-1.0, 10.0)) == ("range", inside)
    assert range_refused((20.0, 31.0)) == ("range", inside)
    assert range_refused((math.nan, 10.0)) == ("range", inside)
    assert range_refused((12.0, 6.0)) == ("range", "The clip's end must come after its start.")
    assert range_refused((5.1, 5.3)) == ("range", "The clip needs at least one whole word.")
    assert range_refused((5.0, 8.0)) == ("range", "The clip must be at least 3 seconds long.")  # 5.0 to 7.9
    assert moved((5.0, 8.4))[0]["end_s"] == 8.4
    assert range_refused((0.0, 190.0), n=400) == ("range", "The clip can be at most 180 seconds long.")
    assert moved((0.0, 179.95), n=400)[0]["end_s"] == 179.9


def test_range_snaps_to_words_and_keeps_the_original():
    fields, edits = moved((4.97, 20.42))
    assert fields == {"start_s": 5.0, "end_s": 20.4, "sentence_ids": ["S2", "S3", "S4", "S5"]}
    assert edits == {"orig_range": [5.0, 14.9]}
    again = clip(**fields, edits=edits)
    fields, edits = moved((5.3, 20.0), again)
    assert fields == {"start_s": 5.5, "end_s": 19.9, "sentence_ids": ["S2", "S3", "S4"]}
    assert edits == {"orig_range": [5.0, 14.9]}  # the first range is the one kept


def test_range_reset_restores_the_original():
    away = clip(start_s=5.5, end_s=19.9, sentence_ids=["S2", "S3", "S4"], edits={"orig_range": [5.0, 14.9]})
    assert moved(None, away) == (
        {"start_s": 5.0, "end_s": 14.9, "sentence_ids": ["S2", "S3"]},
        {"orig_range": None},
    )
    assert moved((5.0, 14.9), away)[1] == {"orig_range": None}  # moved back by hand is the same
    assert moved(None) == ({}, {})  # never moved, so nothing to restore


# What the render is made from


def test_packaging_edits_apply_over_the_packaging():
    edits = {
        "hook_title": "Grey skies ahead",
        "fact_cards": [{"sid": "S2", "text": "A card"}],
        "tiktok_caption": "New",
        "emoji": False,
    }
    p = apply_packaging_edits(PACK, edits)
    assert (p.hook_title, p.accent_word) == ("Grey skies ahead", "Grey")
    assert p.fact_cards == [FactCard(sid="S2", text="A card")]
    assert (p.tiktok_caption, p.instagram_caption) == ("New", "An IG caption")
    assert apply_packaging_edits(PACK, {"hook_title": "A new hook"}).accent_word is None
    assert apply_packaging_edits(PACK, {"accent_word": ""}).accent_word is None
    assert apply_packaging_edits(PACK, {"accent_word": "test"}).accent_word == "test"
    assert apply_packaging_edits(PACK, {}) == PACK


def test_render_key_follows_what_the_video_is_made_from():
    c = clip()
    key = render_key(c, {"preset": "beast", "emoji": False})
    assert len(key) == 10 and key == render_key(c, {"emoji": False, "preset": "beast"})
    copy = {"tiktok_caption": "Hi", "instagram_hashtags": ["#x"], "orig_range": [1.0, 9.0]}
    assert key == render_key(c, {"preset": "beast", "emoji": False, **copy})  # the post copy isn't in it
    assert key != render_key(c, {"preset": "clean", "emoji": False})
    assert key != render_key(clip(end_s=14.4), {"preset": "beast", "emoji": False})
