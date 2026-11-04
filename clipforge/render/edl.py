"""The edit decision list (SPEC 7.7 to 7.9): the stretches of the source a clip keeps, where each kept word
lands, the layout of every stretch with its panels and a source crop for each output frame, the punch-ins,
the hook and the credit. Every time in it is output time, on the output frame grid."""

from __future__ import annotations

import bisect
import math
import statistics
from typing import Any

import numpy as np
from pydantic import BaseModel

from clipforge.brain.text import plain_words
from clipforge.errors import ClipForgeError
from clipforge.library.sfx import PEAK_S
from clipforge.models import Analysis, ClipRow, Packaging, Shot, Track, Transcript, Word
from clipforge.render.captions import CENTRE_Y, CaptionPage, paginate, place_page
from clipforge.render.cards import CARD_GAP, CARD_MAX_S, CARD_MIN_S, MAX_CARDS, card_sprite
from clipforge.render.emoji import EMOJI_MIN_PX, EMOJI_PX, EMOJI_S, EMOJI_STEP, POP_S, SPACING_S, is_emoji
from clipforge.render.hook import (
    CREDIT_GAP,
    FOREHEAD,
    HAIR,
    HOOK_HOLD_S,
    SCALES,
    credit_sprite,
    hook_sprite,
    near,
    place_hook,
)
from clipforge.render.sprites import FACE_GAP, SAFE_BOTTOM, SAFE_LEFT, SAFE_RIGHT, SAFE_TOP, Sprite
from clipforge.render.styles import PRESETS, Style
from clipforge.render.text import needs_shaping
from clipforge.render.timeline import Segment, Timeline, output_fps
from clipforge.vision.camera import PUNCH_MIN_S, SEEN_S, crop_size, follow, punch_ins, punch_scale
from clipforge.vision.layout import shot_layouts

EDL_VERSION = 1
W_OUT, H_OUT = 1080, 1920
GAP_S = 0.35  # a pause longer than this shrinks
GAP_KEEP_S = 0.12  # to this, half of it kept on either side
PAYOFF_GAP_S = 0.5  # the pause before the clip's last sentence keeps up to this
END_S = 0.25  # the clip ends this long after its last word
FADE_S = 0.15
WHOOSH_GAP_S, POP_GAP_S = 4.0, 6.0  # at most one whoosh in any 4 s, one pop in any 6 s (SPEC 8.5)
SFX_CLEAR_S = 0.5  # and no two effects closer than this
NEVER_CUT = {"like", "you", "know"}
PACE_WPS = 2.3  # Auto pace speeds up speech slower than this
PACE_MIN, PACE_MAX = 1.05, 1.10
FACE_STEP_S = 0.1  # how often the faces on the canvas are written down
STACK_FILL = 0.9  # a stacked panel's crop is this share of the space between the two faces
FACECAM_FILL = 2.5  # a facecam crop is this many face heights tall
COLD_MIN_S, COLD_MAX_S = 2.0, 4.0  # a cold open's teaser runs this long (SPEC 8.3)
COLD_TAIL_S = 0.1  # and keeps this much after its last word
# Little words an emphasis phrase may hold that never get the accent themselves
STOP_WORDS = {
    *("a", "an", "the", "and", "or", "but", "so", "of", "to", "in", "on", "at", "for", "by", "with"),
    *("from", "is", "are", "was", "were", "be", "it", "its", "this", "that", "i", "you", "he", "she"),
    *("we", "they", "my", "your", "s", "t", "d", "m", "re", "ve", "ll"),
}
EPS = 1e-6
NO_GAMEPLAY_ITEM = "No gameplay video was found for this clip, so the speaker fills the frame"
GAMEPLAY_OFF = "Gameplay is off for this clip, so the speaker fills the frame"
GAMEPLAY_GONE = "The gameplay video picked for this clip is gone, so another one is used"
MUSIC_NO_MATCH = "No track in your music library matches this clip's mood, so it has none"
MUSIC_GONE = "The music track picked for this clip is gone, so this version has none"
VO_CUT = "The voiceover runs past the end of the clip, so its end is cut"
VO_GONE = "The voiceover file is gone, so this version has none"
VO_LATE = "The voiceover starts after the clip ends, so this version has none"
EMPTY = "Nothing is left of this clip once its cuts are made, so bring back some of its words."
CARD_GONE = "A fact card's sentence is no longer in the clip, so the card is left out"
SHAPING = (
    "Captions are off for this clip because its words use a script ClipForge cannot draw yet, like Arabic, "
    "Hebrew or Hindi"
)
NO_HOOK = "The hook title is off for this clip because ClipForge cannot draw its letters yet"
NO_CREDIT = "The on-screen credit is off for this clip because ClipForge cannot draw the channel's name yet"
NO_CARD = "A fact card is off for this clip because ClipForge cannot draw its letters yet"
NO_COLD_LINE = "No line was picked for a cold open, so this version has none"
COLD_AT_START = "The cold open line is where the clip starts, so this version has none"
COLD_SHORT = "The cold open line is under 2 seconds, so this version has none"
COLD_STRUCK = "The cold open line's words are cut, so this version has none"

