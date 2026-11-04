"""The library API (SPEC 12 and 14.6): gameplay videos added with who made them and the licence that lets
clips use them, listed with how often clips use them, and deleted with their uses (a clip that named one
falls back at its next render); your music, added with its licence, moods and energy, changed in place,
listed A to Z with how many clips pick it, and deleted (a clip that picked it has none at its next render);
the gameplay starter list, each video downloaded only when you press Download, never before; the sound
effects, to hear before turning them on; and the caption fonts with their licences, served only by the names
in ClipForge's own fonts folder."""

from __future__ import annotations

import io
import json
import re
import shutil
import wave
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import unquote

import anyio.to_thread
import numpy as np
from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse

from clipforge import repo
from clipforge.assets import FONTS_DIR
from clipforge.errors import ClipForgeError
from clipforge.library import gameplay, music, sfx, starter
from clipforge.server.errors import INVALID, ApiError
from clipforge.server.uploads import VIDEO_TYPES, receive_upload
from clipforge.server.views import clean_file_name
from clipforge.untrusted import clean_text

if TYPE_CHECKING:
    from clipforge.app import Engine
    from clipforge.models import LibraryItem

NOT_IN_LIBRARY = "That isn't in the library."
NO_CREDIT = "Say who made this video, so clips can credit them."
NO_LICENCE = "Say which licence lets you use this video, like CC BY 4.0 or your own footage."
LINK_WRONG = "The licence link must start with http:// or https://."
KIND_WRONG = "The library takes gameplay videos and music."
TOO_LONG = "Keep the {what} to {n} characters."
FIELDS = {  # what each field of X-Item is called in a sentence, and its longest
    "name": ("name", 120),
    "credit": ("credit", 200),
    "license": ("licence", 200),
    "license_url": ("licence link", 500),
    "notes": ("notes", 1000),
}
LINK = re.compile(r"https?://\S+")
MUSIC_EXTS = frozenset(ext.lstrip(".") for ext in music.MUSIC_TYPES)
PREVIEW_GAIN = 10 ** (-6 / 20)  # about -6 dBFS, so a preview never plays at full scale
FONT_TYPES = {".ttf": "font/ttf", ".txt": "text/plain; charset=utf-8"}

router = APIRouter(prefix="/api")


def _engine(request: Request) -> Engine:
    return request.app.state.engine


# Gameplay and music


def item_json(engine: Engine, item: LibraryItem) -> dict[str, Any]:
    path = Path(item.path)
    track = item.kind == "music"
    uses = music.users_of(engine.db(), item.id) if track else gameplay.clips_using(engine.db(), item.id)
    return {
        "id": item.id,
        "kind": item.kind,
        "name": clean_text(item.name),
        "credit": clean_text(item.credit),
        "license": clean_text(item.license),
        "license_url": item.license_url,
        "source_url": item.source_url,
        "credit_required": item.credit_required,
        "moods": item.tags if track else [],
        "energy": item.energy,
        "notes": clean_text(item.notes),
        "duration_s": item.duration_s,
        "width": item.width,
        "height": item.height,
        "uses": uses,
        "size_bytes": path.stat().st_size if path.is_file() else None,  # None: its file has gone
        "url": f"/media/library/{item.kind}/{path.name}",
        "created_at": item.created_at,
    }


@router.get("/library")
def list_items(request: Request, kind: Literal["gameplay", "music"] | None = None) -> dict[str, Any]:
    """The library's items (of ``kind``, when given), oldest first; music A to Z by title."""
    engine = _engine(request)
    items = music.music_items(engine.db()) if kind == "music" else repo.list_library(engine.db(), kind)
    return {"items": [item_json(engine, item) for item in items]}


