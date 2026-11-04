"""One pass over a clip's range plus 1 s either side (SPEC 7.5 to 7.8): faces, tracks, speaker turns, shot
classes, motion and the layout, saved as clips/<clip_id>/analysis.json."""

from __future__ import annotations

import cv2
import numpy as np

from clipforge.media.ffmpeg import ToolError, ffmpeg_bin, run
from clipforge.models import Analysis, FaceBox, Shot, Signals, Track, Transcript
from clipforge.vision.asd import frame_loudness, mouth_activity, mouth_patch, speaker_turns
from clipforge.vision.camera import crop_size, upscale
from clipforge.vision.decode import ClipSource, analysis_decode, video_geometry
from clipforge.vision.layout import LayoutChoice, choose_layout
from clipforge.vision.shots import FrameStatter, classify_shots, clip_quality
from clipforge.vision.tracks import build_tracks

ANALYSIS_VERSION = 2  # 2: quiet scene cuts, facecams over any content, still shots shown whole
PAD_S = 1.0
SR = 16000
OUT_W, OUT_H = 1080, 1920
MOTION_WIDTH = 160
EPS = 1e-6


class MotionMeter:
    """Where the frame changed since the one before, and how much: the centre x of the absolute difference
    (0 to 1, 0.5 when nothing moved) and its mean (0 to 1)."""

    def __init__(self):
        self._prev: np.ndarray | None = None

    def __call__(self, frame: np.ndarray) -> tuple[float, float]:
        grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        h, w = grey.shape
        size = (MOTION_WIDTH, max(1, round(h * MOTION_WIDTH / w)))
        grey = cv2.resize(grey, size, interpolation=cv2.INTER_AREA)
        prev, self._prev = self._prev, grey
        if prev is None:
            return 0.5, 0.0
        diff = cv2.absdiff(grey, prev).astype(float)
        cols = diff.sum(axis=0)
        total = float(cols.sum())
        if total <= EPS:
            return 0.5, 0.0
        centre = float((cols * (np.arange(len(cols)) + 0.5)).sum() / total / len(cols))
        return centre, float(diff.mean()) / 255


def clip_audio(src: ClipSource, start_s: float, end_s: float) -> np.ndarray:
    """16 kHz mono samples of the range, empty when the file has no sound."""
    args = [ffmpeg_bin(), "-nostdin", "-v", "error", "-ss", f"{max(0.0, src.file_time(start_s)):.3f}"]
    args += ["-t", f"{max(0.0, end_s - start_s):.3f}", "-i", str(src.path), "-vn", "-ac", "1"]
    args += ["-ar", str(SR), "-f", "s16le", "-"]
    try:
        out = run(args).stdout
    except ToolError:  # no audio stream: the speaker turns fall back to silence
        return np.zeros(0, np.float32)
    return np.frombuffer(out, np.int16).astype(np.float32) / 32768


def words_per_s(transcript: Transcript, start_s: float, end_s: float) -> float:
    n = sum(1 for w in transcript.words if w.start >= start_s - EPS and w.end <= end_s + EPS)
    return n / max(end_s - start_s, EPS)


def subdued_share(transcript: Transcript, signals: Signals | None, start_s: float, end_s: float) -> float:
    """The share of the range's speech, by time, in sentences tagged {subdued}."""
    total = quiet = 0.0
    for s in transcript.sentences:
        span = min(s.end, end_s) - max(s.start, start_s)
        if span <= 0:
            continue
        sig = signals.sentences.get(s.id) if signals else None
        total += span
        if "{subdued}" in {*s.tags, *(sig.tags if sig else [])}:
            quiet += span
    return quiet / total if total > EPS else 0.0


def analysis_key(src: ClipSource, start_s: float, end_s: float) -> str:
    """The file, its size and the range: a different key means analyse again."""
    return f"{src.path.name}:{src.path.stat().st_size}:{src.offset_s:.3f}:{start_s:.3f}-{end_s:.3f}"


