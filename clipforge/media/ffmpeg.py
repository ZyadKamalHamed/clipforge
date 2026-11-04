"""Running ffmpeg and ffprobe, reading what a file holds, and making the speech audio (SPEC 7.2)."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from clipforge import children
from clipforge.errors import ToolError, ToolMissing

HOMEBREW_BIN = Path("/opt/homebrew/bin")  # an app started from the Dock has no Homebrew on its PATH
INSTALL = "Install ffmpeg with Homebrew: brew install ffmpeg"
STDERR_LINES = 20
UNREADABLE = "ClipForge can't read this video file. It may be damaged or in a format ffmpeg doesn't know."
QUIET = ["-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
SPEECH_WAV = ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le"]


def _missing(name: str, details: str | None = None) -> ToolMissing:
    return ToolMissing(f"{name}_missing", f"{name} isn't installed. {INSTALL}", details=details)


def _find(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    fallback = HOMEBREW_BIN / name
    if fallback.is_file() and os.access(fallback, os.X_OK):
        return str(fallback)
    raise _missing(name)


def ffmpeg_bin() -> str:
    return _find("ffmpeg")


def ffprobe_bin() -> str:
    return _find("ffprobe")


def _tail(stderr: bytes | str | None) -> str:
    if not stderr:
        return ""
    text = stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else stderr
    return "\n".join(text.splitlines()[-STDERR_LINES:])


def run(
    args: list[str], *, timeout_s: float | None = None, stdin: bytes | None = None
) -> subprocess.CompletedProcess:
    """Run a tool with its output captured, fed ``stdin`` when given and nothing otherwise. Failing or
    overrunning raises ToolError whose details end with the last 20 lines the tool wrote to stderr."""
    name = Path(args[0]).name
    feed = {"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}
    try:
        proc = children.run(args, capture_output=True, timeout=timeout_s, **feed)
    except FileNotFoundError as err:
        raise _missing(name, str(err)) from err
    except subprocess.TimeoutExpired as err:
        raise ToolError(
            f"{name}_timeout",
            f"{name} took too long and was stopped. Try again.",
            details=f"{shlex.join(args)}\nstopped after {timeout_s} s\n{_tail(err.stderr)}",
        ) from err
    if proc.returncode != 0:
        raise ToolError(
            f"{name}_failed",
            f"{name} hit an error working on this video. Try again; the details show what it said.",
            details=f"{shlex.join(args)}\nexit code {proc.returncode}\n{_tail(proc.stderr)}",
        )
    return proc


HDR_TRANSFERS = ("smpte2084", "arib-std-b67")  # PQ and HLG


@dataclass(frozen=True)
class MediaInfo:
    duration_s: float
    width: int  # as displayed: a 90 degree display rotation swaps the stored width and height
    height: int
    fps: float
    vcodec: str | None
    acodec: str | None
    has_video: bool
    has_audio: bool
    size_bytes: int
    pix_fmt: str | None = None
    sample_rate: int = 0
    channels: int = 0
    transfer: str | None = None  # the picture's transfer tag, None when it has none
    video_duration_s: float = 0.0  # each stream's own length, 0 when the file doesn't say
    audio_duration_s: float = 0.0
    frames: int = 0  # the picture's frame count, 0 when the file doesn't say
    nominal_fps: float = 0.0  # the rate its frames are timed at, which an MP4's longer first frame leaves be

    @property
    def hdr(self) -> bool:
        return self.transfer in HDR_TRANSFERS


def _number(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):  # "N/A" or missing
        return 0.0


def _rate(value: str | None) -> float:
    try:
        return float(Fraction(value))  # type: ignore[arg-type]
    except (TypeError, ValueError, ZeroDivisionError):  # missing, or "0/0"
        return 0.0


def _rotation(stream: dict) -> int:
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            return round(_number(side["rotation"]))
    return round(_number((stream.get("tags") or {}).get("rotate")))


def _first(streams: list[dict], kind: str) -> dict | None:
    for stream in streams:
        if stream.get("codec_type") == kind and not (stream.get("disposition") or {}).get("attached_pic"):
            return stream  # cover art in an audio file is not video
    return None


def probe(path: Path) -> MediaInfo:
    path = Path(path).absolute()  # a name starting with "-" must not read as an option
    args = [ffprobe_bin(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams"]
    try:
        data = json.loads(run([*args, str(path)], timeout_s=60).stdout)
    except (ToolError, ValueError) as err:
        details = getattr(err, "details", None) or str(err)
        raise ToolError("unreadable_media", UNREADABLE, details=details) from err
    streams = data.get("streams") or []
    video, audio = _first(streams, "video"), _first(streams, "audio")
    if video is None and audio is None:
        raise ToolError("unreadable_media", UNREADABLE, details=f"no audio or video streams in {path}")
    width = int(video.get("width") or 0) if video else 0
    height = int(video.get("height") or 0) if video else 0
    if video and _rotation(video) % 180 == 90:
        width, height = height, width
    duration = _number((data.get("format") or {}).get("duration"))
    if not duration:
        duration = max((_number(s.get("duration")) for s in streams), default=0.0)
    return MediaInfo(
        duration_s=duration,
        width=width,
        height=height,
        fps=(_rate(video.get("avg_frame_rate")) or _rate(video.get("r_frame_rate"))) if video else 0.0,
        vcodec=video.get("codec_name") if video else None,
        acodec=audio.get("codec_name") if audio else None,
        has_video=video is not None,
        has_audio=audio is not None,
        size_bytes=path.stat().st_size,
        pix_fmt=video.get("pix_fmt") if video else None,
        sample_rate=round(_number(audio.get("sample_rate"))) if audio else 0,
        channels=int(audio.get("channels") or 0) if audio else 0,
        transfer=video.get("color_transfer") if video else None,
        video_duration_s=_number(video.get("duration")) if video else 0.0,
        audio_duration_s=_number(audio.get("duration")) if audio else 0.0,
        frames=int(_number(video.get("nb_frames"))) if video else 0,
        nominal_fps=_rate(video.get("r_frame_rate")) if video else 0.0,
    )


def to_wav16k(src: Path, dst: Path) -> Path:
    """16 kHz mono 16 bit WAV for speech to text, written whole or not at all."""
    dst = Path(dst).absolute()
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".tmp.wav")
    try:
        run([ffmpeg_bin(), *QUIET, "-i", str(Path(src).absolute()), *SPEECH_WAV, str(tmp)])
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(dst)
    return dst
