"""The hook title (SPEC 8.3) and the credit line (SPEC 8.4): text in a rounded box, one word of it
on a box in the accent colour, as high as it can sit from y 270 without covering a face."""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from PIL import Image, ImageFilter

from clipforge.brain.text import plain_words
from clipforge.render.sprites import (
    FACE_GAP,
    SAFE_BOTTOM,
    SAFE_TOP,
    SAFE_W,
    Box,
    Sprite,
    centred_x,
    ink_on,
    rounded,
)
from clipforge.render.styles import Style
from clipforge.render.text import fit, keep_drawable, needs_shaping, text_font, text_layer

HOOK_HOLD_S = 4.0  # shown from frame 0 and gone by then, unless it stays all clip long
HOOK_FADE_S = 0.2  # the fade out, at the end of the hold
FONT, SIZE, MIN_SIZE = "montserrat-extrabold", 68, 24
RADIUS, PAD = 18, 24
SCALES = (1.0, 0.85, 0.72)
HAIR = 0.5  # face boxes grow upward by half their height to take in the hair
FOREHEAD = 0.2  # the top fifth of a face box, above the eyes, which a hook may cover when it must
LOWEST_TOPS = (640, 760)  # the hook's box starts in the canvas's top third, else the safe zone's top half
ACCENT_PAD_X, ACCENT_PAD_Y, ACCENT_RADIUS = 0.16, 0.12, 0.18  # the accent word's box, in font sizes
CREDIT_FONT, CREDIT_SIZE, CREDIT_MIN = "montserrat-bold", 32, 24
CREDIT_OPACITY = 0.8
CREDIT_GAP = 12  # between the hook's box and the credit line
SHADOW_BLUR, SHADOW_ALPHA, SHADOW_DY = 3, 0.6, 2
OUTLINE = 2  # px of dark edge round the credit line's letters, so it reads over white


@dataclass(frozen=True)
class HookSprite(Sprite):
    scale: float = 1.0  # one of SCALES, so the same hook can be drawn again


def hook_sprite(
    title: str, accent_word: str | None, style: Style, *, scale: float = 1.0
) -> HookSprite | None:
    """The hook at ``scale`` of its full size, at most ``scale`` x 850 px wide: one line, else two as even as
    they can be, in a smaller font when two lines at that size cannot hold it, centred at the top of the safe
    zone.
    None when it has nothing to draw, or letters only a shaping library could draw."""
    if needs_shaping(title):
        return None
    return _hook(title, accent_word or "", scale, style.hook_fill, style.hook_text, style.accent)


@lru_cache(maxsize=64)
def _hook(title: str, accent_word: str, scale: float, fill: str, ink: str, accent: str) -> HookSprite | None:
    name = text_font(FONT, title)
    words = keep_drawable(name, title).split()
    want = plain_words(accent_word)
    k = next((i for i, w in enumerate(words) if want and plain_words(w) == want), None)
    pad = round(PAD * scale)
    lay = fit(
        name,
        words,
        round(SIZE * scale),
        MIN_SIZE,
        round(SAFE_W * scale) - 2 * pad,
        accent=k,
        accent_pad=(ACCENT_PAD_X, ACCENT_PAD_Y),
    )
    if lay is None:
        return None
    w, h = math.ceil(lay.width) + 2 * pad, math.ceil(lay.bottom - lay.top) + 2 * pad
    img = rounded(w, h, RADIUS * scale, fill)
    marks: list[tuple[float, float, str, str]] = []
    for n, ln in enumerate(lay.lines):
        ox, base = (w - ln.width) / 2 - ln.left, pad - lay.top + n * lay.pitch
        for j, x in zip(ln.words, ln.xs, strict=True):
            if j == lay.accent:
                a, _, b, _ = lay.f.getbbox(lay.words[j], anchor="ls")
                x0, y0 = round(ox + x + a - lay.pad), round(base + lay.span[0])
                x1, y1 = round(ox + x + b + lay.pad), round(base + lay.span[1])
                img.alpha_composite(rounded(x1 - x0, y1 - y0, ACCENT_RADIUS * lay.size, accent), (x0, y0))
            marks.append((ox + x, base, lay.words[j], ink_on(accent) if j == lay.accent else ink))
    for colour in dict.fromkeys(m[3] for m in marks):
        img.alpha_composite(text_layer(img.size, [m[:3] for m in marks if m[3] == colour], lay.f, colour))
    rgba = np.array(img)
    rgba.flags.writeable = False
    return HookSprite(rgba, centred_x(w), SAFE_TOP, scale)


