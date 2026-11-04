"""QA on a finished clip (SPEC 7.11, 8.1, 8.4, 10.2, 10.4): what probing the file finds, held against the
edit list it was rendered from and against Meta's and TikTok's limits. Every check is in the result, passed
or not; a failed one carries one plain sentence, and a hard failure blocks posting, to both apps or to the
one app whose limit it breaks."""

from __future__ import annotations

import math
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np
from pydantic import BaseModel, computed_field

from clipforge.media.ffmpeg import QUIET, MediaInfo, ffmpeg_bin, probe, run
from clipforge.render.audio import AudioReport
from clipforge.render.captions import shown
from clipforge.render.edl import (
    EDL,
    EPS,
    FACE_STEP_S,
    H_OUT,
    NO_CREDIT,
    NO_HOOK,
    PANELS,
    SHAPING,
    W_OUT,
    Span,
    overlap,
)
from clipforge.render.encode import PEAK_LIMIT, peak_bitrate
from clipforge.render.hook import below_forehead
from clipforge.render.layouts import inside, panels_for
from clipforge.render.sprites import SAFE_BOTTOM, SAFE_LEFT, SAFE_RIGHT, SAFE_TOP
from clipforge.vision.camera import UPSCALE_WARN

TARGET_LUFS, LUFS_TOLERANCE = -14.0, 1.5
BLACK_LUMA = 20  # a first frame this dark on average or darker is black, which is 16
FPS_MIN, FPS_MAX = 23.0, 60.0  # Meta's; TikTok takes the same
FPS_SLACK = 0.01
INSTAGRAM_MIN_S, INSTAGRAM_MAX_S = 3.0, 15 * 60
TIKTOK_MAX_S = 10 * 60
INSTAGRAM_MAX_BYTES = 300_000_000
INSTAGRAM_MAX_RATE = 48000  # the sound's highest sample rate
PRIMING_S = 1024 / 48000  # the AAC encoder's lead in, which an MP4 without an edit list keeps
NOT_MP4 = "The file is not an MP4."
QUICKTIME = "The file is a QuickTime movie, not an MP4."
INDEX_LAST = "The file's index comes after its media, where Instagram needs it first."
EDIT_LIST = "The file has an edit list, which Instagram rejects."
SILENT = "The clip is nearly silent, so its sound was left as it was."
# what reaches outside the safe zone, one and many
THINGS = (
    ("the hook title", "the hook title"),
    ("the credit line", "the credit line"),
    ("a caption", "{n} captions"),
    ("a fact card", "{n} fact cards"),
    ("an emoji", "{n} emoji"),
)


class Check(BaseModel):
    name: str
    ok: bool
    hard: bool  # a failure blocks posting
    message: str = ""  # one plain sentence when it failed
    platform: str | None = None  # the one app whose limit it is, None for both


class QAResult(BaseModel):
    checks: list[Check]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def passed(self) -> bool:
        """No hard failure: the clip may go to either app."""
        return not any(not c.ok and c.hard for c in self.checks)

    def passed_for(self, platform: str) -> bool:
        """No hard failure that stops the clip going to ``platform``."""
        return not any(not c.ok and c.hard and c.platform in (None, platform) for c in self.checks)

    def problems(self) -> list[str]:
        """The failed checks' sentences, the ones that block posting first."""
        failed = [c for c in self.checks if not c.ok]
        return [c.message for c in failed if c.hard] + [c.message for c in failed if not c.hard]


@dataclass(frozen=True)
class Measured:
    """What QA reads from the file itself."""

    info: MediaInfo
    first_luma: float | None  # the first frame's mean luma (16 to 235), None when there is none
    peak_bps: float  # the busiest second of the video
    layout: str | None  # why the MP4's layout won't do for Instagram, None when it will


def run_qa(
    path: Path, edl: EDL, audio: AudioReport, *, upscale: float, hdr: bool = False, own: bool = False
) -> QAResult:
    """QA on the clip rendered at ``path`` from ``edl``, whose sound ``audio`` reports on. ``upscale`` is
    how much the most enlarged panel is scaled up (``upscale_of``), ``hdr`` says the source is HDR, and
    ``own`` that it is the user's own video, which needs no extra edits to count as original."""
    return judge(measure(path), edl, audio, upscale=upscale, hdr=hdr, own=own)


