"""The clips API (SPEC 14.4 and 14.5): a project's clips as their cards show them, one clip as its editor
does with the transcript around it, edits checked as they are saved, and the buttons: Render, Discard,
Restore, Shuffle and Download, and the Sound tab's voiceover."""

from __future__ import annotations

import io
import os
import shutil
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

import anyio.to_thread
from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException

from clipforge import repo, voiceover
from clipforge.app import fixed
from clipforge.edits import UNKNOWN, EditError
from clipforge.errors import ClipForgeError
from clipforge.server.errors import ApiError
from clipforge.server.media import NAME_MAX, attachment, download_name
from clipforge.server.uploads import receive_upload
from clipforge.server.views import (
    clean_file_name,
    clip_card,
    clip_detail,
    hook_title,
    suggestion_json,
    transcript_window,
)
from clipforge.trends import recommend

if TYPE_CHECKING:
    from clipforge.app import Engine
    from clipforge.models import ClipRow

DOWNLOAD_MAX = 40
CHOOSE = "Choose at least one clip to download."
TOO_MANY = f"Download at most {DOWNLOAD_MAX} clips at a time."
NOTHING_TO_DOWNLOAD = "None of those clips has a render to download yet."
ZIP_NAME = "ClipForge clips.zip"
CHUNK = 1 << 20

router = APIRouter(prefix="/api")


class ClipChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edits: dict[str, Any] | None = None
    range: Any = None  # [start, end] in seconds, or null to put the clip back; the engine checks it


def _engine(request: Request) -> Engine:
    return request.app.state.engine


def _clip(engine: Engine, clip_id: str) -> ClipRow:
    clip = repo.get_clip(engine.db(), clip_id)
    if clip is None:
        raise HTTPException(404)
    return clip


def _changes(body: ClipChanges | None) -> tuple[dict[str, Any], Any]:
    """The edits to save and where to move the clip: "keep" when ``range`` was left out."""
    if body is None:
        return {}, "keep"
    return body.edits or {}, body.range if "range" in body.model_fields_set else "keep"


def _refused(err: EditError) -> ApiError:
    """The 422 for an edit or a range that can't be used, naming its field in the request. Only a range
    is refused as "range" for a reason other than UNKNOWN, which is an edit called "range"."""
    field = "range" if err.field == "range" and err.message != UNKNOWN else f"edits.{err.field}"
    return ApiError(422, "invalid", err.message, field)


@router.get("/projects/{project_id}/clips")
def list_clips(project_id: str, request: Request, discarded: bool = False) -> dict[str, Any]:
    """The project's clips in rank order: those kept, or every one with ``discarded=1``."""
    engine = _engine(request)
    project = engine.project(project_id)
    if project is None:
        raise HTTPException(404)
    clips = [c for c in engine.clips(project_id) if discarded or c.discarded_at is None]
    return {"clips": [clip_card(engine, c, project) for c in clips]}


@router.get("/clips/download")  # ahead of /clips/{clip_id}, which would take "download" for an id
def download_clips(request: Request, ids: Annotated[list[str] | None, Query()] = None) -> StreamingResponse:
    """The current renders of up to 40 clips, in the order asked, as one zip of videos named for each
    clip's rank and hook title. ``ids`` is a comma-separated list, and may be given more than once; a
    clip without a render is left out."""
    asked = list(dict.fromkeys(cid.strip() for given in ids or [] for cid in given.split(",") if cid.strip()))
    if not asked:
        raise ApiError(422, "invalid", CHOOSE, "ids")
    if len(asked) > DOWNLOAD_MAX:
        raise ApiError(422, "invalid", TOO_MANY, "ids")
    files = _videos(_engine(request), asked)
    if not files:
        raise ApiError(409, "nothing_to_download", NOTHING_TO_DOWNLOAD)
    headers = {"Content-Disposition": attachment(ZIP_NAME)}
    return StreamingResponse(_zipped(files), media_type="application/zip", headers=headers)


