"""Test media made on the spot with ffmpeg and numpy, so no binary fixtures live in the repo."""

from __future__ import annotations

import itertools
import shutil
import subprocess
import wave
from collections.abc import Iterable, Sequence
from pathlib import Path

import numpy as np


def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"


def _ffmpeg_run(*args: str) -> None:
    subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args], check=True)


def make_test_video(
    path: Path,
    seconds: float = 2.0,
    size: str = "640x360",
    fps: int = 30,
    tone_hz: int = 440,
    *,
    rotation: int | None = None,
    audio: bool = True,
) -> Path:
    """An H.264 and AAC test pattern with a steady tone, or no sound at all with ``audio=False``. ``rotation``
    adds a display matrix, as phones do."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    target = path.with_suffix(".plain.mp4") if rotation else path
    tone = ["-f", "lavfi", "-i", f"sine=frequency={tone_hz}:sample_rate=48000:duration={seconds}"]
    _ffmpeg_run(
        "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}:duration={seconds}",
        *(tone if audio else []),
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        *(["-c:a", "aac", "-shortest"] if audio else []), str(target),
    )  # fmt: skip
    if rotation:
        _ffmpeg_run("-display_rotation", str(rotation), "-i", str(target), "-c", "copy", str(path))
        target.unlink()
    return path


def make_speech_video(path: Path, text: str, *, words_per_minute: int = 150) -> Path:
    """A small test pattern with macOS ``say`` reading ``text`` as its sound, as long as the speech."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    speech = path.with_suffix(".aiff")
    subprocess.run(["say", "-r", str(words_per_minute), "-o", str(speech), text], check=True)
    _ffmpeg_run(
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=10", "-i", str(speech),
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(path),
    )  # fmt: skip
    speech.unlink()
    return path


def make_block_talk_video(path: Path, text: str, *, words_per_minute: int = 150) -> Path:
    """A dark 1280x720 frame at 30 fps with a warm block moving across it (bright, but not a face) and
    macOS ``say`` reading ``text`` as its sound, as long as the speech. The dark frame lets the white of a
    hook's box and of captions stand out."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    speech = path.with_suffix(".aiff")
    subprocess.run(["say", "-r", str(words_per_minute), "-o", str(speech), text], check=True)
    _ffmpeg_run(
        "-f", "lavfi", "-i", "color=c=0x14171c:s=1280x720:r=30",
        "-f", "lavfi", "-i", "color=c=0xc8785a:s=200x200:r=30",
        "-i", str(speech),
        "-filter_complex", "[0:v][1:v]overlay=x='540+380*sin(t*0.6)':y='200+60*sin(t*0.9)'[v]",
        "-map", "[v]", "-map", "2:a",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(path),
    )  # fmt: skip
    speech.unlink()
    return path


def make_test_audio(
    path: Path, seconds: float = 3.0, tone_hz: int = 440, *, sample_rate: int = 44100, channels: int = 1
) -> Path:
    """A steady tone as an audio file, its format from the extension (``.m4a`` is AAC, ``.wav`` is 16-bit
    PCM, ``.mp3`` MP3), as a music track is."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    codec = {".m4a": "aac", ".aac": "aac", ".wav": "pcm_s16le", ".mp3": "libmp3lame", ".flac": "flac"}
    _ffmpeg_run(
        "-f", "lavfi", "-i", f"sine=frequency={tone_hz}:sample_rate={sample_rate}:duration={seconds}",
        "-ac", str(channels), "-c:a", codec[path.suffix.lower()], str(path),
    )  # fmt: skip
    return path


def make_tone_wav(path: Path, seconds: float, tone_hz: int = 440) -> Path:
    """A 16 kHz mono s16 WAV of a steady tone, made by ffmpeg so even a long one is cheap."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _ffmpeg_run(
        "-f", "lavfi", "-i", f"sine=frequency={tone_hz}:sample_rate=16000:duration={seconds}",
        "-ac", "1", "-c:a", "pcm_s16le", str(path),
    )  # fmt: skip
    return path


def make_wav(path: Path, segments: list[tuple[float, float]], sr: int = 16000) -> Path:
    """16 kHz mono s16 WAV: each segment is (seconds, amplitude) of a 440 Hz tone, amplitude 0 is silence."""
    parts, start = [], 0
    for seconds, amplitude in segments:
        count = round(seconds * sr)
        t = (np.arange(count) + start) / sr
        parts.append(amplitude * np.sin(2 * np.pi * 440 * t))
        start += count
    samples = np.concatenate(parts) if parts else np.zeros(0)
    pcm = np.clip(np.round(samples * 32767), -32768, 32767).astype("<i2")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sr)
        out.writeframes(pcm.tobytes())
    return path


def write_wav(path: Path, pcm: np.ndarray, sr: int = 48000) -> Path:
    """A 16 bit WAV of ``pcm``: frames x channels of floats from -1 to 1."""
    pcm = np.asarray(pcm, np.float64).reshape(len(pcm), -1)
    data = np.clip(np.round(pcm * 32767), -32768, 32767).astype("<i2")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(data.shape[1])
        out.setsampwidth(2)
        out.setframerate(sr)
        out.writeframes(data.tobytes())
    return path


def video_from_frames(
    path: Path,
    frames: Iterable[np.ndarray],
    rate: str = "30",
    *,
    audio: Path | None = None,
    matrix: str = "bt709",
    tagged: bool = True,
    quality: Sequence[str] = ("-crf", "10"),
) -> Path:
    """An H.264 MP4 of ``frames`` (each h x w x 3 uint8 BGR) at ``rate`` frames a second ("60000/1001" and
    the like) in ``matrix`` colours, tagged with them unless ``tagged`` is off, and ``audio`` (any file ffmpeg
    reads) as AAC when given."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = iter(frames)
    first = next(frames)
    h, w = first.shape[:2]
    args = [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    args += ["-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-framerate", rate, "-i", "pipe:0"]
    if audio is not None:
        args += ["-i", str(audio), "-map", "0:v", "-map", "1:a", "-c:a", "aac"]
    args += ["-vf", f"scale=out_color_matrix={matrix}:out_range=tv,format=yuv420p"]
    args += ["-c:v", "libx264", "-preset", "ultrafast", *quality]
    if tagged:
        args += ["-colorspace", {"bt709": "bt709", "bt601": "smpte170m"}[matrix], "-color_range", "tv"]
    else:  # 2 is "unspecified" for all three
        args += [
            "-bsf:v",
            "h264_metadata=colour_primaries=2:transfer_characteristics=2:matrix_coefficients=2",
        ]
    proc = subprocess.Popen([*args, str(path)], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for frame in itertools.chain([first], frames):
            proc.stdin.write(np.ascontiguousarray(frame, np.uint8).tobytes())
        proc.stdin.close()
    except BrokenPipeError:
        pass
    err = proc.stderr.read()
    if proc.wait() != 0:
        raise RuntimeError(err.decode("utf-8", "replace"))
    return path
