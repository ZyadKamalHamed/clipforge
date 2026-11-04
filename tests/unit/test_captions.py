import math
import re
from typing import get_args

import numpy as np
import pytest

from clipforge.assets import FONTS
from clipforge.config import Settings
from clipforge.models import Shot
from clipforge.render.captions import (
    CAPTION_H,
    FACE_GAP,
    CaptionPage,
    PageWord,
    caption_box,
    caption_centre_y,
    caption_sprite,
    line_for,
    paginate,
    shown_words,
)
from clipforge.render.edl import EdlWord
from clipforge.render.sprites import Sprite, blend
from clipforge.render.styles import PRESETS, Style, style_for, style_key
from clipforge.render.text import FALLBACK, fallback_file, missing_letters, needs_shaping
from tests.unit.test_edl import analysis, face_track, setup, steady

YELLOW, GREEN, WHITE = (255, 214, 10), (60, 255, 90), (255, 255, 255)


def words(spec):
    """EDL words from (text, start, end) or (text, start, end, emphasis)."""
    return [
        EdlWord(i=i, text=s[0], start=s[1], end=s[2], emphasis=len(s) > 3 and s[3])
        for i, s in enumerate(spec)
    ]


def flow(texts, step=0.25):
    """``texts`` spoken back to back, ``step`` seconds each with 20 ms between them."""
    return words([(t, round(i * step, 6), round(i * step + step - 0.02, 6)) for i, t in enumerate(texts)])


def texts_of(pages):
    return [[w.text for w in p.words] for p in pages]


def page_of(texts, emphasis=None, y=1150):
    ws = [PageWord(text=t, start=i * 0.3, end=i * 0.3 + 0.28) for i, t in enumerate(texts)]
    return CaptionPage(start=0.0, end=0.3 * len(texts), words=ws, emphasis=emphasis, y=y)


def shares(sprite, rgb, tol=40):
    """For each third of the sprite, the share of its solid pixels that are close to ``rgb``."""
    a = sprite.rgba
    solid = a[..., 3] > 200
    near = solid & (np.abs(a[..., :3].astype(int) - rgb).max(axis=-1) < tol)
    thirds = np.array_split(np.arange(a.shape[1]), 3)
    return [near[:, t].sum() / max(1, solid[:, t].sum()) for t in thirds]


def test_pages_hold_one_to_three_words_within_18_characters():
    texts = ["so", "I", "went", "to", "the", "store", "and", "bought", "an", "internationalization", "big"]
    texts += ["watermelon", "yesterday"]
    pages = paginate(flow(texts))
    assert texts_of(pages) == [
        ["so", "I", "went"],
        ["to", "the", "store"],
        ["and", "bought", "an"],
        ["internationalization"],
        ["big", "watermelon"],
        ["yesterday"],
    ]
    for p in pages:
        assert 1 <= len(p.words) <= 3
        assert len(" ".join(w.text for w in p.words)) <= 18 or len(p.words) == 1


def test_pages_break_at_punctuation_and_pauses():
    ws = words(
        [
            ("Well,", 0.0, 0.3),
            ("I", 0.32, 0.4),
            ("think", 0.55, 0.7),  # 150 ms after "I": not a pause
            ("so.", 0.72, 1.0),
            ("Then", 1.3, 1.5),
            ("we", 1.52, 1.6),
            ("left", 1.85, 2.0),  # 250 ms after "we": a pause
        ]
    )
    assert texts_of(paginate(ws)) == [["Well,"], ["I", "think", "so."], ["Then", "we"], ["left"]]


def test_page_times_are_clamped():
    ws = words(
        [
            ("So", 0.0, 0.1),
            ("this", 0.6, 0.8),
            ("is", 0.8, 0.9),
            ("greaaat", 0.9, 2.5),
            ("yes", 2.6, 2.7),
            ("Yes.", 3.0, 3.1),
            ("No.", 3.12, 3.2),
        ]
    )
    pages = paginate(ws)
    assert texts_of(pages) == [["So"], ["this", "is"], ["greaaat"], ["yes"], ["Yes.", "No."]]
    got = [(p.start, p.end) for p in pages]
    want = [(0.0, 0.6), (0.6, 0.9), (0.9, 2.1), (2.6, 3.0), (3.0, 3.3)]
    assert got == [pytest.approx(w) for w in want]
    for p, q in zip(pages, pages[1:], strict=False):
        assert p.end <= q.start + 1e-9
    assert all(0.3 - 1e-9 <= p.end - p.start <= 1.2 + 1e-9 for p in pages)


