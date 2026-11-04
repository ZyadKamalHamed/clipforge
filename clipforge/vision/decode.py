"""Decoding a clip's range for analysis (SPEC 7.6). One ffmpeg run gives small frames at 10 fps on a pipe
while scdet scores every frame of a 480 px copy for scene cuts. Frames go to a callback one at a time, so a
long clip never sits in memory."""

from __future__ import annotations

import json
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from clipforge import children
from clipforge.errors import ToolError
from clipforge.media.ffmpeg import ffmpeg_bin, ffprobe_bin
from clipforge.vision.scenes import merge_cuts, parse_scdet

ANALYSIS_FPS = 10.0
ANALYSIS_SIZE = 640  # the long side of an analysis frame


@dataclass(frozen=True)
class ClipSource:
    """A file holding the clip's range: the whole video (offset 0) or a section download that starts
    ``offset_s`` into it."""

    path: Path
    offset_s: float = 0.0

    def file_time(self, t: float) -> float:
        return t - self.offset_s


@dataclass(frozen=True)
class Geometry:
    width: int  # as displayed, after any rotation
    height: int
    fps: float
    duration: float  # of the file, 0 when unknown


@dataclass
class Decoded:
    times: list[float]  # source seconds of each frame given to the callback
    cuts: list[float]  # source seconds, merged
    geometry: Geometry
    size: tuple[int, int]  # an analysis frame's width and height


OnFrame = Callable[[int, float, np.ndarray], None]  # (index, source time, BGR frame)


def _rate(text: str | None) -> float | None:
    if not text or "/" not in text:
        return None
    num, den = text.split("/", 1)
    try:
        value = float(num) / float(den)
    except (ValueError, ZeroDivisionError):
        return None
    return value if value > 0 else None


def video_geometry(path: Path) -> Geometry:
    args = [
        *(str(ffprobe_bin()), "-v", "error", "-select_streams", "v:0", "-show_entries"),
        "stream=width,height,avg_frame_rate,r_frame_rate:stream_side_data=rotation:format=duration",
        *("-of", "json", str(path)),
    ]
    try:
        out = children.run(args, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ToolError("probe_failed", "Couldn't read the video file.", details=str(exc)) from exc
    try:
        data = json.loads(out.stdout or "{}")
        stream = data["streams"][0]
        width, height = int(stream["width"]), int(stream["height"])
    except (ValueError, KeyError, IndexError) as exc:
        raise ToolError(
            "probe_failed", "The video file has no picture ClipForge can read.", details=out.stderr[-500:]
        ) from exc
    rotation = 0
    for side in stream.get("side_data_list", []):
        if "rotation" in side:
            rotation = int(float(side["rotation"]))
    if abs(rotation) % 180 == 90:
        width, height = height, width
    fps = _rate(stream.get("avg_frame_rate")) or _rate(stream.get("r_frame_rate")) or 30.0
    duration = float(data.get("format", {}).get("duration") or 0.0)
    return Geometry(width, height, fps, duration)


def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def analysis_size(width: int, height: int, long_side: int = ANALYSIS_SIZE) -> tuple[int, int]:
    if width >= height:
        return long_side, _even(long_side * height / width)
    return _even(long_side * width / height), long_side


def analysis_decode(
    src: ClipSource,
    start_s: float,
    end_s: float,
    on_frame: OnFrame,
    *,
    fps: float = ANALYSIS_FPS,
    long_side: int = ANALYSIS_SIZE,
) -> Decoded:
    """Decode ``start_s`` to ``end_s`` (source seconds, clamped to the file) into frames whose long side is
    ``long_side``, upright as displayed, and find the scene cuts in the same pass."""
    geo = video_geometry(src.path)
    file_start = max(0.0, src.file_time(start_s))
    file_end = src.file_time(end_s)
    if geo.duration > 0:
        file_end = min(file_end, geo.duration)
    if file_end - file_start < 1 / fps:
        raise ToolError("empty_range", "This clip's time range is outside the video file.")
    w, h = analysis_size(geo.width, geo.height, long_side)
    first = src.offset_s + file_start  # the source time of the first frame
    times: list[float] = []
    with tempfile.TemporaryDirectory(prefix="clipforge-decode-") as tmp:
        scores_file = Path(tmp) / "scores.txt"
        err_file = Path(tmp) / "err.txt"
        graph = (
            f"[0:v]split=2[a][b];[a]fps={fps:g},scale={w}:{h}:flags=area,format=bgr24[frames];"
            f"[b]scale=480:-2,scdet,metadata=mode=print:key=lavfi.scd.score:file={scores_file}[cuts]"
        )
        args = [
            *(str(ffmpeg_bin()), "-hide_banner", "-nostdin", "-loglevel", "error"),
            *("-ss", f"{file_start:.3f}", "-t", f"{file_end - file_start:.3f}", "-i", str(src.path)),
            *("-filter_complex", graph, "-map", "[frames]", "-f", "rawvideo", "pipe:1"),
            *("-map", "[cuts]", "-f", "null", "-"),
        ]
        frame_bytes = w * h * 3
        with err_file.open("wb") as err:
            proc = children.popen(args, stdout=subprocess.PIPE, stderr=err)
            assert proc.stdout is not None
            try:
                while len(buf := proc.stdout.read(frame_bytes)) == frame_bytes:
                    t = round(first + len(times) / fps, 6)
                    on_frame(len(times), t, np.frombuffer(buf, np.uint8).reshape(h, w, 3))
                    times.append(t)
                code = proc.wait(timeout=60)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
                proc.stdout.close()
        if code != 0:
            raise ToolError(
                "decode_failed",
                "Couldn't decode this clip's part of the video.",
                details=err_file.read_text(errors="replace")[-800:],
            )
        text = scores_file.read_text() if scores_file.exists() else ""
    return Decoded(times, merge_cuts(parse_scdet(text, offset_s=first)), geo, (w, h))
