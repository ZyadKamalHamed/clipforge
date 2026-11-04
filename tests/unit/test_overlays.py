"""Task 10: the hook title, the credit line, fact cards and emoji (SPEC 8.3 to 8.5)."""

from __future__ import annotations

import io

import httpx
import numpy as np
import pytest
from PIL import Image

from clipforge.brain.wording import credit_channel
from clipforge.models import EmojiCue, FactCard, Packaging, ProjectOptions, SourceMeta
from clipforge.paths import Paths
from clipforge.render import emoji as em
from clipforge.render.cards import BAND, CARD_GAP, card_sprite
from clipforge.render.edl import card_layer, hook_layer
from clipforge.render.emoji import emoji_file, emoji_name, emoji_sprite, fetched, pop_scale
from clipforge.render.hook import (
    CREDIT_GAP,
    FOREHEAD,
    HAIR,
    HOOK_FADE_S,
    HOOK_HOLD_S,
    credit_sprite,
    hook_alpha,
    hook_sprite,
    place_hook,
)
from clipforge.render.sprites import FACE_GAP, SAFE_BOTTOM, SAFE_LEFT, SAFE_RIGHT, SAFE_TOP
from clipforge.render.styles import Style
from tests.unit.test_edl import setup, steady

TITLE = "Why most people never get rich"  # two lines at 68 px


def bands(sprite, dark=True):
    """How many separate bands of rows hold dark (or, with ``dark`` False, light) opaque pixels."""
    rgb, alpha = sprite.rgba[..., :3], sprite.rgba[..., 3]
    ink = (rgb.max(axis=2) < 80) if dark else (rgb.min(axis=2) > 200)
    rows = (ink & (alpha > 200)).any(axis=1)
    return int(rows[0]) + int(np.count_nonzero(rows[1:] & ~rows[:-1]))


def line_widths(sprite):
    """How wide each band of dark ink is, top to bottom."""
    rgb, alpha = sprite.rgba[..., :3], sprite.rgba[..., 3]
    ink = (rgb.max(axis=2) < 80) & (alpha > 200)
    rows = np.flatnonzero(ink.any(axis=1))
    groups = np.split(rows, np.flatnonzero(np.diff(rows) > 1) + 1)
    out = []
    for g in groups:
        cols = np.flatnonzero(ink[g[0] : g[-1] + 1].any(axis=0))
        out.append(int(cols[-1] - cols[0] + 1))
    return out