def test_active_word_gets_the_accent():
    sprite = caption_sprite(page_of(["go", "go", "go"]), 0.45, Style())
    yellow, white = shares(sprite, YELLOW), shares(sprite, WHITE)
    assert yellow[1] > 0.2 and yellow[0] < 0.02 and yellow[2] < 0.02
    assert white[0] > 0.2 and white[2] > 0.2 and white[1] < 0.02


def test_active_word_pops_over_80_ms():
    page = page_of(["go"])
    widths = [caption_sprite(page, dt, Style()).rgba.shape[1] for dt in (0.0, 0.04, 0.08, 0.2)]
    assert widths[0] < widths[1] < widths[2] == widths[3]


def test_at_most_one_emphasis_word_per_page():
    ws = words([("a", 0.0, 0.1, True), ("million", 0.12, 0.5, True), ("dollars", 0.52, 0.9, True)])
    pages = paginate(ws)
    assert len(pages) == 1 and pages[0].emphasis == 1
    page = page_of(["go", "go", "go"], emphasis=2)
    on = shares(caption_sprite(page, 0.1, Style()), YELLOW)
    assert on[0] > 0.2 and on[1] < 0.02 and on[2] > 0.2
    off = shares(caption_sprite(page, 0.1, Style(), emphasis_ok=False), YELLOW)
    assert off[0] > 0.2 and off[2] < 0.02


def test_hormozi_is_capitals_in_green():
    style = style_for(Settings(caption_preset="hormozi"), {})
    page = page_of(["go", "go", "go"])
    assert shown_words(page, style.preset) == ("GO", "GO", "GO")
    sprite = caption_sprite(page, 0.45, style)
    assert shares(sprite, GREEN)[1] > 0.2 and max(shares(sprite, YELLOW)) < 0.02


def test_caption_text_drops_trailing_commas_and_periods():
    page = page_of(["Well,", "U.S.", "really?", "3.5.", "wait..."])
    assert shown_words(page, "bold_pop") == ("Well", "U.S.", "really?", "3.5", "wait")


def test_pill_puts_a_box_behind_the_active_word():
    style = style_for(Settings(caption_preset="pill"), {})
    sprite = caption_sprite(page_of(["go", "go", "go"]), 0.45, style)
    yellow = shares(sprite, YELLOW)
    assert yellow[1] > 0.5 and yellow[0] < 0.02 and yellow[2] < 0.02


def test_one_word_preset_shows_one_word():
    style = style_for(Settings(caption_preset="one_word"), {})
    edl = setup(steady(3.0), style=style)
    assert edl.pages and all(len(p.words) == 1 for p in edl.pages)
    assert [p.words[0].text for p in edl.pages] == [w.text for w in edl.words]
    line = line_for(shown_words(edl.pages[0], style.preset), style.preset)
    assert line.font == "anton" and line.size == 110
    assert max(len(p.words) for p in setup(steady(3.0)).pages) == 3


def test_edl_pages_sit_inside_the_safe_zone():
    edl = setup(steady(6.0))
    assert edl.pages and [w.text for p in edl.pages for w in p.words] == [w.text for w in edl.words]
    for p in edl.pages:
        x, y, w, h = p.box
        assert p.y == 1150 and x >= 90 and x + w <= 940 and y >= 270 and y + h <= 1250


def test_long_word_shrinks_into_the_safe_zone():
    page = page_of(["Pneumonoultramicroscopic"])
    assert line_for(shown_words(page, "bold_pop"), "bold_pop").size < 72
    assert line_for(("go",), "bold_pop").size == 72
    x, _, w, _ = caption_box(page, Style())
    assert x >= 90 and x + w <= 940
    sprite = caption_sprite(page, 0.2, Style())
    cols = np.nonzero((sprite.rgba[..., 3] > 128).any(axis=0))[0]
    assert sprite.x + cols[0] >= 90 and sprite.x + cols[-1] + 1 <= 940


# Lines that fit 800 px only once shrunk, where a popped word reaches further one side than the other
WIDE = [
    ["Now", "my", "mornings"],
    ["and", "falling", "asleep"],
    ["the", "change", "before"],
    ["My", "partner", "noticed"],
    ["this", "changed", "everything"],
]


