"""Where each layout's panels sit on the 1080 x 1920 canvas and which part of which frame fills each
(SPEC 7.7), and the scaling and blur that fill them."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np

from clipforge.render.edl import GAMEPLAY_BOX, H_OUT, PANELS, W_OUT, Box, even, fit_box
from clipforge.vision.camera import crop_size

BLUR_ZOOM = 1.1  # the fit layout's background is the middle of the frame zoomed this much more than filling,
BLUR_DIM = 0.6  # at this brightness,
BLUR_SIGMA = 48  # blurred this much, in canvas pixels,
BLUR_SHRINK = 8  # at an eighth of its size, which looks the same and costs a sixty-fourth
CUBIC_FROM = 0.8  # shrinking to this or more, cubic measured sharper than area averaging and costs half


@dataclass(frozen=True)
class Panel:
    box: Box  # where it sits on the canvas
    crop: Box  # the part of its frame that fills it
    source: Literal["main", "gameplay"] = "main"
    blurred: bool = False


def panels_for(
    kind: str, src_w: int, src_h: int, crops: Sequence[Box] = (), *, gameplay: tuple[int, int] | None = None
) -> list[Panel]:
    """The panels of a ``kind`` frame, bottom first: one for each of ``crops`` (one crop of the source frame
    a panel, in the layout's order), the gameplay video's middle under the speaker when ``gameplay`` gives
    its frame size, or for "fit" the whole frame across the canvas over a blurred, darkened copy of itself
    (left out when the frame fills the canvas anyway)."""
    if kind == "fit":
        box, whole = fit_box(src_w, src_h), (0, 0, src_w, src_h)
        if box == (0, 0, W_OUT, H_OUT):
            return [Panel(box, whole)]
        cw, ch = crop_size(src_w, src_h, W_OUT, H_OUT)
        bw, bh = even(cw / BLUR_ZOOM), even(ch / BLUR_ZOOM)
        back = Panel((0, 0, W_OUT, H_OUT), ((src_w - bw) // 2, (src_h - bh) // 2, bw, bh), blurred=True)
        return [back, Panel(box, whole)]
    out = [Panel(box, tuple(crop)) for box, crop in zip(PANELS[kind], crops, strict=True)]
    if kind == "gameplay" and gameplay is not None:
        gw, gh = gameplay
        cw, ch = crop_size(gw, gh, GAMEPLAY_BOX[2], GAMEPLAY_BOX[3])
        out.append(Panel(GAMEPLAY_BOX, ((gw - cw) // 2, (gh - ch) // 2, cw, ch), source="gameplay"))
    return out


def cover_size(src_w: int, src_h: int, w: int, h: int) -> tuple[int, int]:
    """The smallest even size of the source's shape that covers a ``w`` x ``h`` box."""
    s = max(w / src_w, h / src_h)
    return max(w, 2 * math.ceil(src_w * s / 2)), max(h, 2 * math.ceil(src_h * s / 2))


def inside(crop: Box, w: int, h: int) -> Box:
    """``crop`` moved, and shrunk if it must be, to lie inside a ``w`` x ``h`` frame."""
    cw, ch = min(crop[2], w), min(crop[3], h)
    return min(max(crop[0], 0), w - cw), min(max(crop[1], 0), h - ch), cw, ch


def scaled(part: np.ndarray, w: int, h: int) -> np.ndarray:
    """``part`` at ``w`` x ``h``: Lanczos to enlarge, cubic to shrink a little (to CUBIC_FROM or more) and
    area averaging to shrink more."""
    ph, pw = part.shape[:2]
    if (pw, ph) == (w, h):
        return part
    scale = math.sqrt(w * h / (pw * ph))
    kernel = cv2.INTER_LANCZOS4 if scale > 1 else cv2.INTER_CUBIC if scale >= CUBIC_FROM else cv2.INTER_AREA
    return cv2.resize(part, (w, h), interpolation=kernel)


def blurred(part: np.ndarray, w: int, h: int) -> np.ndarray:
    """``part`` at ``w`` x ``h``, blurred by BLUR_SIGMA and darkened to BLUR_DIM."""
    sw, sh = max(1, w // BLUR_SHRINK), max(1, h // BLUR_SHRINK)
    small = cv2.resize(part, (sw, sh), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), BLUR_SIGMA / BLUR_SHRINK)
    return cv2.resize(cv2.convertScaleAbs(small, alpha=BLUR_DIM), (w, h), interpolation=cv2.INTER_LINEAR)