def parse_item(header: str | None) -> tuple[str, dict[str, Any]]:
    """``X-Item``, percent-encoded JSON, as its kind and add_gameplay's or add_music's fields: each problem
    is a 422 naming its field."""
    try:
        raw = json.loads(unquote(header)) if header else {}
    except ValueError:
        raise ApiError(422, "invalid", INVALID, "item") from None
    if not isinstance(raw, dict):
        raise ApiError(422, "invalid", INVALID, "item")
    kind = raw.pop("kind", "gameplay")
    if kind == "music":
        return kind, music_fields(raw, adding=True)
    if kind != "gameplay":
        raise ApiError(422, "invalid", KIND_WRONG, "kind")
    if not set(raw) <= set(FIELDS):
        raise ApiError(422, "invalid", INVALID, "item")
    fields: dict[str, Any] = {}
    for field, (what, longest) in FIELDS.items():
        value = raw.get(field)
        if value is not None and not isinstance(value, str):
            raise ApiError(422, "invalid", INVALID, field)
        value = clean_text(value.strip()) if value else None
        if value and len(value) > longest:
            raise ApiError(422, "invalid", TOO_LONG.format(what=what, n=longest), field)
        fields[field] = value or None
    if not fields["credit"]:
        raise ApiError(422, "invalid", NO_CREDIT, "credit")
    if not fields["license"]:
        raise ApiError(422, "invalid", NO_LICENCE, "license")
    if fields["license_url"] and not LINK.fullmatch(fields["license_url"]):
        raise ApiError(422, "invalid", LINK_WRONG, "license_url")
    return kind, fields


def music_fields(raw: dict[str, Any], *, adding: bool) -> dict[str, Any]:
    """A track's fields from a request, checked as add_music checks them. Adding, every field is there (a
    title left out is the file's name); changing, only those sent."""
    if not set(raw) <= set(music.FIELDS):
        raise ApiError(422, "invalid", INVALID, "item")
    for name, value in raw.items():
        if name in music.TEXT:
            wrong = value is not None and not isinstance(value, str)
        elif name == "moods":
            wrong = not isinstance(value, list) or not all(isinstance(m, str) for m in value)
        elif name == "credit_required":
            wrong = not isinstance(value, bool)
        else:
            wrong = value is not None and (isinstance(value, bool) or not isinstance(value, int))
        if wrong:
            raise ApiError(422, "invalid", INVALID, name)
    if adding:
        raw = {"credit_required": False, "moods": [], "energy": None} | {n: None for n in music.TEXT} | raw
    try:
        fields = music.checked({k: v for k, v in raw.items() if not (adding and k == "title" and not v)})
    except music.MusicRefused as err:
        raise ApiError(422, "invalid", err.message, err.field) from None
    return {"title": None, **fields} if adding else fields


@router.post("/library", status_code=201)
async def add_item(request: Request) -> dict[str, Any]:
    """A gameplay video or a music track added from the Library page: the body is the file,
    ``X-File-Name`` its name and ``X-Item`` (percent-encoded JSON) its kind and fields (a video's name,
    credit, licence, licence link and notes; a track's title, artist, where it's from, licence, licence
    link, whether the licence asks for credit, moods and energy), checked before the file is read."""
    engine = _engine(request)
    kind, fields = parse_item(request.headers.get("x-item"))
    name = unquote(request.headers.get("x-file-name", ""))
    floor = int(engine.settings.min_free_gb * 1e9)
    allowed = VIDEO_TYPES
    if kind == "music":
        _, dot, ext = clean_file_name(name).rpartition(".")
        if not dot or ext.lower() not in MUSIC_EXTS:
            raise ApiError(415, "not_audio", music.NO_SOUND, "file")
        allowed = MUSIC_EXTS
    path = await receive_upload(request, engine.paths.uploads, name, allowed=allowed, floor_bytes=floor)
    try:
        return await anyio.to_thread.run_sync(_add, engine, path, kind, fields)
    except music.MusicRefused as err:
        code = "not_audio" if err.field == "file" else "invalid"
        raise ApiError(422, code, err.message, err.field) from None
    except ClipForgeError as err:
        if err.code == "not_video":
            raise ApiError(422, "not_video", err.message, "file") from None
        raise
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)


def _add(engine: Engine, path: Path, kind: str, fields: dict[str, Any]) -> dict[str, Any]:
    if kind == "music":
        item = music.add_music(engine, path, move=True, **fields)
    else:
        item = gameplay.add_gameplay(engine.db(), engine.paths, path, move=True, **fields)
    return item_json(engine, item)


@router.patch("/library/{item_id}")
def change_track(item_id: str, body: dict[str, Any], request: Request) -> dict[str, Any]:
    """A track's fields changed, each checked as a new track's are; a field left out stays as it was. Only
    music changes here: anything else is 404."""
    engine = _engine(request)
    fields = music_fields(body, adding=False)
    try:
        with engine.lock:
            item = music.update_music(engine, item_id, **fields)
    except music.MusicRefused as err:
        raise ApiError(422, "invalid", err.message, err.field) from None
    except ClipForgeError as err:
        if err.code == "not_music":
            raise ApiError(404, "not_found", err.message) from None
        raise
    return item_json(engine, item)


