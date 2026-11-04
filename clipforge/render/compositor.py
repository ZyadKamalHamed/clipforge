"""Each output frame (SPEC 7.10 step 2): the layout's panels cut from the source frame (and the gameplay
frame), then the edit list's sprites laid over them, only inside their boxes."""

from __future__ import annotations

import numpy as np

from clipforge.render.captions import caption_sprite
from clipforge.render.cards import card_sprite
from clipforge.render.edl import EDL, EPS, H_OUT, W_OUT, Span
from clipforge.render.emoji import emoji_sprite, pop_scale
from clipforge.render.hook import credit_sprite, hook_alpha, hook_sprite
from clipforge.render.layouts import blurred, inside, panels_for, scaled
from clipforge.render.sprites import Sprite, blend

BAR_Y, BAR_H = 1244, 6  # the progress bar's top and height, along the bottom of the safe zone


def compose(frame: np.ndarray, span: Span, f: int, *, gameplay_frame: np.ndarray | None = None) -> np.ndarray:
    """The 1080 x 1920 BGR canvas for the ``f``-th frame of ``span`` (counted from the span's start), cut
    from ``frame``, the source frame at the size the span's crops were made for. A frame past the end of a
    crop list takes its last crop. The gameplay box stays black without ``gameplay_frame``."""
    h, w = frame.shape[:2]
    crops = [path[min(f, len(path) - 1)] for path in span.crops if path]
    game = gameplay_frame.shape[1::-1] if gameplay_frame is not None else None
    canvas = np.zeros((H_OUT, W_OUT, 3), np.uint8)
    for p in panels_for(span.layout, w, h, crops, gameplay=game):
        src = gameplay_frame if p.source == "gameplay" else frame
        x, y, cw, ch = inside(p.crop, src.shape[1], src.shape[0])
        part = src[y : y + ch, x : x + cw]
        bx, by, bw, bh = p.box
        canvas[by : by + bh, bx : bx + bw] = blurred(part, bw, bh) if p.blurred else scaled(part, bw, bh)
    return canvas


def overlays(edl: EDL, t: float) -> list[tuple[Sprite, float]]:
    """The sprites on the canvas at output time ``t``, bottom first (the progress bar, the caption, the
    emoji, the fact cards, the credit line, the hook), each with its opacity."""
    out: list[tuple[Sprite, float]] = []
    if edl.style.progress_bar and edl.duration > EPS:
        bar = progress_sprite(edl.style.accent, t / edl.duration)
        if bar is not None:
            out.append((bar, 1.0))

    def showing(layer: dict) -> bool:
        return layer["start"] - EPS <= t < layer["end"] - EPS

    for page in edl.pages:
        if page.start - EPS <= t < page.end - EPS:
            out.append((caption_sprite(page, t, edl.style), 1.0))
    for e in edl.emoji:
        if "png" in e and showing(e):
            x, y, s, _ = e["box"]
            pop = pop_scale(t - e["start"])
            out.append((emoji_sprite(e["png"], e["size"], pop, centre=(x + s / 2, y + s / 2)), 1.0))
    for card in edl.cards:
        sprite = card_sprite(card["text"]) if showing(card) else None
        if sprite is not None:
            out.append((Sprite(sprite.rgba, card["box"][0], card["box"][1]), 1.0))
    credit = edl.credit
    if credit and showing(credit):
        sprite = credit_sprite(credit["text"], credit["box"][1])
        if sprite is not None:
            out.append((sprite, hook_alpha(t, credit["end"])))
    hook = edl.hook
    if hook and showing(hook):
        sprite = hook_sprite(hook["title"], hook["accent_word"], edl.style, scale=hook["scale"])
        if sprite is not None:
            out.append((Sprite(sprite.rgba, hook["box"][0], hook["box"][1]), hook_alpha(t, hook["end"])))
    return out


def progress_sprite(colour: str, played: float) -> Sprite | None:
    """The progress bar (SPEC 8.7) in ``colour``, as wide as the share of the clip ``played``; None before
    it has a pixel to show."""
    w = round(W_OUT * min(1.0, max(0.0, played)))
    if w <= 0:
        return None
    rgba = np.empty((BAR_H, w, 4), np.uint8)
    rgba[...] = (*(int(colour[k : k + 2], 16) for k in (1, 3, 5)), 255)
    return Sprite(rgba, 0, BAR_Y)


def paint(canvas: np.ndarray, edl: EDL, t: float) -> None:
    """Lays the sprites showing at output time ``t`` over ``canvas``, in place."""
    lay(canvas, overlays(edl, t))


def lay(canvas: np.ndarray, sprites: list[tuple[Sprite, float]]) -> None:
    """Lays ``sprites`` (from ``overlays``) over ``canvas`` in order, in place."""
    for sprite, alpha in sprites:
        blend(canvas, sprite, alpha)