@router.get("/clips/{clip_id}")
def get_clip(clip_id: str, request: Request) -> dict[str, Any]:
    engine = _engine(request)
    return clip_detail(engine, _clip(engine, clip_id))


@router.get("/clips/{clip_id}/transcript")
def get_transcript(clip_id: str, request: Request) -> dict[str, Any]:
    engine = _engine(request)
    return transcript_window(engine, _clip(engine, clip_id))


@router.get("/clips/{clip_id}/sounds")
def clip_sounds(
    clip_id: str,
    request: Request,
    platform: Annotated[Literal["tiktok", "instagram"] | None, Query()] = None,
    account_id: Annotated[str | None, Query(max_length=64)] = None,
) -> dict[str, Any]:
    """The tracks and sounds that suit the clip, best first (SPEC 13): your tracks, with your TikTok
    shortlist for TikTok or no platform, and the account's trending list for Instagram."""
    engine = _engine(request)
    found = recommend.suggestions(engine, _clip(engine, clip_id), platform=platform, account_id=account_id)
    return {"sounds": [suggestion_json(engine, s) for s in found]}


@router.patch("/clips/{clip_id}")
def change_clip(clip_id: str, body: ClipChanges, request: Request) -> dict[str, Any]:
    """Saves the edits (one set to null is taken off) and moves the clip (a null ``range`` puts it back),
    each checked first: one that can't be used is a 422 naming it, and nothing is saved."""
    engine = _engine(request)
    edits, range_ = _changes(body)
    with engine.lock:
        _clip(engine, clip_id)
        try:
            clip = engine.save_edits(clip_id, edits, range_)
        except EditError as err:
            raise _refused(err) from None
        return clip_detail(engine, clip)


@router.post("/clips/{clip_id}/render", status_code=202)
def render_clip(clip_id: str, request: Request, body: ClipChanges | None = None) -> dict[str, Any]:
    """Saves the edits and range given, as PATCH does, and renders the clip again ahead of the clips
    rendering on their own. Its current version stays until the new one is done."""
    engine = _engine(request)
    edits, range_ = _changes(body)
    with engine.lock:
        _clip(engine, clip_id)
        try:
            engine.render_clip(clip_id, edits, range_)
        except EditError as err:
            raise _refused(err) from None
        return clip_detail(engine, _clip(engine, clip_id))


@router.post("/clips/{clip_id}/voiceover", status_code=202)
async def add_voiceover(
    clip_id: str, request: Request, name: Annotated[str, Query(max_length=NAME_MAX)] = ""
) -> dict[str, Any]:
    """A voiceover for the clip: the body is the take the Sound tab recorded, or a recording file you chose,
    and ``name`` its file name. It is checked and kept as the clip's one take, set as its voiceover, and the
    clip renders again. A file with no sound ClipForge can read, a silent one, one of 3 minutes or more or a
    file over 50 MB is refused as "file", and nothing of it is kept."""
    engine = _engine(request)
    fixed(_clip(engine, clip_id))
    _, dot, ext = clean_file_name(name).rpartition(".")
    if not dot or ext.lower() not in voiceover.EXTS:
        raise ApiError(415, "not_audio", voiceover.VO_UNREADABLE, "file")
    length = request.headers.get("content-length", "")
    if length.isascii() and length.isdigit() and int(length) > voiceover.VO_MAX_BYTES:
        raise ApiError(413, "too_big", voiceover.VO_BIG, "file")
    floor = int(engine.settings.min_free_gb * 1e9)
    path = await receive_upload(
        request, engine.paths.uploads, name, allowed=voiceover.EXTS, floor_bytes=floor
    )
    try:
        await anyio.to_thread.run_sync(voiceover.take_recording, engine, clip_id, path)
    except ClipForgeError as err:
        if err.code == "not_found":
            raise HTTPException(404) from None
        if err.code == "too_big":
            raise ApiError(413, err.code, err.message, "file") from None
        if err.code in ("unreadable", "silent", "too_long"):
            raise ApiError(422, err.code, err.message, "file") from None
        raise
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)
    with engine.lock:
        return clip_detail(engine, _clip(engine, clip_id))


