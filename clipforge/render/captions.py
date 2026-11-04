"""Word captions (SPEC 8.1 and 8.2): pages of one to three words timed to the speech, drawn on one line with
the active word in the accent and a short pop, and placed clear of the faces."""

from __future__ import annotations

import bisect
import math
import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from pydantic import BaseModel

from clipforge.render.sprites import CENTRE_X, FACE_GAP, SAFE_BOTTOM, SAFE_TOP, Box, Sprite, ink_on
from clipforge.render.styles import PRESETS, Style, caption_font
from clipforge.render.text import font, keep_drawable, text_font

CENTRE_Y = 1150
MAX_W = 800  # x 140 to 940: centred on the frame and clear of the buttons on the right
CAPTION_H = 112  # a Bold Pop line, popped
EYES = (0.25, 0.45)  # a face's brows to its lower lids, as parts of the way down its box
UP_TOP = 640  # a line moved up keeps its top from here down, under the hook's and the cards' third
POP, POP_S = 1.08, 0.08
MIN_SIZE = 16
STROKE = "#000000"
SHADOW_DY, SHADOW_BLUR, SHADOW_ALPHA = 4, 4, 0.5  # an 8 px blur is a Gaussian of radius 4
GLOW_BLUR = 6
PILL_X = 0.2  # the box's padding either side of the word, in font sizes
PAD = 24  # room around the text for its shadow, glow and box
SPLIT = ("stacked", "gameplay", "stream")
CLAUSE_END = re.compile(r"[,.!?;:\u2026][\"')\]\u2019\u201d]*$")
ABBREVIATION = re.compile(r"(?:[^\W\d_]\.){2,}")
TRIM = ",;:\u2026\u2013\u2014-"  # trimmed from a word's edges
EPS = 1e-6


class PageWord(BaseModel):
    i: int | None = None  # the transcript word
    text: str
    start: float
    end: float


class CaptionPage(BaseModel):
    start: float
    end: float
    words: list[PageWord]
    emphasis: int | None = None  # the one word in the accent all page long
    y: int = CENTRE_Y  # the line's centre on the canvas
    box: Box | None = None  # how far the text reaches on the canvas, pop included


@dataclass(frozen=True)
class Line:
    font: str
    size: int
    stroke: int
    texts: tuple[str, ...]
    xs: tuple[float, ...]  # each word's centre on the canvas
    left: int  # how far the text reaches with any word popped: canvas x
    right: int
    up: int  # and above and below the line's centre
    down: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return 2 * max(self.up, self.down)


def shown(text: str) -> str:
    """A word as captions show it: without the commas, full stops, colons, ellipses and dashes at its edges
    (an abbreviation like U.S. keeps its stops); question and exclamation marks stay."""
    t = unicodedata.normalize("NFC", text).strip().lstrip(TRIM)
    while True:
        cut = t.rstrip(TRIM)
        if cut.endswith(".") and not ABBREVIATION.fullmatch(cut):
            cut = cut.rstrip(".")
        if cut == t:
            return t
        t = cut


def shown_words(page: CaptionPage, preset: str) -> tuple[str, ...]:
    caps = PRESETS[preset].capitals
    return tuple(shown(w.text).upper() if caps else shown(w.text) for w in page.words)