Box = tuple[int, int, int, int]  # x, y, w, h in pixels
PANELS: dict[str, list[Box]] = {
    "face": [(0, 0, W_OUT, H_OUT)],
    "motion": [(0, 0, W_OUT, H_OUT)],
    "stacked": [(0, 0, W_OUT, 960), (0, 960, W_OUT, 960)],
    "gameplay": [(0, 0, W_OUT, 1056)],
    "stream": [(0, 0, W_OUT, 640), (0, 640, W_OUT, 1280)],
}
GAMEPLAY_BOX: Box = (0, 1056, W_OUT, 864)  # the gameplay video, under the speaker
LAYOUTS = (*PANELS, "fit")
ZOOMABLE = ("face", "gameplay")


class EdlWord(BaseModel):
    i: int  # its place in Transcript.words
    sid: str | None = None  # its sentence
    text: str
    start: float
    end: float
    emphasis: bool = False
    teaser: bool = False  # said in the cold open, before the clip's start


class Span(BaseModel):
    """A stretch of one layout: each panel's box on the canvas and its source crop for every output frame
    from ``start``; ``faces`` are (time, x, y, w, h) of the faces it shows, in canvas pixels."""

    start: float
    end: float
    layout: str
    boxes: list[Box] = []
    crops: list[list[Box]] = []
    faces: list[tuple[float, int, int, int, int]] = []


class Zoom(BaseModel):
    start: float
    end: float
    scale: float


class EDL(BaseModel):
    version: int = EDL_VERSION
    clip_id: str
    fps: float
    width: int = W_OUT
    height: int = H_OUT
    src_width: int  # the source frame the crops are cut from
    src_height: int
    duration: float
    segments: list[Segment]
    words: list[EdlWord] = []
    pages: list[CaptionPage] = []
    hook: dict[str, Any] | None = None
    credit: dict[str, Any] | None = None
    cards: list[dict[str, Any]] = []
    emoji: list[dict[str, Any]] = []
    spans: list[Span] = []
    zooms: list[Zoom] = []
    sfx: list[dict[str, Any]] = []
    payoff_s: float | None = None  # where the clip's last sentence starts, when it has more than one
    audio_fade_s: float = FADE_S
    gameplay: dict[str, Any] | None = None  # the library item and segment under the speaker
    music: dict[str, Any] | None = None  # the track under the speech: its item, title, artist, level, credit
    cold_open: dict[str, Any] | None = None  # the teaser's sentence, source range and output end
    voiceover: dict[str, Any] | None = None  # your recording over the clip: its file, start and length
    style: Style
    notes: list[str] = []  # what was left out or changed, in plain words

    def timeline(self) -> Timeline:
        return Timeline(self.segments, self.fps)


def build_edl(
    clip: ClipRow,
    transcript: Transcript,
    analysis: Analysis,
    packaging: Packaging,
    style: Style,
    *,
    src_fps: float,
    src_duration: float,
    credit_name: str | None,
    gameplay: dict[str, Any] | None = None,
) -> EDL:
    """``gameplay`` is the library item and segment for the gameplay layout, None when there is none. The
    clip's struck words are always cut, and its word fixes change what the captions say."""
    edits = clip.edits or {}
    struck = set(edits.get("struck") or ())
    fixes = edits.get("word_fixes") or {}
    words = clip_words(transcript, clip)
    kept = [w for w in words if w.i not in struck and not (style.filler_removal and cuttable(w))]
    if not kept and any(w.i in struck for w in words):
        raise ClipForgeError("empty_clip", EMPTY)
    sids = sentence_ids(transcript, words)
    ranges = cut_ranges(transcript, clip, kept, style, sids, src_duration)
    speed = pace(kept, ranges) if style.auto_pace else 1.0
    notes: list[str] = []
    teaser = None
    if style.cold_open and kept:
        teaser, note = cold_open_range(transcript, kept, packaging.cold_open_sid, sids)
        if note:
            notes.append(note)
    tl = Timeline.from_ranges(ranges, output_fps(src_fps, style.fps_cap_30), speed, teaser=teaser)
    if not tl.frames:
        raise ClipForgeError("empty_clip", EMPTY)
    loud = emphasised(kept, packaging.emphasis)

    def said(w: Word, a: float, b: float, *, early: bool = False) -> EdlWord:
        return EdlWord(
            i=w.i,
            sid=sids.get(w.i),
            text=fixes.get(str(w.i), w.text),
            start=a,
            end=max(a, b),
            emphasis=w.i in loud,
            teaser=early,
        )

    out_words = [said(w, tl.nearest_output(w.start), tl.nearest_output(w.end)) for w in kept]
    cold_open, early_words = None, []
    if tl.teaser_end:
        seg, out_end = tl.segments[0], tl.teaser_end
        cold_open = {
            "sid": packaging.cold_open_sid,
            "src_start": seg.src_start,
            "src_end": seg.src_end,
            "out_end": out_end,
        }

        def early(t: float) -> float:
            return min(out_end, max(0.0, (t - seg.src_start) / speed))

        early_words = [
            said(w, early(w.start), early(w.end), early=True)
            for w in kept
            if seg.src_start - EPS <= w.start and w.end <= seg.src_end + EPS
        ]
    kind = analysis.layout if analysis.layout in LAYOUTS else "face"
    forced = edits.get("layout") == kind
    if kind == "gameplay" and not forced and style.gameplay_mode == "off":
        kind = "face"
        notes.append(GAMEPLAY_OFF)
    elif kind == "gameplay" and gameplay is None:
        kind = "face"
        notes.append(NO_GAMEPLAY_ITEM)
    shots = analysis.shots or [
        Shot(
            start_s=analysis.start_s,
            end_s=analysis.end_s,
            kind="single",
            tracks=[t.id for t in analysis.tracks],
            quality=0.0,
        )
    ]
    framer = Framer(analysis, shots, tl)
    per_shot = [kind] * len(shots) if forced else shot_layouts(shots, kind, framer.lengths())
    first = first_starts(out_words)
    starts = sorted({first[w.sid] for w in out_words if w.emphasis and w.sid in first})
    zooms = []
    if style.punch_ins:
        zooms = punch_ins(
            starts, tl.duration, scale=punch_scale(analysis.height), stops=sorted(first.values())
        )
    spans, zooms = framer.spans(per_shot, zooms)
    pages = []
    if any(needs_shaping(w.text) for w in out_words):
        notes.append(SHAPING)
    else:
        pages = caption_pages(early_words, style, spans, tl.teaser_end) if early_words else []
        pages += caption_pages(out_words, style, spans, tl.duration)
    hold = tl.duration if style.hook_whole_clip else min(tl.duration, HOOK_HOLD_S)
    credit_line = credit_text(style, credit_name)
    hook, credit = hook_layer(
        packaging if style.hook else packaging.model_copy(update={"hook_title": ""}),
        style,
        faces_during(spans, 0.0, hold),
        boxes_during(pages, 0.0, hold),
        tl.duration,
        credit_line,
    )
    if style.hook and packaging.hook_title.strip() and hook is None:
        notes.append(NO_HOOK)
    if credit_line and credit is None:
        notes.append(NO_CREDIT)
    cards = card_layer(packaging, style, out_words, hook, credit, tl.duration, notes) if style.cards else []
    shown = [x for x in (hook, credit, *cards) if x is not None]
    emoji = emoji_layer(packaging, style, out_words, pages, spans, shown, tl.duration)
    payoff = payoff_start(out_words)
    return EDL(
        clip_id=clip.id,
        fps=tl.fps,
        src_width=analysis.width,
        src_height=analysis.height,
        duration=tl.duration,
        segments=tl.segments,
        words=early_words + out_words,
        pages=pages,
        hook=hook,
        credit=credit,
        cards=cards,
        emoji=emoji,
        spans=spans,
        zooms=[Zoom(start=a, end=b, scale=s) for a, b, s in zooms],
        payoff_s=payoff,
        sfx=sfx_layer(spans, emoji, payoff, style, tl.duration, cold_end=tl.teaser_end or None),
        gameplay=gameplay if kind == "gameplay" else None,
        cold_open=cold_open,
        style=style,
        notes=notes,
    )


