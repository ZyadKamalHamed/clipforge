"""The clip's cover (SPEC 7.10 step 5): the rendered frame with the largest face inside the safe zone, from
the clip's first half while the hook title shows in full and covers no face, so the frame Instagram takes at
the cover offset carries the title just as ``cover.jpg`` does. ``thumb.jpg`` is the same picture, small, for
the gallery."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from clipforge.errors import ToolError
from clipforge.media.ffmpeg import QUIET, ffmpeg_bin, ffprobe_bin, probe, run
from clipforge.render.edl import EDL, EPS, overlap
from clipforge.render.hook import HOOK_FADE_S, below_forehead
from clipforge.render.sprites import SAFE_BOTTOM, SAFE_LEFT, SAFE_RIGHT, SAFE_TOP

THUMB_W = 360
COVER_QUALITY, THUMB_QUALITY = 92, 85
NO_FRAME = "ClipForge couldn't read a frame of the clip for its cover."
NOT_SAVED = "ClipForge couldn't save the clip's cover picture."
TO_RGB = ["-sws_flags", "accurate_rnd+full_chroma_int"]  # the fast default comes out 2 levels dark


def make_cover(video: Path, edl: EDL, cover: Path, thumb: Path) -> float:
    """Write ``cover`` and ``thumb`` from the ``video`` rendered from ``edl`` and return when the cover's
    frame shows, in seconds of the file's own timing: the middle of the frame, for Instagram's cover
    offset."""
    return cover_at(video, cover_frame(edl), cover, thumb)


def cover_at(video: Path, frame: int, cover: Path, thumb: Path) -> float:
    """``make_cover`` from frame ``frame`` of ``video``, or its last when the file ends first: a video
    posted as it is takes its first."""
    info = probe(video)
    frames = frame_times(video)
    if not frames:
        raise ToolError("cover_failed", NO_FRAME, details=str(video))
    k = min(frame, len(frames) - 1)  # the file may end before its edit list does
    start, length = frames[k]
    seek = (frames[k - 1][0] + start) / 2 if k else 0.0  # the first frame at or after it is frame k
    args = [*TO_RGB, "-map", "0:v:0", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    raw = run([ffmpeg_bin(), *QUIET, "-ss", f"{seek:.6f}", "-i", str(Path(video).absolute()), *args]).stdout
    w, h = info.width, info.height
    if len(raw) != w * h * 3:
        raise ToolError("cover_failed", NO_FRAME, details=f"{len(raw)} bytes for a {w} x {h} frame")
    picture = np.frombuffer(raw, np.uint8).reshape(h, w, 3)
    small = cv2.resize(picture, (THUMB_W, round(h * THUMB_W / w)), interpolation=cv2.INTER_AREA)
    _save(picture, cover, COVER_QUALITY)
    _save(small, thumb, THUMB_QUALITY)
    end = frames[k + 1][0] if k + 1 < len(frames) else start + length
    return (start + end) / 2


def cover_frame(edl: EDL) -> int:
    """The frame of the clip with the largest face fully inside the safe zone in its first half, at a time
    the hook title shows in full and covers no face (the faces are written down every few frames); the
    first frame when there is none."""
    faces: dict[float, list[tuple[int, int, int, int]]] = {}
    for span in edl.spans:
        for t, *face in span.faces:
            faces.setdefault(t, []).append(tuple(face))
    hook = edl.hook
    best_t, best = 0.0, 0
    for t in sorted(faces):
        if t >= edl.duration / 2:
            continue
        if hook and (
            t > hook["end"] - HOOK_FADE_S + EPS
            or any(overlap(below_forehead(f), hook["box"]) for f in faces[t])
        ):
            continue
        area = max((w * h for x, y, w, h in faces[t] if _inside(x, y, w, h)), default=0)
        if area > best:
            best_t, best = t, area
    return round(best_t * edl.fps)


def frame_times(video: Path) -> list[tuple[float, float]]:
    """When each frame of ``video`` starts and how long it lasts, in the file's own timing, in order."""
    args = ["-select_streams", "v:0", "-show_entries", "packet=pts_time,duration_time", "-of", "csv=p=0"]
    out = run([ffprobe_bin(), "-v", "error", *args, str(Path(video).absolute())]).stdout.decode()
    times = []
    for line in out.split():
        pts, _, length = line.partition(",")
        try:
            start = float(pts)
        except ValueError:  # a frame with no time
            continue
        try:
            times.append((start, float(length)))
        except ValueError:
            times.append((start, 0.0))
    return sorted(times)


def _inside(x: int, y: int, w: int, h: int) -> bool:
    return x >= SAFE_LEFT and y >= SAFE_TOP and x + w <= SAFE_RIGHT and y + h <= SAFE_BOTTOM


def _save(picture: np.ndarray, path: Path, quality: int) -> None:
    ok, data = cv2.imencode(".jpg", picture, [cv2.IMWRITE_JPEG_QUALITY, quality])
    try:
        if not ok:
            raise OSError("the JPEG encoder failed")
        Path(path).write_bytes(data.tobytes())
    except OSError as exc:
        raise ToolError("cover_failed", NOT_SAVED, details=str(exc)) from exc