def paginate(
    words: Sequence[Any],
    *,
    max_words: int = 3,
    max_chars: int = 18,
    pause_s: float = 0.2,
    min_s: float = 0.3,
    max_s: float = 1.2,
) -> list[CaptionPage]:
    """Caption pages from ``words`` (each with ``text``, ``start`` and ``end`` in output time, and maybe ``i``
    and ``emphasis``): up to ``max_words`` words and ``max_chars`` characters (a longer word sits alone),
    broken after punctuation and at pauses of ``pause_s`` or more. Each page shows from its first word until
    the next page, at least ``min_s`` and at most ``max_s``; one that would flash for less than ``min_s``
    joins a neighbour when the limits allow."""

    def fits(g: list[Any]) -> bool:
        chars = len(" ".join(shown(w.text) for w in g))
        return (
            len(g) <= max_words
            and (len(g) == 1 or chars <= max_chars)
            and g[-1].end - g[0].start <= max_s + EPS
        )

    groups: list[list[Any]] = []
    cut = True
    for w in words:
        if not shown(w.text):
            cut = cut or bool(CLAUSE_END.search(w.text))
            continue
        if cut or not fits([*groups[-1], w]) or w.start - groups[-1][-1].end >= pause_s - EPS:
            groups.append([w])
        else:
            groups[-1].append(w)
        cut = bool(CLAUSE_END.search(w.text))
    k = 0
    while k < len(groups):
        nxt = groups[k + 1][0].start if k + 1 < len(groups) else math.inf
        if nxt - groups[k][0].start < min_s - EPS:
            if fits(groups[k] + groups[k + 1]):
                groups[k] += groups.pop(k + 1)
                continue
            if k and fits(groups[k - 1] + groups[k]):
                groups[k - 1] += groups.pop(k)
                k -= 1
                continue
        k += 1
    pages = []
    for k, g in enumerate(groups):
        start = g[0].start
        nxt = groups[k + 1][0].start if k + 1 < len(groups) else None
        end = max(g[-1].end, start + min_s, -math.inf if nxt is None else nxt)
        end = min(end, start + max_s, math.inf if nxt is None else nxt)
        loud = [j for j, w in enumerate(g) if getattr(w, "emphasis", False)]
        pages.append(
            CaptionPage(
                start=start,
                end=end,
                words=[PageWord(i=getattr(w, "i", None), text=w.text, start=w.start, end=w.end) for w in g],
                emphasis=max(loud, key=lambda j: (len(shown(g[j].text)), -j)) if loud else None,
            )
        )
    return pages


