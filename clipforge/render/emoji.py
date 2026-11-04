"""Emoji (SPEC 8.5): Noto Color Emoji pictures (Apache 2.0), fetched from a pinned commit the first time each
is used and kept, then drawn 120 px wide above the caption with a 120 ms pop."""

from __future__ import annotations

import io
import struct
import unicodedata
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from PIL import Image

from clipforge.assets import Fetch, fetch_bytes, write_bytes
from clipforge.paths import Paths
from clipforge.render.sprites import Sprite

NOTO = (
    "https://raw.githubusercontent.com/googlefonts/noto-emoji/e20cbc2bbec1926686be9f9bee7d1d2cfa1fea0e/"
    "2D/png/128/{name}.png"
)
MAX_BYTES = 64 * 1024
PNG_SIZE = 128
PNG_SIGNATURE = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
EMOJI_PX, EMOJI_MIN_PX, EMOJI_STEP = 120, 64, 8  # its size, and how far it may shrink to clear a face
POP_S = 0.12
POP_FROM = 0.6  # the share of its size it pops in from
EMOJI_S = 1.2  # how long each stays up
SPACING_S = 6.0  # at most one emoji in any 6 s
VS16, ZWJ, KEYCAP = 0xFE0F, 0x200D, 0x20E3
KEYCAP_BASES = set("#*0123456789")
TAGS = (0xE0020, 0xE007F)  # the tag letters some flags spell out
SKIN = (0x1F3FB, 0x1F3FF)
MAX_POINTS = 16


def is_emoji(text: str) -> bool:
    """Whether ``text`` looks like one emoji: symbols with the joiners, variation selectors, skin tones and
    keycap marks they come with, and no letters, numbers or spaces (a keycap's digit aside)."""
    if not text or len(text) > MAX_POINTS:
        return False
    keycap = chr(KEYCAP) in text
    for ch in text:
        cp, cat = ord(ch), unicodedata.category(ch)
        if keycap and (ch in KEYCAP_BASES or cp == KEYCAP):
            continue
        if cp in (VS16, ZWJ) or TAGS[0] <= cp <= TAGS[1] or SKIN[0] <= cp <= SKIN[1]:
            continue
        if cp < 0x80 or cat[0] in "LNZC" or cat in ("Mn", "Mc"):
            return False
    return True


def emoji_name(emoji: str) -> str:
    """The Noto file name for ``emoji``: its code points in lower case hex, at least four digits each, joined
    by underscores, without the emoji variation selector."""
    return "emoji_u" + "_".join(f"{ord(ch):04x}" for ch in emoji if ord(ch) != VS16)


def emoji_file(paths: Paths, emoji: str, *, fetch: Fetch | None = None) -> Path | None:
    """The PNG for ``emoji``, fetched from the pinned Noto commit on first use and kept in ``paths.emoji``.
    None when ``emoji`` is no emoji, or the fetch fails or brings back anything but a 128 x 128 PNG, so the
    clip leaves that emoji out."""
    if not is_emoji(emoji):
        return None
    name = emoji_name(emoji)
    dest = paths.emoji / f"{name}.png"
    if dest.is_file() and good_png(dest.read_bytes()):
        return dest
    get = fetch or (lambda url, max_bytes: fetch_bytes(url, max_bytes=max_bytes))
    try:
        data = get(NOTO.format(name=name), MAX_BYTES)
    except (httpx.HTTPError, OSError, ValueError):
        return None
    if not good_png(data):
        return None
    write_bytes(dest, data)
    return dest


def good_png(data: bytes) -> bool:
    """Whether ``data`` is a 128 x 128 PNG that decodes."""
    if len(data) < 24 or data[:8] != PNG_SIGNATURE or data[12:16] != b"IHDR":
        return False
    if struct.unpack(">II", data[16:24]) != (PNG_SIZE, PNG_SIZE):
        return False
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        return False
    return True


def fetched(
    paths: Paths, entries: Iterable[dict[str, Any]], *, fetch: Fetch | None = None
) -> list[dict[str, Any]]:
    """The EDL's emoji whose PNG is to hand, each with its ``png`` path; the rest are left out."""
    out = []
    for e in entries:
        png = emoji_file(paths, e["emoji"], fetch=fetch)
        if png is not None:
            out.append({**e, "png": str(png)})
    return out


@lru_cache(maxsize=64)
def _picture(png: str) -> Image.Image:
    with Image.open(png) as img:
        return img.convert("RGBA")


def emoji_sprite(
    png: Path | str, size: int = EMOJI_PX, scale: float = 1.0, *, centre: tuple[float, float] | None = None
) -> Sprite:
    """The emoji in ``png``, ``size`` px wide at ``scale`` of that (its pop), centred on ``centre``."""
    s = max(1, round(size * scale))
    rgba = np.array(_picture(str(png)).resize((s, s), Image.Resampling.LANCZOS))
    rgba.flags.writeable = False
    cx, cy = centre if centre is not None else (s / 2, s / 2)
    return Sprite(rgba, round(cx - s / 2), round(cy - s / 2))


def pop_scale(dt: float) -> float:
    """The emoji's size ``dt`` s after it shows, as a share of its full size: from POP_FROM, a little past
    full and back, over POP_S."""
    if dt >= POP_S:
        return 1.0
    u = max(0.0, dt / POP_S) - 1.0
    c = 1.70158  # an ease out with a slight overshoot
    return POP_FROM + (1.0 - POP_FROM) * (1.0 + (c + 1.0) * u**3 + c * u**2)
