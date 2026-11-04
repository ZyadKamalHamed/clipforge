"""Text with Pillow (SPEC 8.2): the bundled fonts, a fallback for letters a font lacks, the scripts this
Pillow cannot draw because it has no shaping library (SPEC 1, non-goals), and the line layout the hook, the
credit line and fact cards share."""

from __future__ import annotations

import bisect
import math
import struct
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache, lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from clipforge.assets import font_path

FALLBACK = "fallback"  # the first of FALLBACK_FILES that exists
FALLBACK_FILES = (
    Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
    Path("/Library/Fonts/Arial Unicode.ttf"),
)
# Blocks whose letters join, reorder or stack, which Pillow's basic layout cannot do
SHAPED = (
    (0x0900, 0x0DFF),  # Devanagari to Sinhala
    (0x0E00, 0x0EFF),  # Thai, Lao
    (0x0F00, 0x109F),  # Tibetan, Myanmar
    (0x1100, 0x11FF),  # Hangul jamo left uncombined
    (0x1700, 0x18AF),  # Philippine scripts, Khmer, Mongolian
    (0x1900, 0x1AAF),  # Limbu to Tai Tham
    (0x1B00, 0x1C4F),  # Balinese to Lepcha
    (0x1CC0, 0x1CFF),  # Sundanese supplement, Vedic extensions
    (0xA800, 0xAAFF),  # Syloti Nagri to the Meetei Mayek extensions
    (0xABC0, 0xABFF),  # Meetei Mayek
    (0xD7B0, 0xD7FF),  # Hangul jamo extended B
    (0x11000, 0x11FFF),  # Brahmi and the other historic Indic scripts
)
RTL = {"R", "AL", "RLE", "RLO", "RLI"}
UNDRAWN = {"Cc", "Cf", "Zs", "Zl", "Zp"}  # categories no font has to draw


def fallback_file() -> Path | None:
    return next((p for p in FALLBACK_FILES if p.exists()), None)


def font_file(name: str) -> Path:
    if name != FALLBACK:
        return font_path(name)
    path = fallback_file()
    if path is None:
        raise FileNotFoundError("Arial Unicode is not on this Mac")
    return path


@lru_cache(maxsize=256)
def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    """A bundled font (a key of ``assets.FONTS``) or FALLBACK at ``size`` px, laid out without shaping."""
    return ImageFont.truetype(str(font_file(name)), size, layout_engine=ImageFont.Layout.BASIC)


def needs_shaping(text: str) -> bool:
    """Whether ``text`` has letters that only come out right with a shaping library: right-to-left scripts
    and scripts whose letters join, reorder or stack."""
    for ch in unicodedata.normalize("NFC", text):
        if unicodedata.bidirectional(ch) in RTL or unicodedata.category(ch) == "Mc":
            return True
        if any(a <= ord(ch) <= b for a, b in SHAPED):
            return True
    return False


def missing_letters(name: str, text: str) -> bool:
    """Whether the font ``name`` lacks any letter of ``text``."""
    path = str(font_file(name))
    return any(not _invisible(ch) and not _covers(path, ord(ch)) for ch in unicodedata.normalize("NFC", text))


def text_font(name: str, text: str) -> str:
    """``name``, or FALLBACK when the fallback font has a letter of ``text`` that ``name`` lacks."""
    other = fallback_file()
    if other is None:
        return name
    mine = str(font_file(name))
    for ch in unicodedata.normalize("NFC", text):
        if not _invisible(ch) and not _covers(mine, ord(ch)) and _covers(str(other), ord(ch)):
            return FALLBACK
    return name


def keep_drawable(name: str, text: str) -> str:
    """``text`` without what the font ``name`` cannot draw, so nothing shows as an empty box; any space
    becomes a plain one."""
    path = str(font_file(name))
    out = []
    for ch in unicodedata.normalize("NFC", text):
        if ch.isspace():
            out.append(" ")
        elif not _invisible(ch) and _covers(path, ord(ch)):
            out.append(ch)
    return "".join(out)


def _invisible(ch: str) -> bool:
    cp = ord(ch)
    return unicodedata.category(ch) in UNDRAWN or 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF


def _covers(path: str, cp: int) -> bool:
    starts, ends = _cmap(path)
    k = bisect.bisect_right(starts, cp) - 1
    return k >= 0 and cp <= ends[k]