# The cut


def clip_words(transcript: Transcript, clip: ClipRow) -> list[Word]:
    """The transcript's words whose middle falls inside the clip."""
    return [w for w in transcript.words if clip.start_s - EPS <= (w.start + w.end) / 2 <= clip.end_s + EPS]


def cuttable(w: Word) -> bool:
    """An isolated filler, but never "like" or "you know"."""
    return w.filler and plain_words(w.text) not in NEVER_CUT


def cold_open_range(
    transcript: Transcript, kept: list[Word], sid: str | None, sids: dict[int, str]
) -> tuple[tuple[float, float] | None, str | None]:
    """The source range of the cold open's teaser (SPEC 8.3), or the note saying why there is none: the line
    ``sid`` from its first kept word, through the kept words and over any filler, to the word before one
    that is struck (so a cut word is never heard) or the last that ends within COLD_MAX_S, with COLD_TAIL_S
    after it but never into the next word."""
    if sid is None or sid not in sids.values():
        return None, NO_COLD_LINE
    line = [w for w in kept if sids.get(w.i) == sid]
    if not line:
        return None, COLD_STRUCK
    if line[0] is kept[0]:
        return None, COLD_AT_START
    start = line[0].start
    keep = {w.i for w in line}
    sentence = transcript.sentence(sid)
    last, cut = None, False
    for w in transcript.words[line[0].i : sentence.w1 + 1]:
        if w.end > start + COLD_MAX_S + EPS:
            cut = True
            break
        if w.i in keep:
            last = w
        elif not cuttable(w):
            break
    if last is None or last.end - start < COLD_MIN_S - EPS:
        return None, COLD_SHORT
    if cut:
        return (start, last.end), None
    after = transcript.words[last.i + 1].start if last.i + 1 < len(transcript.words) else math.inf
    end = max(last.end, min(last.end + COLD_TAIL_S, after, start + COLD_MAX_S))
    return (start, end), None


def sentence_ids(transcript: Transcript, words: list[Word]) -> dict[int, str]:
    """Each word's sentence, by the word's place in the transcript."""
    firsts = [s.w0 for s in transcript.sentences]
    out: dict[int, str] = {}
    for w in words:
        k = bisect.bisect_right(firsts, w.i) - 1
        if k >= 0 and transcript.sentences[k].w1 >= w.i:
            out[w.i] = transcript.sentences[k].id
    return out