@router.delete("/clips/{clip_id}/voiceover", status_code=202)
def remove_voiceover(clip_id: str, request: Request) -> dict[str, Any]:
    """The clip's voiceover taken off and its take deleted, and the clip rendered again."""
    engine = _engine(request)
    with engine.lock:
        if not voiceover.remove_take(engine, clip_id):
            raise HTTPException(404)
        return clip_detail(engine, _clip(engine, clip_id))


@router.get("/clips/{clip_id}/voiceover/{file}")
def voiceover_file(clip_id: str, file: str, request: Request) -> FileResponse:
    """The clip's take, for the Sound tab's player."""
    path = voiceover.take_path(_engine(request), clip_id, file)
    if path is None:
        raise HTTPException(404)
    return FileResponse(path, media_type="audio/wav")


@router.post("/clips/{clip_id}/discard")
def discard_clip(clip_id: str, request: Request) -> dict[str, Any]:
    engine = _engine(request)
    with engine.lock:
        _clip(engine, clip_id)
        return clip_card(engine, engine.discard_clip(clip_id))


@router.post("/clips/{clip_id}/restore")
def restore_clip(clip_id: str, request: Request) -> dict[str, Any]:
    engine = _engine(request)
    with engine.lock:
        _clip(engine, clip_id)
        return clip_card(engine, engine.restore_clip(clip_id))


@router.post("/clips/{clip_id}/gameplay/shuffle", status_code=202)
def shuffle_gameplay(clip_id: str, request: Request) -> dict[str, Any]:
    """Another stretch of gameplay under the clip, rendered again."""
    engine = _engine(request)
    with engine.lock:
        _clip(engine, clip_id)
        return clip_detail(engine, engine.shuffle_gameplay(clip_id))


def _videos(engine: Engine, asked: list[str]) -> list[tuple[str, Path]]:
    """Each asked clip's current video with its name in the zip; a name already taken gets " 2", " 3"."""
    conn = engine.db()
    out: list[tuple[str, Path]] = []
    taken: set[str] = set()
    for cid in asked:
        clip = repo.get_clip(conn, cid)
        render = repo.get_render(conn, clip.current_render_id) if clip and clip.current_render_id else None
        if clip is None or render is None or not Path(render.path).is_file():
            continue
        path = Path(render.path)
        title = hook_title(clip) or "clip"
        name = base = download_name(f"{clip.rank} {title}" if clip.rank is not None else title, path)
        n = 1
        while name.lower() in taken:
            n += 1
            stem, tail = base[: -len(path.suffix)], f" {n}{path.suffix}"
            name = stem[: NAME_MAX - len(tail)].rstrip(" .") + tail
        taken.add(name.lower())
        out.append((name, path))
    return out


class _Pipe(io.RawIOBase):
    """Where the zip is written as it is made, for the response to send on. It can't seek, so each video's
    sizes follow its data."""

    def __init__(self) -> None:
        self.made = bytearray()

    def writable(self) -> bool:
        return True

    def write(self, b: Any) -> int:
        self.made += b
        return len(b)

    def take(self) -> bytes:
        out = bytes(self.made)
        self.made.clear()
        return out


def _zipped(files: list[tuple[str, Path]]) -> Iterator[bytes]:
    """The zip of ``files`` as it is made, a piece at a time; the videos are stored as they are (they don't
    compress). A video that went away since is left out."""
    pipe = _Pipe()
    with zipfile.ZipFile(pipe, mode="w", compression=zipfile.ZIP_STORED) as zf:
        for name, path in files:
            try:
                src = path.open("rb")
            except OSError:
                continue
            seen = os.fstat(src.fileno())
            info = zipfile.ZipInfo(name, date_time=time.localtime(seen.st_mtime)[:6])
            info.file_size = seen.st_size  # so a video is written as ZIP64 only when it is over 4 GB
            with src, zf.open(info, mode="w") as dest:
                while chunk := src.read(CHUNK):
                    dest.write(chunk)
                    yield pipe.take()
            yield pipe.take()
    yield pipe.take()