def overlaps(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def grown(face, hair=HAIR):
    x, y, w, h = face
    return (x, y - hair * h, w, h * (1 + hair))


def colour(sprite, hex_colour, tol=8):
    r, g, b = (int(hex_colour[k : k + 2], 16) for k in (1, 3, 5))
    px = sprite.rgba.astype(int)
    near = (abs(px[..., 0] - r) <= tol) & (abs(px[..., 1] - g) <= tol) & (abs(px[..., 2] - b) <= tol)
    return near & (px[..., 3] > 200)


def inside_safe_zone(box):
    x, y, w, h = box
    return x >= SAFE_LEFT and x + w <= SAFE_RIGHT and y >= SAFE_TOP and y + h <= SAFE_BOTTOM


def png(size=128):
    buf = io.BytesIO()
    Image.new("RGBA", (size, size), (255, 80, 0, 255)).save(buf, "PNG")
    return buf.getvalue()


def test_hook_wraps_to_two_lines_within_860_px():
    one = hook_sprite("Stop doing this", None, Style())
    two = hook_sprite(TITLE, None, Style())
    assert bands(one) == 1 and bands(two) == 2
    for s in (one, two):
        assert s.w <= 850 and s.y == SAFE_TOP and inside_safe_zone(s.box)
    # a white box with rounded corners: the middle of its left edge is white, its corner see-through
    assert tuple(two.rgba[two.h // 2, 3]) == (255, 255, 255, 255) and two.rgba[0, 0, 3] == 0
    # never more than two lines, however long
    long = hook_sprite(
        "This is a much longer hook title than anyone should ever write for a clip", None, Style()
    )
    assert bands(long) == 2 and long.w <= 850
    # the two lines as even as they can be
    for s in (two, long):
        short_line, long_line = sorted(line_widths(s))
        assert short_line > 0.7 * long_line
    assert hook_sprite("   ", None, Style()) is None


def test_hook_shrinks_for_a_long_title():
    short = hook_sprite(TITLE, None, Style())
    wordy = "Why almost everybody you know never gets rich"
    long = hook_sprite(wordy, None, Style())
    assert bands(long) == 2 and long.w <= 850
    assert long.h < short.h  # the same two lines in a smaller font
    smaller = [hook_sprite(TITLE, None, Style(), scale=s) for s in (0.85, 0.72)]
    assert short.h > smaller[0].h > smaller[1].h
    # a title already shrunk to fit still comes out at about 85 % and 72 % of its full size
    for s in (0.85, 0.72):
        scaled = hook_sprite(wordy, None, Style(), scale=s)
        assert abs(scaled.w / long.w - s) < 0.05 and abs(scaled.h / long.h - s) < 0.05


def test_hook_never_covers_a_face():
    style = Style()
    # room above the face and its hair: the hook stays at the top
    low = (340, 700, 400, 400)
    top = place_hook(TITLE, None, style, [low])
    assert top.y == SAFE_TOP and not overlaps(top.box, grown(low))
    # a face high up: the hook goes just under it
    high = (340, 120, 400, 300)
    under = place_hook(TITLE, None, style, [high])
    assert under.y == 120 + 300 + FACE_GAP and not overlaps(under.box, grown(high))
    # room for the hook at 85 % only: it shrinks rather than move down
    h85 = hook_sprite(TITLE, None, style, scale=0.85).h
    roof = SAFE_TOP + h85 + FACE_GAP
    tight = (340, roof + 100, 400, 200)  # grown upward by 100 px, its hair starts at ``roof``
    fit = place_hook(TITLE, None, style, [tight])
    assert (fit.y, fit.h) == (SAFE_TOP, h85) and not overlaps(fit.box, grown(tight))
    # the hair counts: a face that clears the hook only without its hair
    hair = (340, SAFE_TOP + hook_sprite(TITLE, None, style).h + 20, 400, 300)
    assert place_hook(TITLE, None, style, [hair]) is None
    assert place_hook(TITLE, None, style, [hair], hair=0.0).y == SAFE_TOP
    # a face over the whole top of the frame: nothing clears
    assert place_hook(TITLE, None, style, [(200, 200, 700, 900)]) is None


def test_hook_keeps_clear_of_captions_and_the_credit():
    style = Style()
    caption = (140, 300, 800, 90)
    s = place_hook(TITLE, None, style, [], avoid=[caption])
    assert s.y == 300 + 90 + FACE_GAP
    # room kept under the box for the credit line
    face = (340, 700, 400, 400)
    free = place_hook(TITLE, None, style, [face], hair=0.0)
    roomy = place_hook(TITLE, None, style, [face], hair=0.0, under=700 - FACE_GAP - SAFE_TOP - free.h + 1)
    assert roomy.h < free.h


def test_hook_accent_word_has_its_box():
    style = Style()
    text = "Nobody tells you this"  # one line, with room for the accent box
    plain = hook_sprite(text, None, style)
    boxed = hook_sprite(text, "tells", style)
    assert not colour(plain, style.accent).any() and bands(boxed) == 1
    mask = colour(boxed, style.accent)
    assert mask.sum() > 1500
    cols = np.flatnonzero(mask.any(axis=0))
    assert cols[-1] - cols[0] < boxed.w / 3  # around one word
    # the word's ink is black on the yellow box
    rows = np.flatnonzero(mask.any(axis=1))
    inside = boxed.rgba[rows[0] : rows[-1] + 1, cols[0] : cols[-1] + 1]
    assert ((inside[..., :3].max(axis=2) < 60) & (inside[..., 3] > 200)).sum() > 300
    # found whatever its case and punctuation; a word not in the title gets no box
    assert colour(hook_sprite("Nobody TELLS you this!", "tells", style), style.accent).sum() > 1500
    assert colour(hook_sprite("Nobody tells you THIS!", "this", style), style.accent).sum() > 1500
    assert not colour(hook_sprite(text, "nothing", style), style.accent).any()
    # white ink on a dark accent, and the box and text colours follow the style
    blue = Style(accent="#1E3A8A", hook_fill="#111111", hook_text="#FFFFFF")
    dark = hook_sprite(text, "tells", blue)
    assert colour(dark, "#1E3A8A").sum() > 1500 and colour(dark, "#111111").sum() > 10000
    assert bands(dark, dark=False) == 1


def test_hook_shows_from_frame_0_and_fades_out_by_4_s():
    assert hook_alpha(0.0, HOOK_HOLD_S) == 1.0 and hook_alpha(HOOK_HOLD_S - HOOK_FADE_S, HOOK_HOLD_S) == 1.0
    assert hook_alpha(HOOK_HOLD_S - HOOK_FADE_S / 2, HOOK_HOLD_S) == pytest.approx(0.5)
    assert hook_alpha(HOOK_HOLD_S, HOOK_HOLD_S) == 0.0 and hook_alpha(9.0, HOOK_HOLD_S) == 0.0


def test_credit_only_for_other_peoples_content():
    meta = SourceMeta(
        source_kind="youtube",
        video_id="v",
        title="t",
        channel=" Lex  Fridman ",
        channel_url=None,
        duration_s=60.0,
    )
    assert credit_channel(meta, "own", ProjectOptions()) is None
    assert credit_channel(meta, "not_cleared", ProjectOptions()) == "Lex Fridman"
    assert credit_channel(meta, "own", ProjectOptions(credit=True)) == "Lex Fridman"
    assert credit_channel(meta, "creative_commons", ProjectOptions(credit=False)) is None
    # on screen: under the hook for its first 4 s
    edl = setup(steady(8.0), credit_name="Lex Fridman")
    hook, credit = edl.hook, edl.credit
    assert credit["text"] == "Source: Lex Fridman" and (credit["start"], credit["end"]) == (0.0, HOOK_HOLD_S)
    hook_bottom = hook["box"][1] + hook["box"][3]
    assert hook_bottom < credit["box"][1] <= hook_bottom + 20 and inside_safe_zone(credit["box"])
    assert setup(steady(8.0)).credit is None
    # 32 px white letters with a dark edge, at 80 % opacity
    sprite = credit_sprite("Source: Lex Fridman", below_y=500)
    assert sprite.y == 500 and inside_safe_zone(sprite.box) and sprite.h < 50
    assert sprite.rgba[..., 3].max() == 204
    opaque = sprite.rgba[sprite.rgba[..., 3] == 204][:, :3]
    assert (opaque.min(axis=1) > 240).mean() > 0.4 and (opaque.max(axis=1) < 40).mean() > 0.25
    # a long channel name is shrunk or cut to fit
    wide = credit_sprite("Source: " + "A Very Long Channel Name " * 6, below_y=500)
    assert inside_safe_zone(wide.box)


def test_card_sprite_fits_its_band():
    for text in (
        "Tim Urban writes Wait But Why",
        "Procrastination: putting off a task you know you should do now",
    ):
        card = card_sprite(text)
        assert BAND == (280, 420) and card.y >= 280 and card.y + card.h <= 420
        assert inside_safe_zone(card.box)
        # a dark see-through card with white text
        edge = card.rgba[card.h // 2, 4]
        assert edge[:3].max() < 60 and 100 < edge[3] < 255
        assert bands(card, dark=False) == (1 if len(text) < 40 else 2)


def test_emoji_name_from_codepoints():
    assert emoji_name(chr(0x1F622)) == "emoji_u1f622"
    assert emoji_name(chr(0x2764) + chr(0xFE0F)) == "emoji_u2764"
    family = chr(0x200D).join(chr(c) for c in (0x1F468, 0x1F469, 0x1F467))
    assert emoji_name(family) == "emoji_u1f468_200d_1f469_200d_1f467"
    assert emoji_name(chr(0x1F44D) + chr(0x1F3FB)) == "emoji_u1f44d_1f3fb"
    assert emoji_name("#" + chr(0xFE0F) + chr(0x20E3)) == "emoji_u0023_20e3"


def test_emoji_file_checks_and_caches(tmp_path):
    paths = Paths(tmp_path / "home")
    calls = []

    def fetch(url, max_bytes):
        calls.append((url, max_bytes))
        return png()

    fire = chr(0x1F525)
    got = emoji_file(paths, fire, fetch=fetch)
    assert got == paths.emoji / "emoji_u1f525.png" and got.read_bytes() == png()
    pinned = "noto-emoji/e20cbc2bbec1926686be9f9bee7d1d2cfa1fea0e/2D/png/128/emoji_u1f525.png"
    assert len(calls) == 1 and calls[0][0].endswith(pinned) and calls[0][1] == 64 * 1024
    assert emoji_file(paths, fire, fetch=fetch) == got and len(calls) == 1  # cached

    # refused and left out: not a PNG, the wrong size, a failed fetch, text that is no emoji
    sad = chr(0x1F622)

    def offline(url, max_bytes):
        raise httpx.ConnectError("offline")

    assert emoji_file(paths, sad, fetch=lambda url, max_bytes: b"<html>not found</html>") is None
    assert emoji_file(paths, sad, fetch=lambda url, max_bytes: png(64)) is None
    assert emoji_file(paths, sad, fetch=offline) is None
    assert emoji_file(paths, "fire", fetch=fetch) is None and emoji_file(paths, "", fetch=fetch) is None
    assert len(calls) == 1 and sorted(p.name for p in paths.emoji.iterdir()) == ["emoji_u1f525.png"]

    # 120 px, smaller while it pops in
    assert (emoji_sprite(got).w, emoji_sprite(got).h) == (120, 120)
    assert emoji_sprite(got, scale=pop_scale(0.0)).w < 120 and pop_scale(em.POP_S) == 1.0
    assert max(pop_scale(k * em.POP_S / 20) for k in range(21)) <= 1.1

    # the EDL's emoji keep only those with a PNG
    entries = [{"emoji": fire, "start": 1.0}, {"emoji": sad, "start": 8.0}]
    kept = fetched(paths, entries, fetch=offline)
    assert [e["emoji"] for e in kept] == [fire] and kept[0]["png"] == str(got)


def test_emoji_cues_are_spaced_6_s_apart():
    fire = chr(0x1F525)
    # word i starts at 0.3 * i: 0.6, 3.0, 7.2, 9.0, 13.5
    cues = [EmojiCue(sid="S1", word=f"w{i}", emoji=fire) for i in (2, 10, 24, 30, 45)]
    cues.append(EmojiCue(sid="S1", word="nowhere", emoji=fire))
    edl = setup(steady(16.0), packaging=Packaging(hook_title="A hook", emoji_cues=cues))
    assert [e["start"] for e in edl.emoji] == pytest.approx([0.6, 7.2, 13.5], abs=0.02)
    for e in edl.emoji:
        page = next(p for p in edl.pages if p.start <= e["start"] + 1e-6 < p.end)
        x, y, w, h = e["box"]
        assert (w, h) == (120, 120) and inside_safe_zone(e["box"])
        assert y + h + FACE_GAP <= page.box[1] and abs((x + w / 2) - (page.box[0] + page.box[2] / 2)) <= 1
        assert e["end"] > e["start"]
    off = setup(
        steady(16.0), packaging=Packaging(hook_title="A hook", emoji_cues=cues), style=Style(emoji=False)
    )
    assert off.emoji == []


def test_edl_places_the_hook_clear_of_faces():
    edl = setup(steady(6.0))
    assert edl.hook["box"][1] == SAFE_TOP and edl.hook["covers"] is None and edl.hook["scale"] == 1.0
    assert inside_safe_zone(edl.hook["box"])
    style = Style()
    pack = Packaging(hook_title=TITLE)
    # only the face itself clears: the hook may cover hair
    layer, _ = hook_layer(pack, style, [(240, 500, 600, 700)], [], 30.0, None)
    assert layer["covers"] == "hair" and layer["box"][1] == SAFE_TOP
    # nothing clears, not even below the forehead: the smallest hook at the top, covering the face, for QA
    layer, _ = hook_layer(pack, style, [(140, 100, 800, 1200)], [], 30.0, None)
    assert layer["covers"] == "face" and layer["scale"] == 0.72 and layer["box"][1] == SAFE_TOP
    # no title: no hook, and the credit goes to the top
    layer, credit = hook_layer(Packaging(hook_title=" "), style, [], [], 30.0, "Source: Lex")
    assert layer is None and credit["box"][1] == SAFE_TOP


def test_hook_may_cover_a_forehead_and_the_credit_moves_down():
    """A face high in a full-height crop, as in round 1's podcast clips: the hook takes the hair and the top
    fifth of the face, never the eyes, and the credit line moves down to just above the captions."""
    style = Style()
    pack = Packaging(hook_title="Nights I cry myself to sleep")
    face = (236, 328, 608, 470)  # its top fifth ends at y 422, its eyes are at about y 510
    caption = (290, 1105, 500, 97)
    hook, credit = hook_layer(pack, style, [face], [caption], 30.0, "Source: Jay Shetty Podcast")
    x, y, w, h = hook["box"]
    assert hook["covers"] == "forehead" and y == SAFE_TOP and y + h + FACE_GAP <= 328 + FOREHEAD * 470
    assert credit["box"][1] + credit["box"][3] + CREDIT_GAP == caption[1]
    assert credit["box"][1] >= 328 + 470 + FACE_GAP and inside_safe_zone(credit["box"])
    assert (credit["start"], credit["end"]) == (0.0, HOOK_HOLD_S)
    # with no caption showing, the credit line sits where the captions would
    _, alone = hook_layer(pack, style, [face], [], 30.0, "Source: Jay Shetty Podcast")
    assert alone["box"][1] + alone["box"][3] / 2 == pytest.approx(1150, abs=1)
    # room for the hook and the credit line together over the hair: they stay together
    hook, credit = hook_layer(pack, style, [(240, 600, 600, 700)], [caption], 30.0, "Source: Lex")
    assert hook["covers"] == "hair" and credit["box"][1] == hook["box"][1] + hook["box"][3] + CREDIT_GAP
    # a face down to the captions leaves no room above them: both stay together over the forehead
    tall = (140, 280, 800, 1200)
    hook, credit = hook_layer(pack, style, [tall], [caption], 30.0, "Source: Lex")
    assert hook["covers"] == "forehead" and credit["box"][1] == hook["box"][1] + hook["box"][3] + CREDIT_GAP
    assert credit["box"][1] + credit["box"][3] + FACE_GAP <= 280 + FOREHEAD * 1200
    # and when nothing clears even the eyes, the credit line still goes under the smallest hook
    hook, credit = hook_layer(pack, style, [(140, 100, 800, 1200)], [caption], 30.0, "Source: Lex")
    assert (hook["covers"], hook["scale"], hook["box"][1]) == ("face", 0.72, SAFE_TOP)
    assert credit["box"][1] == hook["box"][1] + hook["box"][3] + CREDIT_GAP


def test_hook_sits_under_the_captions_on_a_high_seam():
    """The stream layout, as in round 2's gaming clips: a facecam face high up and the captions on the seam
    at y 640 leave room only under the captions, which the middle of the safe zone still allows."""
    style = Style()
    face, caption = (340, 164, 400, 301), (140, 595, 800, 97)
    hook, credit = hook_layer(Packaging(hook_title=TITLE), style, [face], [caption], 30.0, "Source: Peterbot")
    assert hook["covers"] is None and (hook["box"][1], hook["scale"]) == (595 + 97 + FACE_GAP, 1.0)
    assert credit["box"][1] == hook["box"][1] + hook["box"][3] + CREDIT_GAP
    # its box starts no lower than y 760, the middle of the safe zone
    assert place_hook(TITLE, None, style, [face], avoid=[(140, 595, 800, 155)]).y == 760
    assert place_hook(TITLE, None, style, [face], avoid=[(140, 595, 800, 156)]) is None


def test_credit_reads_over_white():
    """A thin dark outline keeps the credit line readable over a white logo or wall (round 1, Dream)."""
    sprite = credit_sprite("Source: Dream", below_y=500)
    white = Image.new("RGBA", (sprite.w, sprite.h), "#FFFFFF")
    over = np.array(Image.alpha_composite(white, Image.fromarray(sprite.rgba)).convert("L"))
    assert over.min() < 90


def test_edl_cards_follow_their_sentences():
    cards = [FactCard(sid="S2", text="Tim Urban writes Wait But Why"), FactCard(sid="S1", text="A blog")]
    edl = setup(steady(16.0), split=30, packaging=Packaging(hook_title="A hook", fact_cards=cards))
    first, second = edl.cards
    # S1 starts at 0 but waits for the hook to go; S2 starts at 9.0
    assert first["text"] == "A blog" and first["start"] == pytest.approx(HOOK_HOLD_S)
    assert second["start"] == pytest.approx(9.0, abs=0.02)
    for c in edl.cards:
        assert 2.5 - 1e-6 <= c["end"] - c["start"] <= 4.0 + 1e-6
        assert BAND[0] <= c["box"][1] and c["box"][1] + c["box"][3] <= BAND[1]
    assert first["end"] <= second["start"] + 1e-6
    # with the hook kept all clip long, cards sit under it
    whole = setup(
        steady(16.0),
        split=30,
        packaging=Packaging(hook_title="A hook", fact_cards=cards),
        style=Style(hook_whole_clip=True),
    )
    hook = whole.hook["box"]
    assert all(c["box"][1] >= hook[1] + hook[3] for c in whole.cards)
    assert whole.cards[0]["start"] == pytest.approx(0.0)
    # a credit line moved down to the captions is not in the way: the card goes just under the hook
    pack = Packaging(hook_title="A hook", fact_cards=cards)
    low = {"text": "Source: Lex", "start": 0.0, "end": HOOK_HOLD_S, "box": [400, 1047, 280, 46]}
    first, _ = card_layer(pack, whole.style, whole.words, whole.hook, low, whole.duration, [])
    assert first["box"][1] == hook[1] + hook[3] + CARD_GAP
    # one right under the hook is: the card goes under it
    stacked = low | {"box": [400, hook[1] + hook[3] + CREDIT_GAP, 280, 46]}
    first, _ = card_layer(pack, whole.style, whole.words, whole.hook, stacked, whole.duration, [])
    assert first["box"][1] == hook[1] + hook[3] + CREDIT_GAP + 46 + CARD_GAP