@pytest.mark.parametrize("preset", list(PRESETS))
def test_a_line_as_wide_as_allowed_stays_inside_x_140_to_940(preset):
    style = Style(preset=preset)
    for texts in WIDE:
        page = page_of(texts)
        line = line_for(shown_words(page, preset), preset)
        assert line.width <= 800 and line.left >= 140 and line.right <= 940, texts
        for word in page.words:  # each word popped in turn
            sprite = caption_sprite(page, word.start + 0.1, style)
            cols = np.nonzero((sprite.rgba[..., 3] > 200).any(axis=0))[0]
            assert sprite.x + cols[0] >= 140 and sprite.x + cols[-1] + 1 <= 940, (texts, word.text)


def test_caption_moves_down_off_a_face_but_not_past_1250():
    half = CAPTION_H / 2
    assert caption_centre_y("face", [], None) == 1150
    assert caption_centre_y("face", [(340, 600, 400, 490)], None) == math.ceil(1090 + FACE_GAP + half)
    low = caption_centre_y("face", [(340, 700, 400, 520)], None)
    assert low == math.floor(1250 - half)
    assert caption_centre_y("face", [(0, 1000, 100, 200)], None, width=400) == 1150
    assert caption_centre_y("stacked", [], 960) == 960
    assert caption_centre_y("stream", [], 640) == 640
    assert caption_centre_y("face", [], 960) == 1150


def test_caption_keeps_off_the_eyes_of_a_face_that_fills_the_frame():
    """Round 3's Dream webcam close-ups: a face too big to clear down to y 1250 kept the line at y 1150,
    across the eyes. The line keeps off them now (a quarter to 45% of the way down the face box): under
    them when there is room, else above them."""
    half = CAPTION_H / 2
    assert caption_centre_y("face", [(0, 650, 774, 1000)], None) == math.ceil(650 + 450 + FACE_GAP + half)
    assert caption_centre_y("face", [(0, 717, 774, 1050)], None) == math.floor(717 + 262.5 - FACE_GAP - half)
    # its top goes no higher than y 640: with the eyes from y 750 to 1270, the highest place covers the least
    assert caption_centre_y("face", [(0, 100, 800, 2600)], None) == math.ceil(640 + half)


def test_every_bundled_font_draws_plain_english():
    for name in FONTS:
        assert not missing_letters(name, "Hello world, $1,000 is 50% off?!"), name


def test_missing_letters_use_the_fallback_font():
    assert not missing_letters("montserrat-black", "Hello, café Привет")
    assert missing_letters("montserrat-black", "Ελληνικά")
    assert missing_letters("bangers", "Привет")
    if fallback_file() is None:
        pytest.skip("no Arial Unicode on this Mac")
    assert not missing_letters(FALLBACK, "Привет 你好 Ελληνικά")
    assert line_for(("ПРИВЕТ",), "beast").font == FALLBACK
    assert line_for(("HELLO",), "beast").font == "bangers"
    sprite = caption_sprite(page_of(["你好"]), 0.2, Style())
    assert (sprite.rgba[..., 3] > 200).sum() > 500


def test_text_that_needs_shaping_turns_captions_off():
    assert needs_shaping("مرحبا") and needs_shaping("שלום") and needs_shaping("नमस्ते")
    for text in ("Hello", "Привет", "你好", "Tiếng Việt", "Ελληνικά"):
        assert not needs_shaping(text), text
    edl = setup(steady(3.0), texts=["مرحبا"] * 10)
    assert edl.pages == [] and any("Captions are off" in n for n in edl.notes)
    plain = setup(steady(3.0))
    assert plain.pages and plain.notes == []


def test_style_for_takes_settings_then_clip_edits():
    base = style_for(Settings(), {})
    assert base.preset == "bold_pop" and base.accent == "#FFD60A" and base.punch_ins
    assert style_for(Settings(caption_preset="hormozi"), {}).accent == "#3CFF5A"
    assert style_for(Settings(caption_preset="hormozi", accent_color="#ff0000"), {}).accent == "#FF0000"
    clip = style_for(Settings(punch_ins=False), {"preset": "neon", "punch_ins": True, "layout": "stacked"})
    assert clip.preset == "neon" and clip.accent == "#00E5FF" and clip.punch_ins
    assert style_for(Settings(), {"accent": "#123456", "sfx": None}).accent == "#123456"
    assert style_for(Settings(accent_color="#FF0000"), {"accent": "#123456"}).accent == "#123456"
    assert style_for(Settings(encoder="best", fps_cap_30=True), {}).encoder == "best"