@router.delete("/library/{item_id}", status_code=204)
def delete_item(item_id: str, request: Request) -> Response:
    """The item, its file and its uses gone."""
    engine = _engine(request)
    with engine.lock:  # as a render takes its segment
        item = repo.get_library_item(engine.db(), item_id)
        if item is None or not repo.delete_library_item(engine.db(), item_id):
            raise ApiError(404, "not_found", NOT_IN_LIBRARY)
    path = Path(item.path)
    if path.resolve().is_relative_to(engine.paths.library.resolve()):  # only ever the library's own copy
        path.unlink(missing_ok=True)
    return Response(status_code=204)


# The starter list


def starter_json(engine: Engine, entry: starter.StarterEntry) -> dict[str, Any]:
    return {
        **entry.model_dump(),
        "chips": starter.chips(entry),
        "first_part": entry.duration_s > starter.STARTER_MAX_S,
        **starter.state_of(engine, entry),
    }


@router.get("/library/starter")
def starter_list(request: Request) -> dict[str, Any]:
    """The starter list with where each video stands. Nothing downloads here."""
    engine = _engine(request)
    return {"note": starter.NOTE, "entries": [starter_json(engine, e) for e in starter.entries()]}


@router.post("/library/starter/{entry_id:path}/download", status_code=202)
def download_starter(entry_id: str, request: Request) -> dict[str, Any]:
    """The video's download queued as a job (202 with its row), unless the library has it already or it is on
    its way; 404 for an id that isn't on the list."""
    engine = _engine(request)
    entry = starter.entry_of(entry_id)
    if entry is None:
        raise ApiError(404, "not_found", starter.NOT_LISTED)
    return {**starter_json(engine, entry), **starter.start(engine, entry)}


# Sound effects


@router.get("/library/sfx")
def sound_effects() -> dict[str, Any]:
    return {
        "sounds": [
            {"kind": kind, "length_s": sfx.LENGTH_S[kind], "url": f"/api/library/sfx/{kind}.wav"}
            for kind in sfx.KINDS
        ]
    }


@router.get("/library/sfx/{name}")
def sound_effect(name: str) -> Response:
    kind, _, ext = name.partition(".")
    if ext != "wav" or kind not in sfx.KINDS:
        raise ApiError(404, "not_found", "That isn't one of ClipForge's sound effects.")
    return Response(wav_of(kind), media_type="audio/wav")


@lru_cache(maxsize=len(sfx.KINDS))
def wav_of(kind: str) -> bytes:
    """``kind`` as a 16-bit mono WAV at 48 kHz and about -6 dBFS, made on first use and kept."""
    pcm = np.round(sfx.synth(kind) * PREVIEW_GAIN * 32767).astype("<i2")
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(pcm.tobytes())
    return out.getvalue()


# Fonts


def font_files() -> dict[str, Path]:
    """The fonts folder's fonts and licences by file name: the only names served."""
    return {
        p.name: p
        for p in FONTS_DIR.iterdir()
        if p.suffix in FONT_TYPES and not p.name.startswith(".") and p.is_file()
    }


def font_json(path: Path, files: dict[str, Path]) -> dict[str, Any]:
    """A font's family (from the part of its name before the dash, "BebasNeue" read as "Bebas Neue"), its
    style, and the licence beside it ("OFL-BebasNeue.txt")."""
    stem, _, style = path.stem.partition("-")
    licence = next(
        (n for n in sorted(files) if n.endswith(".txt") and Path(n).stem.partition("-")[2] == stem), None
    )
    return {
        "file": path.name,
        "family": re.sub(r"(?<=[a-z])(?=[A-Z])", " ", stem),
        "style": style or "Regular",
        "licence": licence,
        "url": f"/api/fonts/{path.name}",
        "licence_url": f"/api/fonts/{licence}" if licence else None,
    }


@router.get("/fonts")
def fonts() -> dict[str, Any]:
    files = font_files()
    return {"fonts": [font_json(files[n], files) for n in sorted(files) if n.endswith(".ttf")]}


@router.get("/fonts/{file}")
def font_file(file: str) -> FileResponse:
    path = font_files().get(file)
    if path is None:
        raise ApiError(404, "not_found", "That isn't one of ClipForge's fonts.")
    return FileResponse(path, media_type=FONT_TYPES[path.suffix])
