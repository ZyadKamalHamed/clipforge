"""Fact cards (SPEC 8.4): a line or two of context in white on a dark see-through card, centred in the band
from y 280 to 420."""

from __future__ import annotations

import math
from functools import lru_cache

import numpy as np

from clipforge.render.sprites import SAFE_W, Sprite, centred_x, rounded
from clipforge.render.text import fit, keep_drawable, needs_shaping, text_font, text_layer

BAND = (280, 420)
FONT, SIZE, MIN_SIZE = "montserrat-bold", 44, 30
FILL, OPACITY, INK = "#111111", 0.72, "#FFFFFF"
RADIUS, PAD_X, PAD_Y = 20, 24, 18
CARD_MIN_S, CARD_MAX_S = 2.5, 4.0
MAX_CARDS = 3
CARD_GAP = 12  # between a hook kept all clip long and the cards under it


@lru_cache(maxsize=32)
def card_sprite(text: str) -> Sprite | None:
    """``text`` at 44 px on one line, else two as even as they can be, smaller when it must be to fit the
    band, on a dark rounded card centred in BAND. None when it has nothing to draw, or letters only a
    shaping library could draw."""
    if needs_shaping(text):
        return None
    name = text_font(FONT, text)
    room_h = BAND[1] - BAND[0] - 2 * PAD_Y
    lay = fit(name, keep_drawable(name, text).split(), SIZE, MIN_SIZE, SAFE_W - 2 * PAD_X, max_h=room_h)
    if lay is None:
        return None
    w, h = math.ceil(lay.width) + 2 * PAD_X, math.ceil(lay.bottom - lay.top) + 2 * PAD_Y
    card = rounded(w, h, RADIUS, FILL, OPACITY)
    items = []
    for n, ln in enumerate(lay.lines):
        ox, base = (w - ln.width) / 2 - ln.left, PAD_Y - lay.top + n * lay.pitch
        items += [(ox + x, base, lay.words[j]) for j, x in zip(ln.words, ln.xs, strict=True)]
    card.alpha_composite(text_layer(card.size, items, lay.f, INK))
    rgba = np.array(card)
    rgba.flags.writeable = False
    return Sprite(rgba, centred_x(w), (BAND[0] + BAND[1]) // 2 - h // 2)