def test_presets_match_the_settings_choices():
    assert set(PRESETS) == set(get_args(Settings.model_fields["caption_preset"].annotation))
    assert {p.font for p in PRESETS.values()} <= set(FONTS)


def test_style_key_changes_with_the_style():
    a = Style()
    assert re.fullmatch(r"[0-9a-f]{10}", style_key(a))
    assert style_key(Style()) == style_key(a)
    assert style_key(Style(accent="#FF0000")) != style_key(a)
    assert style_key(Style(punch_ins=False)) != style_key(a)
    assert style_key(Style(accent="#ff0000")) == style_key(Style(accent="#FF0000"))


def test_blend_clips_to_the_canvas():
    red = np.zeros((4, 4, 4), np.uint8)
    red[...] = (255, 0, 0, 255)
    canvas = np.zeros((10, 10, 3), np.uint8)
    blend(canvas, Sprite(red, 8, -2))
    assert (canvas[0:2, 8:10] == (0, 0, 255)).all()
    assert canvas[2:].sum() == 0 and canvas[:, :8].sum() == 0
    blend(canvas, Sprite(red, 20, 20))
    blend(canvas, Sprite(red, -4, 3))
    assert canvas[2:].sum() == 0
    half = np.zeros((10, 10, 3), np.uint8)
    blend(half, Sprite(red, 0, 0), alpha=0.5)
    assert tuple(half[0, 0]) == (0, 0, 128) and half[5, 5].sum() == 0


def test_edl_pages_move_down_off_a_face():
    edl = setup(steady(6.0), an=analysis(tracks=[face_track(0, 0, 0.0, 60.0, 0.5, size=0.225)]))
    bottom = max(y + h for _, _, y, _, h in edl.spans[0].faces)
    assert edl.pages
    for p in edl.pages:
        x, y, w, h = p.box
        assert p.y > 1150 and y >= bottom + FACE_GAP and y + h <= 1250


def test_edl_pages_sit_on_the_seam_of_a_split_layout():
    shots = [
        Shot(start_s=0.0, end_s=2.0, kind="single", tracks=[0], quality=8.0),
        Shot(start_s=2.0, end_s=60.0, kind="two_shot", tracks=[1, 2], quality=8.0),
    ]
    tracks = [
        face_track(0, 0, 0.0, 2.0, 0.5),
        face_track(1, 1, 2.0, 7.0, 0.25),
        face_track(2, 1, 2.0, 7.0, 0.75),
    ]
    edl = setup(steady(6.0), an=analysis(shots, tracks))
    assert [s.layout for s in edl.spans] == ["face", "stacked"]
    assert {p.y for p in edl.pages if p.end <= 2.0} == {1150}
    assert {p.y for p in edl.pages if p.start >= 2.0} == {960}


def test_letters_no_font_has_are_left_out():
    line = line_for(("hi\U0001f642",), "bold_pop")
    assert line.texts == ("hi",) and line.font == "montserrat-black"


def test_last_page_ends_with_the_clip():
    edl = setup([(0.0, 0.25), (0.3, 0.55), (0.9, 0.92)])
    last = edl.pages[-1]
    assert [w.text for w in last.words] == ["w2"]
    assert last.end == pytest.approx(edl.duration) and last.end - last.start < 0.3


def gaps(sprite, wide):
    """The runs of columns with no solid pixel, between solid ones, at least ``wide`` px across."""
    solid = (sprite.rgba[..., 3] > 200).any(axis=0)
    cols = np.flatnonzero(solid)
    steps = np.diff(cols) - 1
    return [int(s) for s in steps if s >= wide]


@pytest.mark.parametrize("preset", [p for p in PRESETS if not PRESETS[p].one_word])
def test_words_never_touch_even_when_one_pops(preset):
    style = Style(preset=preset)
    page = page_of(["this", "changed", "everything"])
    for t in (0.1, 0.4, 0.7):  # each word popped in turn
        assert len(gaps(caption_sprite(page, t, style), 0.1 * PRESETS[preset].size)) >= 2, t


def test_a_word_with_nothing_to_draw_takes_no_room():
    plain = line_for(("go", "now"), "bold_pop")
    line = line_for(("go", "\U0001f642", "now"), "bold_pop")
    assert line.texts == ("go", "", "now") and line.width == plain.width
    assert line_for(("\U0001f642",), "bold_pop").width == 0
    page = page_of(["\U0001f642"])
    assert caption_sprite(page, 0.1, Style()).w == 0
