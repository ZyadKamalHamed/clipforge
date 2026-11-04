"""A voiceover for one clip (phase 5g Task 4): a take recorded in the Sound tab or a recording file Zyad
picks, checked, kept as 48 kHz stereo 16-bit WAV in the clip's ``voiceover`` folder under a name taken from
what is in it, and set as the clip's ``voiceover`` edit, which renders the clip again. One take is kept for
each clip; a new one replaces it. A voiceover gets no captions: there is no transcript of it."""

from __future__ import annotations

import hashlib
import io
import shutil
import tempfile
import wave
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from clipforge import repo
from clipforge.errors import ClipForgeError, ToolError
from clipforge.render.audio import SILENT_LUFS, SR, decode_take, loudness

if TYPE_CHECKING:
    from clipforge.app import Engine

VO_MAX_S = 180.0
VO_SLACK_S = 0.5  # the recorder stops itself at 3:00, and its take ends a moment after
VO_MAX_BYTES = 50_000_000
EXTS = frozenset({"webm", "ogg", "m4a", "mp4", "mp3", "wav", "aac", "caf"})
FOLDER = "voiceover"
VO_UNREADABLE = (
    "That recording has no sound ClipForge can read. Record again, or pick an audio file such as M4A, MP3 "
    "or WAV."
)
VO_SILENT = "That recording is silent. Check which microphone Chrome is using, then record again."
VO_LONG = "Keep a voiceover under 3 minutes."
VO_BIG = "That file is bigger than 50 MB. Pick a smaller one, such as an M4A or MP3."
NO_CLIP = "That clip isn't here any more."


def take_folder(engine: Engine, clip_id: str) -> Path | None:
    """Where the clip's take is kept, None when there is no such clip."""
    clip = repo.get_clip(engine.db(), clip_id)
    return None if clip is None else engine.paths.project(clip.project_id).clip_dir(clip_id) / FOLDER


def take_recording(engine: Engine, clip_id: str, upload: Path) -> dict[str, Any]:
    """``upload`` checked, kept as the clip's take and set as its voiceover edit, which queues a render:
    ``{"file": name, "duration_s": length}``. Raises ClipForgeError with the sentence to show for a file
    with no sound ClipForge can read ("unreadable"), a silent one ("silent"), one longer than 3 minutes
    ("too_long"; a take a moment past is cut at 3:00) or a file over 50 MB ("too_big"); nothing is kept
    then. ``upload`` itself is left for the caller."""
    folder = take_folder(engine, clip_id)
    if folder is None:
        raise ClipForgeError("not_found", NO_CLIP)
    if upload.stat().st_size > VO_MAX_BYTES:
        raise ClipForgeError("too_big", VO_BIG)
    try:
        pcm = decode_take(upload, VO_MAX_S + 1.0)
    except ToolError:
        raise ClipForgeError("unreadable", VO_UNREADABLE) from None
    if len(pcm) == 0:
        raise ClipForgeError("unreadable", VO_UNREADABLE)
    if len(pcm) > (VO_MAX_S + VO_SLACK_S) * SR:
        raise ClipForgeError("too_long", VO_LONG)
    pcm = pcm[: int(VO_MAX_S * SR)]
    if loudness(pcm) < SILENT_LUFS:
        raise ClipForgeError("silent", VO_SILENT)
    data = _wav(pcm)
    name = hashlib.sha256(data).hexdigest()[:10] + ".wav"
    folder.parent.mkdir(parents=True, exist_ok=True)
    fresh = Path(tempfile.mkdtemp(prefix=".voiceover-", dir=folder.parent))
    (fresh / name).write_bytes(data)
    with engine.lock:
        old = folder.with_name(f".{FOLDER}-old")
        shutil.rmtree(old, ignore_errors=True)
        if folder.exists():
            folder.rename(old)
        fresh.rename(folder)
        shutil.rmtree(old, ignore_errors=True)
        engine.render_clip(clip_id, {"voiceover": name})
    return {"file": name, "duration_s": round(len(pcm) / SR, 3)}


def remove_take(engine: Engine, clip_id: str) -> bool:
    """The clip's voiceover edit taken off and its take deleted, which queues a render. False when there is
    no such clip."""
    folder = take_folder(engine, clip_id)
    if folder is None:
        return False
    with engine.lock:
        engine.render_clip(clip_id, {"voiceover": None, "voiceover_at": None})
        shutil.rmtree(folder, ignore_errors=True)
    return True


def take_path(engine: Engine, clip_id: str, file: str) -> Path | None:
    """The clip's take named ``file``, None when the clip has no take of that name."""
    folder = take_folder(engine, clip_id)
    if folder is None or not folder.is_dir():
        return None
    return next((p for p in folder.iterdir() if p.name == file and p.is_file()), None)


def _wav(pcm: np.ndarray) -> bytes:
    """``pcm`` (frames x 2 float) as a 48 kHz stereo 16-bit WAV."""
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(np.round(np.clip(pcm, -1.0, 1.0) * 32767).astype("<i2").tobytes())
    return out.getvalue()