def measure(path: Path) -> Measured:
    info = probe(path)
    return Measured(
        info=info,
        first_luma=first_luma(path) if info.has_video else None,
        peak_bps=peak_bitrate(path) if info.has_video else 0.0,
        layout=mp4_problem(path),
    )


def first_luma(path: Path) -> float | None:
    """The mean luma of ``path``'s first frame, on video's 16 to 235."""
    args = ["-map", "0:v:0", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "yuv420p", "pipe:1"]
    raw = run([ffmpeg_bin(), *QUIET, "-i", str(Path(path).absolute()), *args]).stdout
    luma = raw[: len(raw) * 2 // 3]  # the Y plane comes first, two thirds of a yuv420p frame
    return float(np.frombuffer(luma, np.uint8).mean()) if luma else None


def mp4_problem(path: Path) -> str | None:
    """Why the file's layout won't do for Instagram (SPEC 10.2: an MP4 with its index at the front and no
    edit list), None when it will."""
    with open(path, "rb") as f:
        top = list(_boxes(f, 0, os.fstat(f.fileno()).st_size))
        kinds = [kind for kind, _, _ in top]
        if not kinds or kinds[0] != b"ftyp":
            return NOT_MP4
        f.seek(top[0][1])
        if f.read(4) == b"qt  ":
            return QUICKTIME
        if b"moov" not in kinds:
            return NOT_MP4
        if b"mdat" in kinds and kinds.index(b"mdat") < kinds.index(b"moov"):
            return INDEX_LAST
        _, start, end = top[kinds.index(b"moov")]
        for kind, s, e in _boxes(f, start, end):
            if kind == b"trak" and any(k == b"edts" for k, _, _ in _boxes(f, s, e)):
                return EDIT_LIST
    return None


def _boxes(f: BinaryIO, start: int, end: int) -> Iterator[tuple[bytes, int, int]]:
    """The MP4 boxes from ``start`` to ``end`` of ``f``: each one's type and where its contents start and
    end. It stops at a box that is cut short or runs past ``end``."""
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        head = f.read(8)
        size, body = int.from_bytes(head[:4], "big"), pos + 8
        if size == 1:  # a 64-bit size follows the type
            size, body = int.from_bytes(f.read(8), "big"), pos + 16
        if size < body - pos or pos + size > end:  # one that runs to the end (size 0) is always last
            return
        yield head[4:], body, pos + size
        pos += size


def judge(
    m: Measured, edl: EDL, audio: AudioReport, *, upscale: float, hdr: bool = False, own: bool = False
) -> QAResult:
    """``run_qa`` on what was measured."""
    info = m.info
    rules: list[tuple[str, bool, str | None, str | None]] = [  # name, hard, platform, problem
        ("codec", True, None, _codec(info)),
        ("size", True, None, _size(info)),
        ("fps_range", True, None, _fps_range(info)),
        ("fps", False, None, _fps(info, edl)),
        ("duration", False, None, _duration(info, edl)),
        ("instagram_length", True, "instagram", _instagram_length(info.duration_s)),
        ("tiktok_length", True, "tiktok", _tiktok_length(info.duration_s)),
        ("file_size", True, "instagram", _file_size(info.size_bytes)),
        ("bitrate", True, "instagram", _bitrate(m.peak_bps)),
        ("mp4", True, "instagram", m.layout),
        ("audio", True, None, None if info.has_audio else "The video has no sound track."),
        ("audio_format", True, "instagram", _audio_format(info)),
        ("first_frame", True, None, _first_frame(m.first_luma)),
        ("loudness", False, None, _loudness(audio)),
        ("safe_zone", False, None, _safe_zone(edl)),
        ("hook_face", False, None, _hook_face(edl)),
        ("captions", False, None, _captions(edl)),
        ("upscale", False, None, _upscale(upscale)),
        ("hdr", False, None, _hdr(hdr)),
        ("originality", False, None, _originality(edl, own)),
    ]
    return _result(rules)


def as_is_qa(m: Measured) -> QAResult:
    """QA on a video posted as it is (SPEC 14.2): only what the apps themselves check. Its size, length
    and look are the video's own, and ClipForge added nothing to it to check."""
    info = m.info
    return _result(
        [
            ("codec", True, None, _codec(info)),
            ("fps_range", True, None, _fps_range(info)),
            ("instagram_length", True, "instagram", _instagram_length(info.duration_s)),
            ("tiktok_length", True, "tiktok", _tiktok_length(info.duration_s)),
            ("file_size", True, "instagram", _file_size(info.size_bytes)),
            ("bitrate", True, "instagram", _bitrate(m.peak_bps)),
            ("mp4", True, "instagram", m.layout),
            ("audio_format", True, "instagram", _audio_format(info)),
        ]
    )


def _result(rules: list[tuple[str, bool, str | None, str | None]]) -> QAResult:
    return QAResult(
        checks=[
            Check(name=name, ok=problem is None, hard=hard, message=problem or "", platform=platform)
            for name, hard, platform, problem in rules
        ]
    )


def _named(codec: str | None) -> str:
    return codec.upper() if codec else "an unknown format"


def _rate(fps: float) -> str:
    return f"{round(fps, 3):g}"


def _minutes(seconds: float) -> str:
    m, s = divmod(math.ceil(seconds), 60)
    return f"{m} min {s} s" if s else f"{m} min"


def _codec(info: MediaInfo) -> str | None:
    if not info.has_video:
        return "The file has no picture."
    if info.vcodec != "h264":
        return f"The video uses {_named(info.vcodec)}, where the apps need H.264."
    if info.pix_fmt != "yuv420p":
        return f"The video's colour format is {info.pix_fmt or 'unknown'}, where the apps need yuv420p."
    return None


def _size(info: MediaInfo) -> str | None:
    if info.has_video and (info.width, info.height) != (W_OUT, H_OUT):
        return f"The video is {info.width} x {info.height} instead of {W_OUT} x {H_OUT}."
    return None


def _fps_range(info: MediaInfo) -> str | None:
    fps = info.nominal_fps or info.fps
    if info.has_video and not FPS_MIN <= fps <= FPS_MAX:
        return f"The video runs at {_rate(fps)} fps, outside the 23 to 60 fps the apps take."
    return None


def _fps(info: MediaInfo, edl: EDL) -> str | None:
    fps = info.nominal_fps or info.fps
    if info.has_video and abs(fps - edl.fps) > FPS_SLACK:
        return f"The video runs at {_rate(fps)} fps instead of {_rate(edl.fps)} fps."
    return None


def _duration(info: MediaInfo, edl: EDL) -> str | None:
    """The picture within a frame of the edit list, and the sound within a frame and the encoder's lead in
    (which an MP4 without an edit list shows as a longer first frame, so a stream's own length has it too)."""
    slack = 1 / edl.fps + PRIMING_S + 0.001
    picture = sound = None
    if info.has_video and info.frames:
        if abs(info.frames - round(edl.duration * edl.fps)) > 1:
            picture = info.frames / (info.nominal_fps or edl.fps)
    elif info.has_video and info.video_duration_s and abs(info.video_duration_s - edl.duration) > slack:
        picture = info.video_duration_s
    if info.has_audio and info.audio_duration_s and abs(info.audio_duration_s - edl.duration) > slack:
        sound = info.audio_duration_s
    should = f"{edl.duration:.2f} s"
    if picture is not None and sound is not None:
        return f"The picture lasts {picture:.2f} s and the sound {sound:.2f} s, instead of {should}."
    if picture is not None:
        return f"The picture lasts {picture:.2f} s instead of {should}."
    if sound is not None:
        return f"The sound lasts {sound:.2f} s instead of {should}."
    return None


def _instagram_length(seconds: float) -> str | None:
    if seconds < INSTAGRAM_MIN_S:
        return f"The clip is {math.floor(seconds * 10) / 10:g} s long, under Instagram's 3 s minimum."
    if seconds > INSTAGRAM_MAX_S:
        return f"The clip is {_minutes(seconds)} long, over Instagram's 15 minute limit."
    return None


def _tiktok_length(seconds: float) -> str | None:
    if seconds > TIKTOK_MAX_S:
        return f"The clip is {_minutes(seconds)} long, over TikTok's 10 minute limit."
    return None


def _file_size(size: int) -> str | None:
    if size > INSTAGRAM_MAX_BYTES:
        return f"The file is {math.ceil(size / 1e6)} MB, over Instagram's 300 MB limit."
    return None


def _bitrate(bps: float) -> str | None:
    if bps > PEAK_LIMIT:
        mbps = math.ceil(bps / 1e5) / 10
        return f"The busiest second of the video is {mbps:g} Mbps, over Instagram's 25 Mbps limit."
    return None


def _audio_format(info: MediaInfo) -> str | None:
    if not info.has_audio:
        return None  # "audio" says so
    if info.acodec != "aac":
        return f"The sound uses {_named(info.acodec)}, where Instagram needs AAC."
    if info.sample_rate > INSTAGRAM_MAX_RATE:
        return f"The sound is sampled at {info.sample_rate / 1000:g} kHz, over Instagram's 48 kHz limit."
    if info.channels not in (1, 2):
        return f"The sound has {info.channels} channels, where Instagram takes 1 or 2."
    return None


def _first_frame(luma: float | None) -> str | None:
    if luma is not None and luma <= BLACK_LUMA:
        return "The first frame is black, so the clip would open on nothing."
    return None


def _loudness(audio: AudioReport) -> str | None:
    if audio.silent:
        return SILENT
    lufs = audio.output_lufs
    if abs(lufs - TARGET_LUFS) <= LUFS_TOLERANCE:
        return None
    way = "quieter" if lufs < TARGET_LUFS else "louder"
    return f"The sound is {lufs:.1f} LUFS instead of {TARGET_LUFS:g}, so it will play {way} than other clips."


def _outside(box: Any) -> bool:
    x, y, w, h = box
    return x < SAFE_LEFT or y < SAFE_TOP or x + w > SAFE_RIGHT or y + h > SAFE_BOTTOM


def _safe_zone(edl: EDL) -> str | None:
    """Every text box (and emoji) inside x 90 to 940 and y 270 to 1250 (SPEC 8.1, 8.2)."""
    groups = [
        [edl.hook["box"]] if edl.hook else [],
        [edl.credit["box"]] if edl.credit else [],
        [page.box for page in edl.pages if page.box is not None],
        [card["box"] for card in edl.cards],
        [e["box"] for e in edl.emoji],
    ]
    names, total = [], 0
    for (one, many), boxes in zip(THINGS, groups, strict=True):
        n = sum(_outside(box) for box in boxes)
        if n:
            names.append(one if n == 1 else many.format(n=n))
            total += n
    if not names:
        return None
    things = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    verb, them = ("reaches", "it") if total == 1 else ("reach", "them")
    where = f"outside the safe zone, where the apps' buttons can cover {them}"
    return f"{things[0].upper()}{things[1:]} {verb} {where}."


def _hook_face(edl: EDL) -> str | None:
    """No face under the hook title while it shows (from frame 0), the top fifth of a face aside."""
    hook = edl.hook
    if not hook:
        return None
    times = {
        t
        for span in edl.spans
        for t, *face in span.faces
        if t < hook["end"] - EPS and overlap(below_forehead(face), hook["box"])
    }
    if not times:
        return None
    step = max(1, round(FACE_STEP_S * edl.fps)) / edl.fps  # how often the faces were written down
    return f"The hook title covers a face for {len(times) * step:.1f} s."


def _label(text: str) -> str:
    return shown(text) or text.strip()


def _captions(edl: EDL) -> str | None:
    """The captioned words are the kept words, each once and in order, as captions show them; captions
    turned off for a script ClipForge cannot draw yet are named as off. A cold open's words and pages say
    its line a second time, so the check reads the clip after it."""
    after = edl.cold_open["out_end"] - EPS if edl.cold_open else -math.inf
    pages = [p for p in edl.pages if p.start >= after]
    said = {w.i: shown(w.text) for w in edl.words if shown(w.text) and not w.teaser}
    seen = [(w.i, w.text) for page in pages for w in page.words]
    if not pages:
        if SHAPING in edl.notes:
            return f"{SHAPING}."
        return "The clip has no captions." if said else None
    if [(i, shown(t)) for i, t in seen] == list(said.items()):
        return None
    for i, t in seen:
        if i not in said:
            return f'The captions show "{_label(t)}", which the clip never says.'
    for i, t in seen:
        if shown(t) != said[i]:
            return f'The captions show "{_label(t)}" where the clip says "{said[i]}".'
    shown_i = [i for i, _ in seen]
    for i in shown_i:
        if shown_i.count(i) > 1:
            return f'The captions show "{said[i]}" twice.'
    starts = {w.i: w.start for w in edl.words if not w.teaser}
    missing = [i for i in said if i not in shown_i]
    if missing:
        first = f'"{said[missing[0]]}" at {starts[missing[0]]:.1f} s'
        if len(missing) == 1:
            return f"The captions leave out {first}."
        return f"The captions leave out {len(missing)} words, the first {first}."
    return "The captions show the words out of order."


def _upscale(factor: float) -> str | None:
    if factor > UPSCALE_WARN:
        return (
            f"The picture is enlarged {factor:.1f} times, so it may look soft; a higher quality download "
            "would be sharper."
        )
    return None


def _hdr(hdr: bool) -> str | None:
    if hdr:
        return (
            "The source video is HDR, so ClipForge converted its colours and its brightest parts may look"
            " clipped."
        )
    return None


def _originality(edl: EDL, own: bool) -> str | None:
    """SPEC 8.4: a hook title and one more edit of ClipForge's own: a fact card, the credit line, a cold
    open or a voiceover. It never asks for an edit the clip's notes say ClipForge cannot draw."""
    if own:
        return None
    if not edl.hook:
        if NO_HOOK in edl.notes:
            return f"Light on original edits: {_lowered(NO_HOOK)}."
        return "Light on original edits: the clip needs a hook title and a fact card or credit line."
    if not (edl.cards or edl.credit or edl.cold_open or edl.voiceover):
        if NO_CREDIT in edl.notes:
            return f"Light on original edits: {_lowered(NO_CREDIT)}, so add a fact card."
        return "Light on original edits: add a fact card or the credit line to the hook title."
    return None


def _lowered(note: str) -> str:
    return note[0].lower() + note[1:]


def upscale_of(edl: EDL) -> float:
    """How much the render scales up the most enlarged part of the source, leaving out the gameplay video
    and the punch-ins' brief extra zoom (SPEC 7.7: the warning is about the crop); under 1 when every
    panel shrinks it."""
    w, h = edl.src_width, edl.src_height
    spans = edl.spans or [Span(start=0.0, end=edl.duration, layout="fit")]  # as render_video draws it
    factors = []
    for span in spans:
        if span.layout == "fit":
            pairs = {(p.box, p.crop, 1.0) for p in panels_for("fit", w, h) if not p.blurred}
        else:
            panels = zip(PANELS[span.layout], span.crops, strict=False)
            pairs = {
                (box, crop, _zoom(edl, span.start + j / edl.fps))
                for box, path in panels
                for j, crop in enumerate(path)
            }
        for (_, _, bw, bh), crop, zoom in pairs:
            _, _, cw, ch = inside(crop, w, h)
            factors.append(max(bw / cw, bh / ch) / zoom)
    return max(factors, default=1.0)


def _zoom(edl: EDL, t: float) -> float:
    """The punch-in's scale at output time ``t``; 1 outside them."""
    return next((z.scale for z in edl.zooms if z.start - EPS <= t < z.end - EPS), 1.0)