def place_hook(
    title: str,
    accent_word: str | None,
    style: Style,
    faces_px: Sequence[Box],
    *,
    avoid: Sequence[Box] = (),
    hair: float = HAIR,
    under: int = 0,
) -> HookSprite | None:
    """The hook at the highest place from y 270 where it covers no face (each grown upward by ``hair`` of its
    height, or cut from the top by ``-hair`` of it) and no box in ``avoid``, keeping ``under`` px clear below
    it for the credit line: at full size, else 85 %, else 72 %, starting in the top third of the canvas, else
    in the top half of the safe zone (under the captions on a high seam). None when no place clears them."""
    blocked = [(x, y - hair * h, w, h * (1 + hair)) for x, y, w, h in faces_px] + list(avoid)
    for lowest in LOWEST_TOPS:
        for scale in SCALES:
            sprite = hook_sprite(title, accent_word, style, scale=scale)
            if sprite is None:
                return None
            y = highest(sprite.x, sprite.w, sprite.h + under, blocked, lowest)
            if y is not None:
                return dataclasses.replace(sprite, y=y)
    return None


def highest(x: int, w: int, h: int, blocked: Sequence[Sequence[float]], lowest: int) -> int | None:
    """The highest top from y 270 down to ``lowest`` for a ``w`` x ``h`` box at ``x`` that stays inside the
    safe zone and FACE_GAP clear of every box in ``blocked``."""
    for top in sorted({SAFE_TOP, *(math.ceil(by + bh + FACE_GAP) for _, by, _, bh in blocked)}):
        if top > lowest or top + h > SAFE_BOTTOM:
            break
        if top >= SAFE_TOP and not any(near((x, top, w, h), b) for b in blocked):
            return top
    return None


def near(a: Sequence[float], b: Sequence[float]) -> bool:
    """Whether box ``a`` comes closer than FACE_GAP to box ``b``."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    g = FACE_GAP
    return ax < bx + bw + g and bx - g < ax + aw and ay < by + bh + g and by - g < ay + ah


def below_forehead(face: Sequence[float]) -> tuple[float, float, float, float]:
    """The part of a face box that a hook covering the face would cover: all but its top fifth, the
    forehead, which a hook may cover when it must (SPEC 8.3)."""
    x, y, w, h = face
    return (x, y + FOREHEAD * h, w, h * (1 - FOREHEAD))


def hook_alpha(t: float, end: float) -> float:
    """The hook's (and the credit line's) opacity at output time ``t``: full from frame 0, fading out over
    the last HOOK_FADE_S before ``end``."""
    if t >= end:
        return 0.0
    return min(1.0, (end - t) / HOOK_FADE_S)


@lru_cache(maxsize=32)
def credit_sprite(text: str, below_y: int) -> Sprite | None:
    """``text`` in white at 32 px with a thin dark outline, a soft shadow and 80 % opacity, its top at
    ``below_y``, centred inside
    the safe zone: smaller, then cut short, when it is long. None when it has nothing to draw, or letters
    only a shaping library could draw."""
    if needs_shaping(text):
        return None
    name = text_font(CREDIT_FONT, text)
    m = 2 * SHADOW_BLUR + SHADOW_DY  # room for the shadow
    lay = fit(name, keep_drawable(name, text).split(), CREDIT_SIZE, CREDIT_MIN, SAFE_W - 2 * m, max_lines=1)
    if lay is None:
        return None
    (line,) = lay.lines
    w, h = math.ceil(line.width) + 2 * m, math.ceil(line.bottom - line.top) + 2 * m
    items = [
        (m - line.left + x, m - line.top, lay.words[j]) for j, x in zip(line.words, line.xs, strict=True)
    ]
    shadow = text_layer((w, h), [(x, y + SHADOW_DY, t) for x, y, t in items], lay.f, "#000000")
    soft = shadow.getchannel("A").filter(ImageFilter.GaussianBlur(SHADOW_BLUR))
    shadow.putalpha(soft.point(lambda v: round(v * SHADOW_ALPHA)))
    out = Image.alpha_composite(shadow, text_layer((w, h), items, lay.f, "#000000", stroke=OUTLINE))
    out.alpha_composite(text_layer((w, h), items, lay.f, "#FFFFFF"))
    out.putalpha(out.getchannel("A").point(lambda v: round(v * CREDIT_OPACITY)))
    rgba = np.array(out)
    rgba.flags.writeable = False
    return Sprite(rgba, centred_x(w), below_y)
