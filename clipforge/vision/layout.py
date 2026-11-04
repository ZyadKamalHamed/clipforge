"""Layout choice (SPEC 7.7), with one plain sentence saying why. Each shot takes the layout that suits it;
gameplay, and a layout you choose, hold for the whole clip."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from clipforge.models import Shot, Track

MIN_STACK_S = 2.5
MIN_SHOT_S = 1.0  # a shorter shot keeps the layout before it
SLOW_WPS = 2.6
MIN_VOICE_S = 20.0
EPS = 1e-6
NO_GAMEPLAY = "No gameplay video in the library yet, so the speaker fills the frame"
NAMES = {
    "face": "speaker",
    "gameplay": "speaker over gameplay",
    "stacked": "stacked",
    "stream": "facecam over game",
    "fit": "whole frame",
    "motion": "follow the action",
}
REASONS = {
    "stream": "A facecam over a game: facecam on top, the game below",
    "fit": "No faces and a still picture, like slides or a screen: shown whole",
    "motion": "No faces and plenty of movement: the crop follows the action",
    "stacked": "Two people in a wide shot, both facing camera: stacked",
}


@dataclass(frozen=True)
class LayoutChoice:
    kind: str
    reason: str
    per_shot: list[str]


def shot_layouts(shots: list[Shot], kind: str, lengths: list[float] | None = None) -> list[str]:
    """Each shot's layout. A face shot shows its speaker and a two-shot of 2.5 s or more is stacked; a facecam
    shot is split, and a shot without a face is shown whole when still and followed when moving, or keeps the
    split in a clip that is mostly facecam. A shot under a second keeps the layout before it (the first ones,
    the layout after them). Gameplay holds for the whole clip. ``lengths`` are how long each shot runs in the
    clip, by default its whole length."""
    if kind == "gameplay":
        return [kind] * len(shots)
    lengths = [s.end_s - s.start_s for s in shots] if lengths is None else lengths
    total = sum(lengths)
    cam = sum(n for s, n in zip(shots, lengths, strict=True) if s.kind == "stream")
    split = total > EPS and cam / total >= 0.5
    out = [_layout(s, n, split) for s, n in zip(shots, lengths, strict=True)]
    long = [n >= MIN_SHOT_S - EPS for n in lengths]
    held = next((out[i] for i, ok in enumerate(long) if ok), None)
    for i, ok in enumerate(long):
        if ok:
            held = out[i]
        elif held is not None:
            out[i] = held
    return out


def _layout(s: Shot, length: float, split: bool) -> str:
    if s.kind == "two_shot" and length >= MIN_STACK_S - EPS:
        return "stacked"
    if s.kind in ("single", "two_shot", "group"):
        return "face"
    if split or s.kind == "stream":
        return "stream"
    return "fit" if s.kind == "screen" else "motion"


def choose_layout(
    shots: list[Shot],
    tracks: list[Track],
    *,
    words_per_s: float,
    subdued_share: float,
    gameplay_mode: str,
    has_gameplay: bool,
    forced: str | None = None,
    voice_s: float | None = None,
) -> LayoutChoice:
    """The layout covering most of the clip, its reason naming the other shots' layouts. ``voice_s`` is how
    long one voice runs; it defaults to the single-speaker shots' total."""
    n = len(shots)
    if forced:
        if forced == "gameplay" and not has_gameplay:
            return LayoutChoice("face", NO_GAMEPLAY, ["face"] * n)
        why = f"You chose the {NAMES.get(forced, forced)} layout for this clip"
        return LayoutChoice(forced, why, [forced] * n)
    span: dict[str, float] = defaultdict(float)
    for s in shots:
        span[s.kind] += s.end_s - s.start_s
    total = sum(span.values())
    if total <= EPS:
        return LayoutChoice("fit", "Nothing to read in this clip, so the whole frame is shown", ["fit"] * n)
    per_shot = shot_layouts(shots, "face")
    if span["single"] / total >= 0.5:
        voice = span["single"] if voice_s is None else voice_s
        talky = subdued_share >= 0.5 and words_per_s < SLOW_WPS and voice >= MIN_VOICE_S - EPS
        if gameplay_mode == "always" or (gameplay_mode == "auto" and talky):
            if not has_gameplay:
                return LayoutChoice("face", NO_GAMEPLAY, per_shot)
            why = "One person talking calmly for a while: speaker on top, gameplay below"
            return LayoutChoice("gameplay", why, ["gameplay"] * n)
    cover: dict[str, float] = defaultdict(float)
    for s, k in zip(shots, per_shot, strict=True):
        cover[k] += s.end_s - s.start_s
    kind = max(cover, key=cover.__getitem__)
    why = REASONS.get(kind) or _face_reason(span, total)
    others = [NAMES[k] for k in dict.fromkeys(per_shot) if k != kind]
    if others:
        why += "; other shots: " + ", ".join(others)
    return LayoutChoice(kind, why, per_shot)


def _face_reason(span: dict[str, float], total: float) -> str:
    if span["single"] / total >= 0.5:
        return "One speaker: the speaker fills the frame"
    if span["group"] > 0 and span["group"] >= span["two_shot"]:
        return "Three or more people, so the camera cuts to whoever is talking"
    return "The camera follows whoever is talking"