def cut_ranges(
    transcript: Transcript,
    clip: ClipRow,
    kept: list[Word],
    style: Style,
    sids: dict[int, str],
    src_duration: float,
) -> list[tuple[float, float]]:
    """The source stretches to keep: from the clip's start, never into the word before it and with a long
    silence before the first word trimmed, to 250 ms after the last word (or the next word's start), with
    the pauses between the kept words shrunk and the cut words taken out."""
    lo = max(0.0, clip.start_s)
    if not kept:
        return clamp([(lo, clip.end_s)], src_duration)
    first, last = kept[0], kept[-1]
    start = max(lo, transcript.words[first.i - 1].end if first.i > 0 else 0.0)
    if style.pause_trim and first.start - start > GAP_S + EPS:
        start = first.start - GAP_KEEP_S / 2
    start = min(start, first.start)
    after = transcript.words[last.i + 1].start if last.i + 1 < len(transcript.words) else math.inf
    end = max(last.end, min(last.end + END_S, after))
    payoff = next(w for w in kept if sids.get(w.i) == sids.get(last.i))
    ranges: list[tuple[float, float]] = []
    a = start
    for prev, nxt in zip(kept, kept[1:], strict=False):
        between = transcript.words[prev.i + 1 : nxt.i]  # all cut
        gap = nxt.start - prev.end
        before = max(0.0, between[0].start - prev.end) if between else gap
        after_cut = max(0.0, nxt.start - between[-1].end) if between else gap
        silence = before + after_cut if between else gap
        if nxt is payoff:
            keep = min(silence, PAYOFF_GAP_S) if style.pause_trim else silence
        elif style.pause_trim and silence > GAP_S + EPS:
            keep = GAP_KEEP_S
        else:
            keep = silence
        keep = max(0.0, keep)
        if not between and keep >= gap - EPS:
            continue
        left = min(before, keep / 2)  # the kept silence, split either side where there is room
        right = min(after_cut, keep - left)
        left = min(before, keep - right)
        ranges.append((a, prev.end + left))
        a = nxt.start - right
    ranges.append((a, end))
    return clamp(ranges, src_duration)


def clamp(ranges: list[tuple[float, float]], src_duration: float) -> list[tuple[float, float]]:
    out = []
    for a, b in ranges:
        a, b = max(0.0, a), min(src_duration, b)
        if b > a + EPS:
            out.append((a, b))
    return out


def pace(kept: list[Word], ranges: list[tuple[float, float]]) -> float:
    """Auto pace: speech under 2.3 words a second plays 1.05 to 1.10 times as fast."""
    total = sum(b - a for a, b in ranges)
    if not kept or total <= EPS or len(kept) / total >= PACE_WPS - EPS:
        return 1.0
    return min(PACE_MAX, max(PACE_MIN, PACE_WPS * total / len(kept)))


def emphasised(kept: list[Word], phrases: list[str]) -> set[int]:
    """The kept words that say one of the emphasis phrases, as their places in the transcript; the little
    words of a phrase ("a", "the", "of") are left plain."""
    tokens = [(tok, w.i) for w in kept for tok in plain_words(w.text).split()]
    said = [tok for tok, _ in tokens]
    out: set[int] = set()
    for phrase in phrases:
        want = plain_words(phrase).split()
        n = len(want)
        for k in range(len(said) - n + 1 if n else 0):
            if said[k : k + n] == want:
                out.update(i for tok, i in tokens[k : k + n] if tok not in STOP_WORDS)
    return out


def first_starts(words: list[EdlWord]) -> dict[str, float]:
    """Where each sentence's first kept word starts."""
    out: dict[str, float] = {}
    for w in words:
        if w.sid is not None:
            out.setdefault(w.sid, w.start)
    return out


def payoff_start(words: list[EdlWord]) -> float | None:
    if not words or words[-1].sid is None or words[-1].sid == words[0].sid:
        return None
    return first_starts(words)[words[-1].sid]


def credit_text(style: Style, credit_name: str | None) -> str | None:
    """The credit line, with the channel put in by plain replacement so braces in it stay as written."""
    if not credit_name or not style.credit_line:
        return None
    return style.credit_format.replace("{channel}", credit_name)


def caption_pages(
    words: list[EdlWord], style: Style, spans: list[Span], duration: float
) -> list[CaptionPage]:
    """The caption pages, ending by the clip's end, each placed for the layout showing at its middle and
    clear of the faces seen while it shows."""
    pages = paginate(words, max_words=1 if PRESETS[style.preset].one_word else 3)
    starts = [s.start for s in spans]
    for p in pages:
        p.end = min(p.end, duration)
        span = spans[max(0, bisect.bisect_right(starts, (p.start + p.end) / 2 + EPS) - 1)]
        place_page(p, style, span.layout, faces_during(spans, p.start, p.end), seam(span.layout))
    return pages


def faces_during(spans: list[Span], start: float, end: float) -> list[Box]:
    """The faces seen on the canvas from ``start`` to ``end``."""
    return [
        (x, y, w, h)
        for s in spans
        if s.start < end + EPS and s.end > start - EPS
        for t, x, y, w, h in s.faces
        if start - EPS <= t <= end + EPS
    ]


def boxes_during(pages: list[CaptionPage], start: float, end: float) -> list[Box]:
    """Where the caption pages shown from ``start`` to ``end`` sit on the canvas."""
    return [p.box for p in pages if p.box is not None and p.start < end - EPS and p.end > start + EPS]


# The overlays


