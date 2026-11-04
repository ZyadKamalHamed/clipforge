"""What the pipeline's stages share (SPEC 7): writing a file so a reader never sees half of it, reading
what an earlier stage wrote, and telling a finished download from one still on its way. Each stage is in
its own module: ingest, asr, signals and brain."""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from clipforge.errors import PipelineError
from clipforge.ingest.youtube_signals import CapEvent, CapWord, parse_json3
from clipforge.models import Comment, Signals, SourceMeta, Transcript
from clipforge.paths import ProjectPaths

AS_IS_POINTER = "To post the whole video, add it again with Post as it is on in Create's Options."

log = logging.getLogger(__name__)


def write_text(path: Path, text: str) -> Path:
    """``text`` at ``path``, written to a temp file beside it and moved into place, so a reader sees the
    old file or the new one, and a crash never leaves half of one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def write_json(path: Path, data: Any) -> Path:
    return write_text(path, json.dumps(data, ensure_ascii=False))


def complete_file(folder: Path, stem: str) -> Path | None:
    """The finished file called ``stem`` in ``folder``, like ``audio.m4a`` for "audio". Names with more
    to them never match: yt-dlp's ``.part`` and ``.ytdl`` files, its format parts (``video.f137.mp4``)
    and hidden temp files are all still on their way."""
    if not folder.is_dir():
        return None
    name = re.compile(rf"{re.escape(stem)}\.[A-Za-z0-9]+")
    found = sorted(p for p in folder.iterdir() if p.is_file() and name.fullmatch(p.name))
    return found[0] if found else None


def missing(path: Path) -> PipelineError:
    return PipelineError(
        "missing_file",
        f"Part of this project's work is missing ({path.name}), so ClipForge can't carry on. "
        "Make the clips again as a new project.",
    )


def _read(path: Path) -> str:
    if not path.is_file():
        raise missing(path)
    return path.read_text(encoding="utf-8")


def read_meta(pp: ProjectPaths) -> SourceMeta:
    return SourceMeta.model_validate_json(_read(pp.meta_json))


def read_transcript(pp: ProjectPaths) -> Transcript:
    return Transcript.model_validate_json(_read(pp.transcript_json))


def read_signals(pp: ProjectPaths) -> Signals:
    return Signals.model_validate_json(_read(pp.signals_json))


def read_captions(pp: ProjectPaths) -> tuple[list[CapWord], list[CapEvent], str | None]:
    """YouTube's caption words and sound events, and the track they came from (``en-orig`` from
    ``subs.en-orig.json3``). A file that can't be read counts as no captions: it came from the web."""
    path = next(iter(sorted(pp.root.glob(pp.captions_glob))), None)
    if path is None:
        return [], [], None
    track = path.name.removeprefix("subs.").removesuffix(".json3")
    try:
        words, events = parse_json3(path)
    except Exception as err:  # untrusted data: any failure to read it means there are no captions
        log.warning("the captions file %s can't be read (%s), so it is left out", path.name, err)
        return [], [], track
    return words, events, track


def read_comments(pp: ProjectPaths) -> list[Comment]:
    """The comments saved by ingest.extras; none when there is no file or it can't be read."""
    if not pp.comments_json.is_file():
        return []
    try:
        return [Comment.model_validate(c) for c in json.loads(pp.comments_json.read_text(encoding="utf-8"))]
    except (OSError, ValueError, TypeError) as err:
        log.warning("the comments file can't be read (%s), so the comments are left out", err)
        return []


def job_result(conn: sqlite3.Connection, pid: str, kind: str) -> dict[str, Any]:
    """What the newest finished job of ``kind`` gave back for this project; empty when there is none."""
    row = conn.execute(
        "SELECT result_json FROM jobs WHERE ref_id = ? AND kind = ? AND status = 'done' "
        "ORDER BY id DESC LIMIT 1",
        (pid, kind),
    ).fetchone()
    return json.loads(row["result_json"]) if row and row["result_json"] else {}
