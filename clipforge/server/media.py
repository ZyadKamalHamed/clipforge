"""The media the window plays and shows (SPEC 17): the clips' versions with their covers and thumbnails,
the sources' thumbnails, the gameplay and music library, the screenshots a post's log took and the
accounts' pictures, straight from the home. Only the paths on the list are served, and only when no symlink
leads there; anything else is a bare 404. A log's saved pages are never served, as they may hold the
account's own details."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response
from starlette.exceptions import HTTPException

from clipforge.ingest.thumbs import ensure_thumb

if TYPE_CHECKING:
    from clipforge.app import Engine

ID = r"[A-Za-z0-9_-]+"
VERSION_FILE = re.compile(rf"projects/{ID}/clips/{ID}/v[1-9][0-9]*/(?:clip\.mp4|cover\.jpg|thumb\.jpg)")
ALLOWED = re.compile(
    rf"{VERSION_FILE.pattern}|projects/{ID}/source/thumb\.jpg|library/gameplay/{ID}\.(?:mp4|mov|mkv|webm|m4v)"
    rf"|library/music/{ID}\.(?:mp3|m4a|aac|wav|aif|aiff|flac|mp4|m4v|mov)"
    rf"|logs/posts/{ID}/[0-9]{{2}}-{ID}\.png|accounts/{ID}/avatar\.png"
)
SOURCE_THUMB = re.compile(rf"projects/({ID})/source/thumb\.jpg")
TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/x-m4v",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".wav": "audio/wav",
    ".aif": "audio/aiff",
    ".aiff": "audio/aiff",
    ".flac": "audio/flac",
    ".jpg": "image/jpeg",
    ".png": "image/png",
}
IMMUTABLE = "private, max-age=31536000, immutable"  # a version's folder never changes once it is made
NO_CACHE = "private, no-cache"
NAME_MAX = 80

router = APIRouter()


def media_target(home: Path, rel: str) -> Path | None:
    """``home/rel`` when ``rel`` is on the list and leads to a regular file with no symlink on the way."""
    if not ALLOWED.fullmatch(rel):
        return None
    try:
        real = (home / rel).resolve(strict=True)
        expected = home.resolve(strict=True).joinpath(*rel.split("/"))
    except (OSError, RuntimeError):
        return None
    return real if real == expected and real.is_file() else None


def download_name(asked: str, path: Path) -> str:
    """``asked`` as a file name: letters, digits, spaces, ``.``, ``_`` and ``-`` only, at most 80
    characters, ending in the served file's own extension. Nothing usable left gives the file's name."""
    ext = path.suffix
    stem = asked[: -len(ext)] if asked.lower().endswith(ext) else asked
    kept = "".join(ch for ch in stem if ch.isalnum() or ch in " ._-")
    kept = " ".join(kept.split()).strip(" .")
    kept = kept[: NAME_MAX - len(ext)].rstrip(" .")
    return (kept or path.stem) + ext


def attachment(name: str) -> str:
    """The Content-Disposition for downloading ``name``, with a plain ASCII name for old browsers."""
    plain = "".join(ch if ch.isascii() else "_" for ch in name)
    if plain == name:
        return f'attachment; filename="{name}"'
    return f"attachment; filename=\"{plain}\"; filename*=UTF-8''{quote(name, safe='')}"


@router.api_route("/media/{rel:path}", methods=["GET", "HEAD"], include_in_schema=False)
def media(rel: str, request: Request, download: str | None = None) -> Response:
    engine = request.app.state.engine
    path = media_target(engine.paths.home, rel)
    if path is None and (thumb := SOURCE_THUMB.fullmatch(rel)) is not None:
        path = _made_thumb(engine, thumb.group(1), rel)
    try:
        info = None if path is None else os.stat(path)  # one look, which the response then uses
    except OSError:  # removed since, as a project being deleted is
        info = None
    if path is None or info is None or not stat.S_ISREG(info.st_mode):
        raise HTTPException(404)
    headers = {"Cache-Control": IMMUTABLE if VERSION_FILE.fullmatch(rel) else NO_CACHE}
    if download is not None:
        headers["Content-Disposition"] = attachment(download_name(download, path))
    return FileResponse(path, headers=headers, media_type=TYPES[path.suffix], stat_result=info)


def _made_thumb(engine: Engine, project_id: str, rel: str) -> Path | None:
    """A source's thumbnail asked for before there is one: made now, once its metadata is saved. One that
    can't be made is remembered, and not tried again while the server runs."""
    if project_id in engine.thumb_misses:
        return None
    project = engine.project(project_id)
    if project is None or not engine.paths.project(project_id).meta_json.is_file():
        return None
    if ensure_thumb(engine.paths, project, fetch=engine.thumb_fetch) is None:
        engine.thumb_misses.add(project_id)
        return None
    return media_target(engine.paths.home, rel)
