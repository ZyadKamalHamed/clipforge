"""Your music library (SPEC 8.6 and 12): tracks you own or have a licence for, added from the Library page
with who made them, where they're from, what the licence lets you do, whether it asks for credit in the
caption, and the moods and energy the suggestions match a clip with. Each is copied into
``library/music/``. A clip names its track in its edits (``music``); deleting a track leaves that edit, and
the clip's next render says the track is gone and has no music."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any, get_args

from clipforge import repo
from clipforge.brain.schemas import Mood
from clipforge.errors import ClipForgeError, ToolError
from clipforge.library.gameplay import keep
from clipforge.media.ffmpeg import probe
from clipforge.models import LibraryItem
from clipforge.untrusted import clean_text

if TYPE_CHECKING:
    from clipforge.app import Engine

# A video file with sound is music too (its picture is never used), so .mp4 and .mov are on the list.
MUSIC_TYPES = (".mp3", ".m4a", ".aac", ".wav", ".aif", ".aiff", ".flac", ".mp4", ".m4v", ".mov")
MOODS: tuple[str, ...] = get_args(Mood)
SHORTEST_S = 1.0
NO_SOUND = "That file has no sound ClipForge can read. Pick an audio file such as MP3, M4A or WAV."
TOO_SHORT = "That track is under a second long. Pick a longer one."
NO_LICENCE = "Say what the licence lets you do."
NO_TITLE = "Give the track a title."
MOOD_WRONG = "Pick moods from the list."
ENERGY_WRONG = "Energy must be a whole number from 1 to 5."
LINK_WRONG = "The {what} must start with http:// or https://."
TOO_LONG = "Keep the {what} to {n} characters."
NOT_MUSIC = "That isn't a track in your music library."
TEXT = {  # what each text field is called in a sentence, and its longest
    "title": ("title", 120),
    "artist": ("artist", 200),
    "license": ("licence", 200),
    "source_url": ("link to where it's from", 500),
    "license_url": ("licence link", 500),
}
LINKS = ("source_url", "license_url")
FIELDS = (*TEXT, "credit_required", "moods", "energy")
LINK = re.compile(r"https?://\S+")
COLUMNS = {"title": "name", "artist": "credit", "moods": "tags"}  # the library_items column of a field


class MusicRefused(ClipForgeError):
    """A track or one of its fields refused, with the field (``file`` for the file itself) it is about."""

    def __init__(self, field: str, message: str):
        super().__init__("music_refused", message)
        self.field = field


def checked(fields: dict[str, Any]) -> dict[str, Any]:
    """``fields`` (any of ``FIELDS``) cleaned and checked: text trimmed, cleaned and empty as None, moods
    from ``MOODS`` once each. Each problem is refused naming its field; a title or licence can't be
    empty."""
    out: dict[str, Any] = {}
    for name, value in fields.items():
        if name in TEXT:
            text = clean_text(value.strip()) if isinstance(value, str) else None
            what, longest = TEXT[name]
            if text and len(text) > longest:
                raise MusicRefused(name, TOO_LONG.format(what=what, n=longest))
            if name in LINKS and text and not LINK.fullmatch(text):
                raise MusicRefused(name, LINK_WRONG.format(what=what))
            out[name] = text or None
        elif name == "moods":
            if not isinstance(value, list) or not all(m in MOODS for m in value):
                raise MusicRefused(name, MOOD_WRONG)
            out[name] = list(dict.fromkeys(value))
        elif name == "energy":
            if value is not None and (type(value) is not int or not 1 <= value <= 5):
                raise MusicRefused(name, ENERGY_WRONG)
            out[name] = value
        elif name == "credit_required":
            out[name] = bool(value)
        else:
            raise ValueError(f"unknown music field: {name}")
    if "title" in out and not out["title"]:
        raise MusicRefused("title", NO_TITLE)
    if "license" in out and not out["license"]:
        raise MusicRefused("license", NO_LICENCE)
    return out


def add_music(
    engine: Engine,
    path: Path | str,
    *,
    title: str | None,
    artist: str | None,
    source_url: str | None,
    license: str | None,
    license_url: str | None,
    credit_required: bool,
    moods: list[str],
    energy: int | None,
    move: bool = False,
) -> LibraryItem:
    """The track at ``path`` copied into ``library/music/`` (moved, for an upload, with ``move``) and added,
    titled ``title`` or after the file. Refused when a field is wrong, or when the file has no sound
    ClipForge can read or under a second of it; then nothing of it is kept."""
    src = Path(path).expanduser()
    fields = checked(
        {
            "title": title if title and title.strip() else src.stem,
            "artist": artist,
            "source_url": source_url,
            "license": license,
            "license_url": license_url,
            "credit_required": credit_required,
            "moods": moods,
            "energy": energy,
        }
    )
    if not src.is_file():
        raise ClipForgeError("no_file", f"There's no file at {path}.")
    ext = src.suffix.lower()
    if ext not in MUSIC_TYPES:
        raise MusicRefused("file", NO_SOUND)
    try:
        info = probe(src)
    except ToolError:
        raise MusicRefused("file", NO_SOUND) from None
    length = info.audio_duration_s or info.duration_s
    if not info.has_audio or length is None:
        raise MusicRefused("file", NO_SOUND)
    if length < SHORTEST_S:
        raise MusicRefused("file", TOO_SHORT)
    item_id = repo.new_id("l")
    dest = keep(src, engine.paths.music / f"{item_id}{ext}", move=move)
    item = LibraryItem(
        id=item_id,
        kind="music",
        path=str(dest),
        duration_s=length,
        **{COLUMNS.get(k, k): v for k, v in fields.items()},
    )
    try:
        return repo.add_library_item(engine.db(), item)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise


def update_music(engine: Engine, item_id: str, **fields: Any) -> LibraryItem:
    """The track with ``fields`` (any of ``FIELDS``) changed, each checked as a new track's are."""
    conn = engine.db()
    item = repo.get_library_item(conn, item_id)
    if item is None or item.kind != "music":
        raise ClipForgeError("not_music", NOT_MUSIC)
    columns = {COLUMNS.get(k, k): v for k, v in checked(fields).items()}
    return repo.update_library_item(conn, item_id, **columns)  # type: ignore[return-value]


def music_items(conn: sqlite3.Connection) -> list[LibraryItem]:
    """Your tracks, A to Z by title."""
    rows = conn.execute(
        "SELECT * FROM library_items WHERE kind = 'music' ORDER BY name COLLATE NOCASE, rowid"
    )
    return [repo._library_item(r) for r in rows]


def users_of(conn: sqlite3.Connection, item_id: str) -> int:
    """How many clips pick the track in their edits."""
    row = conn.execute(
        "SELECT COUNT(*) FROM clips WHERE json_extract(edits_json, '$.music') = ?", (item_id,)
    ).fetchone()
    return row[0]