def analyze_clip(
    src: ClipSource,
    start_s: float,
    end_s: float,
    *,
    detector,
    transcript: Transcript,
    signals: Signals | None,
    gameplay_mode: str,
    has_gameplay: bool,
    forced: str | None = None,
    pcm: np.ndarray | None = None,
    pcm_start_s: float = 0.0,
) -> Analysis:
    """``pcm`` is 16 kHz mono sound from ``pcm_start_s`` covering the padded range, when the caller has
    it; otherwise the range's sound is decoded here."""
    geo = video_geometry(src.path)
    a = max(src.offset_s, start_s - PAD_S)
    b = min(src.offset_s + geo.duration, end_s + PAD_S)
    samples: list[tuple[float, list[FaceBox]]] = []
    patches: dict[tuple[int, int], np.ndarray] = {}
    stats, motion = [], []
    statter, meter = FrameStatter(), MotionMeter()

    def on_frame(_i: int, t: float, frame: np.ndarray) -> None:
        faces = detector.detect(frame, t)
        for j, face in enumerate(faces):
            patch = mouth_patch(frame, face)
            if patch is not None:
                patches[(len(samples), j)] = patch
        samples.append((t, faces))
        stats.append(statter(frame))
        motion.append((t, *meter(frame)))

    dec = analysis_decode(src, a, b, on_frame)
    times = [t for t, _ in samples]
    tracks = build_tracks(samples, dec.cuts)
    for tr in tracks:
        tr.mouth = mouth_activity(tr, patches)
    sound, sound_start = (clip_audio(src, a, b), a) if pcm is None else (pcm, pcm_start_s)
    loud = frame_loudness(sound, SR, times, start_s=sound_start) if len(sound) else np.zeros(len(times))
    shots = classify_shots(dec.cuts, tracks, stats, times, a, b)
    choice = _choose(shots, tracks, transcript, signals, start_s, end_s, gameplay_mode, has_gameplay, forced)
    cw, ch = crop_size(geo.width, geo.height, OUT_W, OUT_H)
    return Analysis(
        version=ANALYSIS_VERSION,
        key=analysis_key(src, start_s, end_s),
        start_s=a,
        end_s=b,
        width=geo.width,
        height=geo.height,
        fps=geo.fps,
        cuts=dec.cuts,
        shots=shots,
        tracks=tracks,
        turns=speaker_turns(tracks, loud, times, dec.cuts),
        motion=motion,
        quality=clip_quality(shots, tracks, upscale(cw, ch, OUT_W, OUT_H)[0]),
        layout=choice.kind,
        layout_reason=choice.reason,
    )


def relayout(
    analysis: Analysis,
    transcript: Transcript,
    signals: Signals | None,
    start_s: float,
    end_s: float,
    *,
    gameplay_mode: str,
    has_gameplay: bool,
    forced: str | None = None,
) -> Analysis:
    """``analysis`` with its layout chosen again, as a fresh analysis would choose it, for when the clip's
    layout, the gameplay mode or the library changed but the video did not."""
    choice = _choose(
        analysis.shots,
        analysis.tracks,
        transcript,
        signals,
        start_s,
        end_s,
        gameplay_mode,
        has_gameplay,
        forced,
    )
    return analysis.model_copy(update={"layout": choice.kind, "layout_reason": choice.reason})


def _choose(
    shots: list[Shot],
    tracks: list[Track],
    transcript: Transcript,
    signals: Signals | None,
    start_s: float,
    end_s: float,
    gameplay_mode: str,
    has_gameplay: bool,
    forced: str | None,
) -> LayoutChoice:
    return choose_layout(
        shots,
        tracks,
        words_per_s=words_per_s(transcript, start_s, end_s),
        subdued_share=subdued_share(transcript, signals, start_s, end_s),
        gameplay_mode=gameplay_mode,
        has_gameplay=has_gameplay,
        forced=forced,
    )