@lru_cache(maxsize=512)
def line_for(texts: tuple[str, ...], preset: str, face: str | None = None) -> Line:
    """The words on one line in ``face`` or the preset's font (or the fallback when it lacks a letter),
    centred on the frame and shrunk until the line fits MAX_W with any word popped, then moved back inside
    x 140 to 940 when a popped word reaches further one side than the other."""
    p = PRESETS[preset]
    name = text_font(face or p.font, " ".join(texts))
    texts = tuple(keep_drawable(name, t) for t in texts)
    size = p.size
    drawn = [j for j, t in enumerate(texts) if t]
    if not drawn:
        return Line(name, size, 0, texts, (float(CENTRE_X),) * len(texts), CENTRE_X, CENTRE_X, 0, 0)
    while True:
        stroke = round(p.stroke * size / p.size)
        f = font(name, size)
        pf, pstroke = _popped(name, size, stroke)
        # Each word has room to pop without its neighbours moving, and the gap allows for both strokes (and
        # a box's padding), so the ink of two words stays about a space apart
        gap = f.getlength(" ") + 2 * pstroke + (2 * PILL_X * pf.size if p.box else 0.0)
        xs, end = [], None
        for t in texts:
            w = pf.getlength(t) if t else 0.0
            left = 0.0 if end is None else end + gap
            xs.append(left + w / 2)
            if t:
                end = left + w
        base, popped = _reach(f, stroke, texts, p.box), _reach(pf, pstroke, texts, p.box)
        lo = min(xs[j] + base[j][0] for j in drawn)
        hi = max(xs[j] + base[j][2] for j in drawn)
        xs = [c + CENTRE_X - (lo + hi) / 2 for c in xs]
        reach = [(xs[j], b[j]) for b in (base, popped) for j in drawn]
        left = math.floor(min(c + b[0] for c, b in reach))
        right = math.ceil(max(c + b[2] for c, b in reach))
        if right - left <= MAX_W or size <= MIN_SIZE:
            if right - left <= MAX_W:
                shift = max(0, CENTRE_X - MAX_W // 2 - left) + min(0, CENTRE_X + MAX_W // 2 - right)
                xs, left, right = [c + shift for c in xs], left + shift, right + shift
            up = math.ceil(max(-b[1] for _, b in reach))
            down = math.ceil(max(b[3] for _, b in reach))
            return Line(name, size, stroke, texts, tuple(xs), left, right, up, down)
        size = max(MIN_SIZE, min(size - 1, math.floor(size * MAX_W / (right - left))))


def _popped(name: str, size: int, stroke: int) -> tuple[ImageFont.FreeTypeFont, int]:
    return font(name, round(size * POP)), round(stroke * POP)


def _reach(
    f: ImageFont.FreeTypeFont, stroke: int, texts: tuple[str, ...], box: bool
) -> list[tuple[float, float, float, float]]:
    """How far each word's ink (and box, for a boxed preset) reaches from its centre: left, up, right and
    down."""
    asc, desc = f.getmetrics()
    out = []
    for t in texts:
        if not t:
            out.append((0.0, 0.0, 0.0, 0.0))
            continue
        x0, y0, x1, y1 = f.getbbox(t, anchor="mm", stroke_width=stroke)
        if box:
            half_w, half_h = f.getlength(t) / 2 + PILL_X * f.size, (asc + desc) / 2
            x0, y0, x1, y1 = min(x0, -half_w), min(y0, -half_h), max(x1, half_w), max(y1, half_h)
        out.append((x0, y0, x1, y1))
    return out


def caption_box(page: CaptionPage, style: Style) -> Box:
    line = line_for(shown_words(page, style.preset), style.preset, caption_font(style))
    return (line.left, page.y - line.up, line.width, line.up + line.down)


def caption_centre_y(
    layout: str,
    faces_px: Sequence[tuple[int, int, int, int]],
    seam_y: int | None,
    *,
    height: int = CAPTION_H,
    width: int = MAX_W,
) -> int:
    """Where the line's centre goes: y 1150 for a full-frame layout, the seam for a split one, moved down
    until the line clears every face (each grown by FACE_GAP), but never so far that the line passes y 1250.
    When no place there clears them, the line keeps off their eyes: the place there that covers none of the
    eyes and the least of the faces, else the nearest place above that covers none of the eyes, its top no
    higher than y 640. When no place keeps off the eyes, the one that covers the least of them."""
    half = height / 2
    lo, hi = math.ceil(SAFE_TOP + half), math.floor(SAFE_BOTTOM - half)
    base = seam_y if layout in SPLIT and seam_y is not None else CENTRE_Y
    first = min(max(base, lo), hi)
    x0, x1 = CENTRE_X - width / 2, CENTRE_X + width / 2
    eyes = [(x, y + EYES[0] * h, w, (EYES[1] - EYES[0]) * h) for x, y, w, h in faces_px]

    def covered(y: int, boxes: Sequence[Sequence[float]]) -> float:
        return sum(_overlap((x0, y - half, x1, y + half), box) for box in boxes)

    down = range(first, hi + 1)
    up = range(first - 1, max(lo, math.ceil(UP_TOP + half)) - 1, -1)
    clear = next((y for y in down if covered(y, faces_px) <= 0), None)
    if clear is not None:
        return clear
    off_eyes = [y for y in down if covered(y, eyes) <= 0]
    if off_eyes:
        return _least(off_eyes, lambda y: covered(y, faces_px))
    above = next((y for y in up if covered(y, eyes) <= 0), None)
    if above is not None:
        return above
    return _least([*down, *up], lambda y: covered(y, eyes))


def _least(ys: Sequence[int], cost: Callable[[int], float]) -> int:
    """The first of ``ys`` that costs the least."""
    best, least = ys[0], math.inf
    for y in ys:
        c = cost(y)
        if c < least - EPS:
            best, least = y, c
    return best


def _overlap(band: tuple[float, float, float, float], face: Sequence[float]) -> float:
    x, y, w, h = face
    ow = min(band[2], x + w + FACE_GAP) - max(band[0], x - FACE_GAP)
    oh = min(band[3], y + h + FACE_GAP) - max(band[1], y - FACE_GAP)
    return max(0.0, ow) * max(0.0, oh)


def place_page(
    page: CaptionPage,
    style: Style,
    layout: str,
    faces_px: Sequence[tuple[int, int, int, int]],
    seam_y: int | None,
) -> None:
    """Sets the page's centre and box for the layout and the faces seen while it shows."""
    line = line_for(shown_words(page, style.preset), style.preset, caption_font(style))
    page.y = caption_centre_y(layout, faces_px, seam_y, height=line.height, width=line.width)
    page.box = caption_box(page, style)


def caption_sprite(page: CaptionPage, t: float, style: Style, *, emphasis_ok: bool = True) -> Sprite:
    """The page at output time ``t``. The word spoken last is active: in the accent, growing to 1.08 times
    its size over its first 80 ms. With ``emphasis_ok`` the page's emphasis word is in the accent too."""
    texts = shown_words(page, style.preset)
    active = max(0, bisect.bisect_right([w.start for w in page.words], t + EPS) - 1)
    grow = min(1.0, max(0.0, (t - page.words[active].start) / POP_S)) if page.words else 0.0
    emphasis = page.emphasis if emphasis_ok else None
    scale = round(1 + (POP - 1) * grow, 3)
    return _draw(texts, style.preset, caption_font(style), style.accent, active, emphasis, scale, page.y)


@lru_cache(maxsize=128)
def _draw(
    texts: tuple[str, ...],
    preset: str,
    face: str,
    accent: str,
    active: int,
    emphasis: int | None,
    scale: float,
    y: int,
) -> Sprite:
    p = PRESETS[preset]
    line = line_for(texts, preset, face)
    ox, oy = line.left - PAD, y - line.up - PAD
    size = (line.width + 2 * PAD, line.up + line.down + 2 * PAD)
    back, text = Image.new("RGBA", size), Image.new("RGBA", size)
    glow = Image.new("RGBA", size) if p.glow else None
    for j, word in enumerate(line.texts):
        if not word:
            continue
        f, stroke = (
            _popped(line.font, line.size, line.stroke)
            if j == active
            else (font(line.font, line.size), line.stroke)
        )
        if j == active and scale != round(POP, 3):
            f, stroke = font(line.font, max(1, round(line.size * scale))), round(line.stroke * scale)
        at = (line.xs[j] - ox, y - oy)
        colour = accent if j in (active, emphasis) else p.fill
        if p.box and j == active:
            asc, desc = f.getmetrics()
            half_w, half_h = f.getlength(word) / 2 + PILL_X * f.size, (asc + desc) / 2
            edges = (at[0] - half_w, at[1] - half_h, at[0] + half_w, at[1] + half_h)
            ImageDraw.Draw(back).rounded_rectangle(edges, radius=round(half_h * 0.45), fill=accent)
            colour, stroke = ink_on(accent), 0
        if glow is not None and j == active:
            ImageDraw.Draw(glow).text(
                at, word, font=f, fill=accent, anchor="mm", stroke_width=stroke + 4, stroke_fill=accent
            )
        ImageDraw.Draw(text).text(
            at, word, font=f, fill=colour, anchor="mm", stroke_width=stroke, stroke_fill=STROKE
        )
    shape = Image.alpha_composite(back, text)
    drop = Image.new("L", size, 0)
    drop.paste(shape.getchannel("A").point(lambda v: round(v * SHADOW_ALPHA)), (0, SHADOW_DY))
    black = Image.new("L", size, 0)
    out = Image.merge("RGBA", (black, black, black, drop.filter(ImageFilter.GaussianBlur(SHADOW_BLUR))))
    if glow is not None:
        out = Image.alpha_composite(out, glow.filter(ImageFilter.GaussianBlur(GLOW_BLUR)))
    out = Image.alpha_composite(out, shape)
    bbox = out.getbbox()
    if bbox is None:
        return Sprite(np.zeros((0, 0, 4), np.uint8), ox, oy)
    rgba = np.array(out.crop(bbox))
    rgba.flags.writeable = False
    return Sprite(rgba, ox + bbox[0], oy + bbox[1])
