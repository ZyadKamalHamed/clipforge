"""Overlay sprites: an RGBA image and where its top left corner sits on the canvas, blended into a BGR frame
in place; and the canvas's safe zone (SPEC 8.1) the text on it keeps to."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

Box = tuple[int, int, int, int]  # x, y, w, h in canvas pixels
SAFE_LEFT, SAFE_TOP, SAFE_RIGHT, SAFE_BOTTOM = 90, 270, 940, 1250  # text and faces stay inside
SAFE_W = SAFE_RIGHT - SAFE_LEFT  # 850
CENTRE_X = 540
FACE_GAP = 10  # the room text leaves around a face
SUPERSAMPLE = 4


@dataclass(frozen=True)
class Sprite:
    rgba: np.ndarray  # h x w x 4 uint8, straight alpha
    x: int
    y: int

    @property
    def w(self) -> int:
        return int(self.rgba.shape[1])

    @property
    def h(self) -> int:
        return int(self.rgba.shape[0])

    @property
    def box(self) -> Box:
        return (self.x, self.y, self.w, self.h)


def blend(canvas: np.ndarray, sprite: Sprite, alpha: float = 1.0) -> None:
    """Lays ``sprite`` over ``canvas`` (h x w x 3 uint8, BGR) at ``alpha`` times its own opacity, touching
    only the part of its box that lies on the canvas."""
    alpha = min(1.0, max(0.0, alpha))
    x0, y0 = max(sprite.x, 0), max(sprite.y, 0)
    x1 = min(sprite.x + sprite.w, canvas.shape[1])
    y1 = min(sprite.y + sprite.h, canvas.shape[0])
    if x0 >= x1 or y0 >= y1 or alpha <= 0:
        return
    src = sprite.rgba[y0 - sprite.y : y1 - sprite.y, x0 - sprite.x : x1 - sprite.x]
    a = src[..., 3:4].astype(np.float32) * (alpha / 255.0)
    roi = canvas[y0:y1, x0:x1]
    roi[...] = (src[..., 2::-1] * a + roi * (1.0 - a) + 0.5).astype(np.uint8)


def centred_x(w: int) -> int:
    """The left edge of a box ``w`` px wide centred on the canvas, moved left as far as it must be to end by
    the safe zone's right edge."""
    return max(SAFE_LEFT, min(CENTRE_X - w // 2, SAFE_RIGHT - w))


def ink_on(colour: str) -> str:
    """Black or white, whichever reads better on ``colour``."""
    r, g, b = (int(colour[k : k + 2], 16) for k in (1, 3, 5))
    return "#000000" if 0.299 * r + 0.587 * g + 0.114 * b > 150 else "#FFFFFF"


def rounded(w: int, h: int, radius: float, colour: str, opacity: float = 1.0) -> Image.Image:
    """A ``w`` x ``h`` RGBA box in ``colour`` with round corners, drawn larger and shrunk so its edges are
    smooth."""
    k = SUPERSAMPLE
    mask = Image.new("L", (w * k, h * k), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w * k - 1, h * k - 1), radius=radius * k, fill=255)
    mask = mask.resize((w, h), Image.Resampling.BOX)
    if opacity < 1.0:
        mask = mask.point(lambda v: round(v * opacity))
    box = Image.new("RGBA", (w, h), colour)
    box.putalpha(mask)
    return box