@cache
def _cmap(path: str) -> tuple[list[int], list[int]]:
    """The code points the font maps to a real glyph, as sorted inclusive ranges (starts, ends), read from
    its cmap table: the full Unicode subtable (format 12) when there is one, else the BMP one (format 4)."""
    data = Path(path).read_bytes()
    base = struct.unpack_from(">I", data, 12)[0] if data[:4] == b"ttcf" else 0
    (count,) = struct.unpack_from(">H", data, base + 4)
    tables = {}
    for k in range(count):
        tag, _, offset, _ = struct.unpack_from(">4sIII", data, base + 12 + 16 * k)
        tables[tag] = offset
    cmap = tables[b"cmap"]
    (n,) = struct.unpack_from(">H", data, cmap + 2)
    subtables = {}
    for k in range(n):
        platform, encoding, offset = struct.unpack_from(">HHI", data, cmap + 4 + 8 * k)
        subtables[(platform, encoding)] = cmap + offset
    found: list[tuple[int, int]] = []
    for key in ((3, 10), (0, 6), (0, 4), (3, 1), (0, 3), (0, 2), (0, 1), (0, 0)):
        at = subtables.get(key)
        fmt = struct.unpack_from(">H", data, at)[0] if at is not None else None
        if fmt in (4, 12):
            found = _format12(data, at) if fmt == 12 else _format4(data, at)
            break
    merged: list[tuple[int, int]] = []
    for a, b in sorted(found):
        if merged and a <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return [a for a, _ in merged], [b for _, b in merged]


def _format12(data: bytes, at: int) -> list[tuple[int, int]]:
    (groups,) = struct.unpack_from(">I", data, at + 12)
    out = []
    for k in range(groups):
        a, b, glyph = struct.unpack_from(">III", data, at + 16 + 12 * k)
        a += glyph == 0  # the group's first code point maps to the missing glyph
        if a <= b:
            out.append((a, b))
    return out


def _format4(data: bytes, at: int) -> list[tuple[int, int]]:
    (seg2,) = struct.unpack_from(">H", data, at + 6)
    seg = seg2 // 2
    ends = struct.unpack_from(f">{seg}H", data, at + 14)
    starts = struct.unpack_from(f">{seg}H", data, at + 16 + seg2)
    deltas = struct.unpack_from(f">{seg}H", data, at + 16 + 2 * seg2)
    range_at = at + 16 + 3 * seg2
    offsets = struct.unpack_from(f">{seg}H", data, range_at)
    out: list[tuple[int, int]] = []
    for k in range(seg):
        a, b, delta, offset = starts[k], ends[k], deltas[k], offsets[k]
        if offset == 0:  # glyph = code + delta, so one code point at most maps to glyph 0
            zero = -delta & 0xFFFF
            out += [r for r in ((a, min(b, zero - 1)), (max(a, zero + 1), b)) if r[0] <= r[1]]
            continue
        for c in range(a, b + 1):
            (g,) = struct.unpack_from(">H", data, range_at + 2 * k + offset + 2 * (c - a))
            if g and (g + delta) & 0xFFFF:
                out.append((c, c))
    return out


# Laying out the hook, the credit line and fact cards

LINE = 1.15  # the line pitch, in font sizes
ELLIPSIS = chr(0x2026)


@dataclass(frozen=True)
class TextLine:
    words: tuple[int, ...]  # indices into the words
    xs: tuple[float, ...]  # each word's origin on the baseline, from the line's start
    left: float  # how far the line's ink (with the accent word's box) reaches, from its start
    right: float
    top: float  # about the baseline, so negative
    bottom: float

    @property
    def width(self) -> float:
        return self.right - self.left


def text_line(
    f: ImageFont.FreeTypeFont,
    words: Sequence[str],
    idx: Sequence[int],
    *,
    accent: int | None = None,
    pad: float = 0.0,
    span: tuple[float, float] = (0.0, 0.0),
) -> TextLine:
    """The words at ``idx`` a space apart on one line; the word at ``accent`` gets ``pad`` px of room on
    either side for its box, which reaches from ``span[0]`` to ``span[1]`` about the baseline."""
    space = f.getlength(" ")
    xs, x = [], 0.0
    left = top = math.inf
    right = bottom = -math.inf
    for n, k in enumerate(idx):
        if n:
            x += space + (pad if accent in (k, idx[n - 1]) else 0.0)
        xs.append(x)
        a, t, b, d = f.getbbox(words[k], anchor="ls")
        if k == accent:
            a, b, t, d = a - pad, b + pad, min(t, span[0]), max(d, span[1])
        left, right, top, bottom = min(left, x + a), max(right, x + b), min(top, t), max(bottom, d)
        x += f.getlength(words[k])
    return TextLine(tuple(idx), tuple(xs), left, right, top, bottom)