def hook_layer(
    packaging: Packaging,
    style: Style,
    faces: list[Box],
    avoid: list[Box],
    duration: float,
    credit_text: str | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """The hook (SPEC 8.3), with the credit line (SPEC 8.4) under it for the first 4 s, as high as they can
    sit clear of ``faces`` with their hair and of the caption boxes in ``avoid``; else clear of the faces
    alone, so the hook ``covers`` their hair. Failing that the credit line moves down to just above the
    captions, when it can sit there clear of the faces, and the hook tries again on its own; then it may
    cover the top fifth of a face, its ``forehead``; else it sits at 72 % at the top, covering a face for QA
    to report. With no hook the credit line sits at the top."""
    credit = credit_sprite(credit_text, SAFE_TOP) if credit_text else None
    under = credit.h + CREDIT_GAP if credit is not None else 0
    low = low_credit(credit, faces, avoid) if credit is not None else None
    room = under if low is None else 0  # what the hook keeps clear below it once the stack has failed
    tries = [(HAIR, under, None), (0.0, under, "hair"), (HAIR, room, None), (0.0, room, "hair")]
    tries.append((-FOREHEAD, room, "forehead"))
    title, accent = packaging.hook_title.strip(), packaging.accent_word
    sprite, covers, kept = None, None, under
    if title and hook_sprite(title, accent, style) is not None:
        for hair, kept, why in dict.fromkeys(tries):
            sprite = place_hook(title, accent, style, faces, avoid=avoid, hair=hair, under=kept)
            if sprite is not None:
                covers = why
                break
        if sprite is None:
            sprite, covers, kept = hook_sprite(title, accent, style, scale=SCALES[-1]), "face", room
    hook = None
    if sprite is not None:
        hook = {
            "title": title,
            "accent_word": accent,
            "start": 0.0,
            "end": duration if style.hook_whole_clip else min(duration, HOOK_HOLD_S),
            "scale": sprite.scale,
            "box": list(sprite.box),
            "covers": covers,
        }
    if credit is None:
        return hook, None
    if sprite is None:
        y = SAFE_TOP
    elif kept:
        y = sprite.y + sprite.h + CREDIT_GAP
    else:
        y = low
    line = {
        "text": credit_text,
        "start": 0.0,
        "end": min(duration, HOOK_HOLD_S),
        "box": [credit.x, y, credit.w, credit.h],
    }
    return hook, line


def low_credit(credit: Sprite, faces: list[Box], captions: list[Box]) -> int | None:
    """The credit line's top when it moves down (SPEC 8.4): CREDIT_GAP above the highest of the ``captions``
    shown with it, or centred where captions sit when none shows. None when it would come near a face
    there."""
    y = min(b[1] for b in captions) - CREDIT_GAP - credit.h if captions else CENTRE_Y - credit.h // 2
    if any(near((credit.x, y, credit.w, credit.h), f) for f in faces):
        return None
    return y


def card_layer(
    packaging: Packaging,
    style: Style,
    words: list[EdlWord],
    hook: dict[str, Any] | None,
    credit: dict[str, Any] | None,
    duration: float,
    notes: list[str],
) -> list[dict[str, Any]]:
    """Up to three fact cards (SPEC 8.4), one at a time, each from its sentence's first kept word for as long
    as the sentence lasts, within 2.5 to 4 s: in their band once the hook and the credit line are gone, or
    just under a hook kept all clip long (and under the credit line when that sits under the hook)."""
    said: dict[str, list[float]] = {}
    for w in words:
        if w.sid is not None:
            said.setdefault(w.sid, [w.start, w.end])[1] = w.end
    top = [x for x in (hook, credit) if x is not None]
    whole = hook is not None and style.hook_whole_clip
    free = 0.0 if whole else max((x["end"] for x in top), default=0.0)
    below = None
    if whole:
        end = hook["box"][1] + hook["box"][3]
        below = max(x["box"][1] + x["box"][3] for x in top if x["box"][1] <= end + CREDIT_GAP) + CARD_GAP
    if any(c.sid not in said for c in packaging.fact_cards):
        notes.append(CARD_GONE)  # its sentence was cut or the clip's range moved off it
    out: list[dict[str, Any]] = []
    for card in sorted((c for c in packaging.fact_cards if c.sid in said), key=lambda c: said[c.sid][0]):
        if len(out) == MAX_CARDS:
            break
        sprite = card_sprite(card.text)
        if sprite is None:
            if card.text.strip() and NO_CARD not in notes:
                notes.append(NO_CARD)
            continue
        a, b = said[card.sid]
        start = max(a, free, out[-1]["end"] if out else 0.0)
        end = min(duration, start + min(CARD_MAX_S, max(CARD_MIN_S, b - a)))
        y = sprite.y if below is None else below
        if end - start < CARD_MIN_S - EPS or y + sprite.h > SAFE_BOTTOM:
            continue
        out.append(
            {
                "text": card.text,
                "sid": card.sid,
                "start": start,
                "end": end,
                "box": [sprite.x, y, sprite.w, sprite.h],
            }
        )
    return out


def emoji_layer(
    packaging: Packaging,
    style: Style,
    words: list[EdlWord],
    pages: list[CaptionPage],
    spans: list[Span],
    shown: list[dict[str, Any]],
    duration: float,
) -> list[dict[str, Any]]:
    """Each cue's emoji (SPEC 8.5) just above the caption from its word's start for EMOJI_S, at most one in
    any SPACING_S: smaller when it must be to clear a face, and left out when even EMOJI_MIN_PX does not,
    or when it would cover the hook, the credit line or a card."""
    if not style.emoji or not pages:
        return []
    cues = []
    for cue in packaging.emoji_cues:
        want = plain_words(cue.word)
        w = next((w for w in words if w.sid == cue.sid and want and plain_words(w.text) == want), None)
        if w is not None and is_emoji(cue.emoji):
            cues.append((w.start, cue.emoji, w.text))
    out: list[dict[str, Any]] = []
    for start, emoji, word in sorted(cues, key=lambda c: c[0]):
        if out and start - out[-1]["start"] < SPACING_S - EPS:
            continue
        end = min(duration, start + EMOJI_S)
        under = [p for p in pages if p.box is not None and p.start < end - EPS and p.end > start + EPS]
        holding = next((p for p in under if p.start - EPS <= start < p.end), None)
        if holding is None or end - start < POP_S:
            continue
        blocked = faces_during(spans, start, end) + [
            tuple(x["box"]) for x in shown if x["start"] < end - EPS and x["end"] > start + EPS
        ]
        box = emoji_box(holding.box[0] + holding.box[2] / 2, min(p.box[1] for p in under) - FACE_GAP, blocked)
        if box is not None:
            out.append(
                {"emoji": emoji, "word": word, "start": start, "end": end, "box": list(box), "size": box[2]}
            )
    return out


def emoji_box(cx: float, bottom: int, blocked: list[Any]) -> Box | None:
    """The biggest square from EMOJI_PX down to EMOJI_MIN_PX centred on ``cx`` and ending at ``bottom``,
    inside the safe zone and clear of every box in ``blocked``."""
    for size in range(EMOJI_PX, EMOJI_MIN_PX - 1, -EMOJI_STEP):
        box = (min(max(round(cx - size / 2), SAFE_LEFT), SAFE_RIGHT - size), bottom - size, size, size)
        if box[1] >= SAFE_TOP and not any(overlap(box, b) for b in blocked):
            return box
    return None


def overlap(a: Any, b: Any) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def sfx_layer(
    spans: list[Span],
    emoji: list[dict[str, Any]],
    payoff_s: float | None,
    style: Style,
    duration: float,
    *,
    cold_end: float | None = None,
) -> list[dict[str, Any]]:
    """The sound effects (SPEC 8.5), each peaking on its moment: a soft hit on the payoff, a whoosh out of a
    cold open at ``cold_end`` and on a hard layout change (one whose panels differ, so a face to a motion shot
    is none), and a pop on each emoji. At most one whoosh in any WHOOSH_GAP_S and one pop in any POP_GAP_S,
    no two effects within SFX_CLEAR_S, the hit first, then the cold open's whoosh, the other whooshes and
    the pops."""
    if not style.sfx:
        return []
    wants = [("hit", payoff_s)] if payoff_s is not None else []
    wants += [("whoosh", cold_end)] if cold_end is not None else []
    wants += [
        ("whoosh", b.start)
        for a, b in zip(spans, spans[1:], strict=False)
        if PANELS.get(a.layout) != PANELS.get(b.layout)
    ]
    wants += [("pop", e["start"]) for e in emoji]
    gaps = {"whoosh": WHOOSH_GAP_S, "pop": POP_GAP_S}
    kept: list[tuple[str, float]] = []
    for kind, on in wants:
        if on < duration - EPS and all(
            abs(on - t) >= (gaps.get(kind, SFX_CLEAR_S) if k == kind else SFX_CLEAR_S) - EPS for k, t in kept
        ):
            kept.append((kind, on))
    cues = [{"kind": k, "at": round(max(0.0, on - PEAK_S[k]), 6), "on": on} for k, on in kept]
    return sorted(cues, key=lambda c: c["at"])


def seam(layout: str) -> int | None:
    """Where a split layout's two parts meet on the canvas."""
    if layout == "gameplay":
        return GAMEPLAY_BOX[1]
    boxes = PANELS.get(layout, [])
    return boxes[1][1] if len(boxes) > 1 else None


# The frame


def even(x: float) -> int:
    """``x`` rounded down to an even number of pixels, at least 2."""
    return max(2, 2 * math.floor(x / 2))


def fit_box(src_w: int, src_h: int) -> Box:
    """The whole source frame scaled to fit the canvas, centred."""
    s = min(W_OUT / src_w, H_OUT / src_h)
    w, h = min(W_OUT, even(src_w * s)), min(H_OUT, even(src_h * s))
    return ((W_OUT - w) // 2, (H_OUT - h) // 2, w, h)


def nearest(times: np.ndarray, t: float) -> int | None:
    """The sample nearest ``t``, when one is within 0.15 s."""
    if not len(times):
        return None
    k = int(np.searchsorted(times, t))
    best = min((j for j in (k - 1, k) if 0 <= j < len(times)), key=lambda j: abs(times[j] - t))
    return best if abs(times[best] - t) <= SEEN_S + EPS else None


def strength(track: Track) -> tuple[int, float]:
    """Real detections first, then size."""
    area = statistics.fmean(b[2] * b[3] for b in track.boxes) if track.boxes else 0.0
    return sum(1 for s in track.scores if s > 0), area


class Framer:
    """Lays out every output frame: which shot it comes from, its layout, and each panel's crop."""

    def __init__(self, analysis: Analysis, shots: list[Shot], tl: Timeline):
        self.a, self.shots, self.fps = analysis, shots, tl.fps
        self.w, self.h = analysis.width, analysis.height
        self.n = tl.frames
        self.jump = round(tl.teaser_end * tl.fps) or None  # the body's first frame after a cold open
        self.t_out = np.arange(self.n) / tl.fps
        self.t_src = np.array([tl.to_source(t) for t in self.t_out])
        starts = np.array([s.start_s for s in shots])
        self.shot_of = np.clip(np.searchsorted(starts, self.t_src + EPS, side="right") - 1, 0, len(shots) - 1)
        self.tracks = {t.id: t for t in analysis.tracks}
        self.times = {t.id: np.asarray(t.times, dtype=float) for t in analysis.tracks}
        self.turns = sorted(analysis.turns, key=lambda u: u.start_s)
        self.turn_starts = [u.start_s for u in self.turns]
        ranked = [
            sorted((i for i in s.tracks if i in self.tracks), key=self._strength, reverse=True) for s in shots
        ]
        self.main = [r[0] if r else None for r in ranked]
        self.pairs = [sorted(r[:2], key=self._eye_x) if len(r) >= 2 else None for r in ranked]

    def _strength(self, tid: int) -> tuple[int, float]:
        return strength(self.tracks[tid])

    def _eye_x(self, tid: int) -> float:
        return statistics.median(e[0] for e in self.tracks[tid].eyes)

    def at(self, tid: int | None, t: float) -> int | None:
        """The sample of track ``tid`` nearest ``t``, when one is within 0.15 s."""
        times = self.times.get(tid) if tid is not None else None
        return None if times is None else nearest(times, t)

    def lengths(self) -> list[float]:
        """How long each shot runs in the clip."""
        return [float(n) / self.fps for n in np.bincount(self.shot_of, minlength=len(self.shots))]

    def speaker(self, f: int) -> int | None:
        """Whoever is talking at frame ``f`` when they are in its shot, or else the shot's main face."""
        t, k = self.t_src[f], self.shot_of[f]
        j = bisect.bisect_right(self.turn_starts, t + EPS) - 1
        if j >= 0 and t < self.turns[j].end_s - EPS and self.turns[j].track in self.shots[k].tracks:
            return self.turns[j].track
        return self.main[k]

    def spans(
        self, per_shot: list[str], zooms: list[tuple[float, float, float]]
    ) -> tuple[list[Span], list[tuple[float, float, float]]]:
        """The spans, and the punch-ins that start in a face or gameplay span, cut short at its end."""
        labels = []
        for f in range(self.n):
            k = self.shot_of[f]
            label = per_shot[k]
            labels.append("face" if label == "stacked" and self.pairs[k] is None else label)
        runs, f0 = [], 0
        for f in range(1, self.n + 1):
            if f == self.n or labels[f] != labels[f0]:
                runs.append((labels[f0], f0, f))
                f0 = f
        kept = []
        for a, b, s in zooms:
            f = min(self.n - 1, round(a * self.fps))
            label, _, f1 = next(r for r in runs if r[1] <= f < r[2])
            b = min(b, f1 / self.fps)
            if label in ZOOMABLE and b - a >= PUNCH_MIN_S - EPS:
                kept.append((a, b, s))
        return [self.span(label, f0, f1, kept) for label, f0, f1 in runs], kept

    def span(self, label: str, f0: int, f1: int, zooms: list[tuple[float, float, float]]) -> Span:
        cuts = [
            float(self.t_out[f])
            for f in range(f0 + 1, f1)
            if self.shot_of[f] != self.shot_of[f - 1] or f == self.jump
        ]
        frames = range(f0, f1)
        if label in ZOOMABLE:
            cw, ch = crop_size(self.w, self.h, W_OUT, PANELS[label][0][3])
            crops = [self.track_crops([self.speaker(f) for f in frames], f0, f1, cw, ch, cuts, zooms)]
        elif label == "stacked":
            pairs = [self.pairs[self.shot_of[f]] or (None, None) for f in frames]
            tops, bottoms = [p[0] for p in pairs], [p[1] for p in pairs]
            cw, ch = self.stack_size(f0, tops, bottoms)
            crops = [self.track_crops(ids, f0, f1, cw, ch, cuts, ()) for ids in (tops, bottoms)]
        elif label == "stream":
            crops = [[box] * (f1 - f0) for box in self.stream_crops(f0, f1)]
        elif label == "motion":
            crops = [self.follow_motion(f0, f1, cuts)]
        else:
            crops = [[(0, 0, self.w, self.h)] * (f1 - f0)]
        boxes = [fit_box(self.w, self.h)] if label == "fit" else PANELS[label]
        return Span(
            start=f0 / self.fps,
            end=f1 / self.fps,
            layout=label,
            boxes=boxes,
            crops=crops,
            faces=self.faces(f0, f1, boxes, crops),
        )

    def track_crops(self, targets, f0, f1, cw, ch, cuts, zooms) -> list[Box]:
        """The crop path following ``targets`` (a track id or None for each frame) and keeping the face's
        box inside, cutting hard where the target changes within a shot."""
        points, faces, switches = [], [], []
        for j, f in enumerate(range(f0, f1)):
            tid = targets[j]
            if (
                j
                and tid is not None
                and targets[j - 1] not in (None, tid)
                and self.shot_of[f] == self.shot_of[f - 1]
            ):
                switches.append(float(self.t_out[f]))
            k = self.at(tid, self.t_src[f])
            if k is not None:
                t = float(self.t_out[f])
                ex, ey = self.tracks[tid].eyes[k]
                x, y, w, h = self.tracks[tid].boxes[k]
                points.append((t, ex * self.w, ey * self.h))
                faces.append((t, x * self.w, y * self.h, w * self.w, h * self.h))
        path = follow(
            points,
            self.t_out[f0:f1],
            src_w=self.w,
            src_h=self.h,
            crop_w=cw,
            crop_h=ch,
            cuts=cuts,
            switches=switches,
            zoomed=zooms,
            faces=faces,
        )
        return path.rects

    def stack_size(self, f0: int, tops: list, bottoms: list) -> tuple[int, int]:
        """A stacked panel's crop: 90% of the space between the two faces, from half to all of the widest
        crop that fits."""
        hi_w, hi_h = crop_size(self.w, self.h, W_OUT, 960)
        seps = []
        for j, (a, b) in enumerate(zip(tops, bottoms, strict=True)):
            t = self.t_src[f0 + j]
            ka, kb = self.at(a, t), self.at(b, t)
            if ka is not None and kb is not None:
                seps.append(abs(self.tracks[b].eyes[kb][0] - self.tracks[a].eyes[ka][0]) * self.w)
        sep = statistics.median(seps) if seps else self.w / 2
        cw = min(hi_w, max(even(hi_w / 2), even(STACK_FILL * sep)))
        return cw, min(hi_h, even(cw * 960 / W_OUT))

    def stream_crops(self, f0: int, f1: int) -> tuple[Box, Box]:
        """A still facecam crop around the clip's facecam, the same in every stream span (or, in a clip
        without one, around the span's main face), and the middle of the game below it."""
        gw, gh = crop_size(self.w, self.h, W_OUT, 1280)
        game = ((self.w - gw) // 2, (self.h - gh) // 2, gw, gh)
        cams = [tid for s in self.shots if s.kind == "stream" for tid in s.tracks if tid in self.tracks]
        if not cams:
            ids = {self.main[k] for k in set(self.shot_of[f0:f1].tolist())} - {None}
            if not ids:
                cw, ch = crop_size(self.w, self.h, W_OUT, 640)
                return ((self.w - cw) // 2, (self.h - ch) // 2, cw, ch), game
            cams = [max(ids, key=self._strength)]
        boxes = [b for tid in cams for b in self.tracks[tid].boxes]
        x, y, w, h = (statistics.median(b[i] for b in boxes) for i in range(4))
        ch = min(even(self.h), even(FACECAM_FILL * h * self.h))
        cw = even(ch * W_OUT / 640)
        if cw > self.w:
            cw = even(self.w)
            ch = even(cw * 640 / W_OUT)
        cx = min(max(round((x + w / 2) * self.w - cw / 2), 0), self.w - cw)
        cy = min(max(round((y + h / 2) * self.h - ch / 2), 0), self.h - ch)
        return (cx, cy, cw, ch), game

    def follow_motion(self, f0: int, f1: int, cuts: list[float]) -> list[Box]:
        """The crop follows where the frame changes most."""
        cw, ch = crop_size(self.w, self.h, W_OUT, H_OUT)
        motion = self.a.motion
        mt = np.array([m[0] for m in motion], dtype=float)
        points = []
        for f in range(f0, f1):
            k = nearest(mt, self.t_src[f])
            if k is not None and motion[k][2] > EPS:
                points.append((float(self.t_out[f]), motion[k][1] * self.w, self.h / 2))
        path = follow(points, self.t_out[f0:f1], src_w=self.w, src_h=self.h, crop_w=cw, crop_h=ch, cuts=cuts)
        return path.rects

    def faces(self, f0: int, f1: int, boxes: list[Box], crops: list[list[Box]]) -> list:
        """Every face of the frame's shot, every 0.1 s, put through each panel's crop onto the canvas."""
        out: list[tuple[float, int, int, int, int]] = []
        for f in range(f0, f1, max(1, round(FACE_STEP_S * self.fps))):
            for tid in self.shots[self.shot_of[f]].tracks:
                k = self.at(tid, self.t_src[f])
                if k is None or tid not in self.tracks:
                    continue
                x, y, w, h = self.tracks[tid].boxes[k]
                fx, fy, fw, fh = x * self.w, y * self.h, w * self.w, h * self.h
                for (px, py, pw, ph), path in zip(boxes, crops, strict=True):
                    cx, cy, cw, ch = path[f - f0]
                    sx, sy = pw / cw, ph / ch
                    x0 = round(max(px, px + (fx - cx) * sx))
                    y0 = round(max(py, py + (fy - cy) * sy))
                    x1 = round(min(px + pw, px + (fx + fw - cx) * sx))
                    y1 = round(min(py + ph, py + (fy + fh - cy) * sy))
                    if x1 > x0 and y1 > y0:
                        out.append((round(float(self.t_out[f]), 6), x0, y0, x1 - x0, y1 - y0))
        return out