def wrap(f: ImageFont.FreeTypeFont, words: Sequence[str], room: float, **kw) -> list[TextLine]:
    """``words`` on one line when it is at most ``room`` px wide, else on the two lines as even as they can
    be, which may still be wider."""
    one = text_line(f, words, range(len(words)), **kw)
    if one.width <= room or len(words) < 2:
        return [one]
    pairs = (
        [text_line(f, words, range(k), **kw), text_line(f, words, range(k, len(words)), **kw)]
        for k in range(1, len(words))
    )
    return min(pairs, key=lambda pair: max(ln.width for ln in pair))


def shorter(words: Sequence[str]) -> list[str]:
    """``words`` a step shorter, ending in an ellipsis: the last word left out, or the only word a letter
    shorter; empty when nothing is left to cut."""
    if len(words) > 1:
        return [*words[:-2], words[-2].rstrip(ELLIPSIS) + ELLIPSIS]
    word = words[0].rstrip(ELLIPSIS) if words else ""
    return [word[:-1] + ELLIPSIS] if len(word) > 1 else []


@dataclass(frozen=True)
class Fit:
    """Words laid out on one or two lines in one font and size."""

    f: ImageFont.FreeTypeFont
    size: int
    words: tuple[str, ...]
    lines: tuple[TextLine, ...]
    pitch: int  # from one baseline to the next
    accent: int | None  # the word on the accent box
    pad: float  # the room either side of the accent word
    span: tuple[float, float]  # the accent box's top and bottom about the baseline

    @property
    def top(self) -> float:
        return min(n * self.pitch + ln.top for n, ln in enumerate(self.lines))

    @property
    def bottom(self) -> float:
        return max(n * self.pitch + ln.bottom for n, ln in enumerate(self.lines))

    @property
    def width(self) -> float:
        return max(ln.width for ln in self.lines)


def fit(
    name: str,
    words: Sequence[str],
    size: int,
    min_size: int,
    room: float,
    *,
    max_lines: int = 2,
    max_h: float = math.inf,
    accent: int | None = None,
    accent_pad: tuple[float, float] = (0.0, 0.0),
) -> Fit | None:
    """``words`` in the font ``name`` on at most ``max_lines`` lines (one or two), none wider than ``room``
    px and all of them at most ``max_h`` px tall: at ``size``, else as much smaller as they must be down to
    ``min_size``, then cut a word or a letter at a time, with an ellipsis. ``accent_pad`` is the room for
    the accent word's box, across and up and down, in font sizes. None when nothing is left to draw."""
    words = list(words)
    while words:
        f = font(name, size)
        pad, vpad = accent_pad[0] * size, accent_pad[1] * size
        span = (f.getbbox("H", anchor="ls")[1] - vpad, f.getbbox("g", anchor="ls")[3] + vpad)
        kw = {"accent": accent, "pad": pad, "span": span}
        lines = (
            wrap(f, words, room, **kw) if max_lines > 1 else [text_line(f, words, range(len(words)), **kw)]
        )
        out = Fit(f, size, tuple(words), tuple(lines), round(LINE * size), accent, pad, span)
        wide, tall = out.width, out.bottom - out.top
        if wide <= room and tall <= max_h:
            return out
        if size > min_size:
            size = max(
                min_size, min(size - 1, math.floor(size * min(room / max(wide, 1.0), max_h / max(tall, 1.0))))
            )
            continue
        words = shorter(words)
        accent = accent if accent is not None and accent < len(words) else None
    return None


def text_layer(
    size: tuple[int, int],
    items: Sequence[tuple[float, float, str]],
    f: ImageFont.FreeTypeFont,
    colour: str,
    stroke: int = 0,
) -> Image.Image:
    """``items`` (x, baseline y, text) in ``colour`` on an otherwise clear RGBA image of ``size``, every
    letter grown by ``stroke`` px all round."""
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    for x, y, text in items:
        draw.text((x, y), text, font=f, fill=255, anchor="ls", stroke_width=stroke, stroke_fill=255)
    layer = Image.new("RGBA", size, colour)
    layer.putalpha(mask)
    return layer
