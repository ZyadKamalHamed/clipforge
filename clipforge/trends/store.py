"""Sound, hashtag set and list read queries. Every function takes a connection from ``db.connect``."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from clipforge.db import transaction
from clipforge.repo import _insert, _update
from clipforge.trends.models import AudioType, HashtagSet, Sound, SoundSource, TrendRead

_KEPT = ("id", "created_at")  # what a put over a known shortlisted sound leaves as it was
# Only Instagram's lists have an account: they show while it is live and still linked through Facebook
_LIVE = (
    "(account_id IS NULL OR account_id IN (SELECT id FROM accounts WHERE deleted_at IS NULL"
    " AND method = 'api' AND ig_mode = 'facebook'))"
)


# Sounds


def _columns(sound: Sound) -> dict[str, Any]:
    columns = sound.model_dump()
    columns["mood_tags_json"] = json.dumps(columns.pop("mood_tags"))
    return columns


def _sound(row: sqlite3.Row) -> Sound:
    data = dict(row)
    data["mood_tags"] = json.loads(data.pop("mood_tags_json"))
    return Sound.model_validate(data)


def sounds(
    conn: sqlite3.Connection, *, source: SoundSource | None = None, account_id: str | None = None
) -> list[Sound]:
    """The shortlist and the lists of live accounts linked through Facebook: Instagram's lists by account,
    then type, then their place in the list; the shortlist newest first."""
    where, args = [_LIVE], []
    if source is not None:
        where.append("source = ?")
        args.append(source)
    if account_id is not None:
        where.append("account_id = ?")
        args.append(account_id)
    rows = conn.execute(
        f"SELECT * FROM sounds WHERE {' AND '.join(where)} "
        "ORDER BY source = 'shortlist', account_id, audio_type, rank, created_at DESC, rowid DESC",
        args,
    )
    return [_sound(r) for r in rows]


def get_sound(conn: sqlite3.Connection, sound_id: str) -> Sound | None:
    row = conn.execute(f"SELECT * FROM sounds WHERE id = ? AND {_LIVE}", (sound_id,)).fetchone()
    return _sound(row) if row else None


def put_shortlist(conn: sqlite3.Connection, sound: Sound) -> Sound:
    """Adds the shortlisted sound, or, when its id on TikTok is already on the shortlist, writes over that row
    (keeping its id and when it was added, so it doesn't turn fresh). Returns what is stored."""
    row = conn.execute(
        "SELECT * FROM sounds WHERE source = 'shortlist' AND external_id = ?", (sound.external_id,)
    ).fetchone()
    if row is None:
        _insert(conn, "sounds", _columns(sound))
        return sound
    known = _sound(row)
    columns = _columns(sound.model_copy(update={k: getattr(known, k) for k in _KEPT}))
    _update(conn, "sounds", known.id, {k: v for k, v in columns.items() if k not in _KEPT})
    return get_sound(conn, known.id)  # type: ignore[return-value]


def update_sound(conn: sqlite3.Connection, sound_id: str, **fields: Any) -> Sound | None:
    """A shortlisted sound with ``fields`` changed, or None when there is no such sound."""
    known = get_sound(conn, sound_id)
    if known is None:
        return None
    sound = known.model_validate({**known.model_dump(), **fields})
    _update(conn, "sounds", sound_id, {k: v for k, v in _columns(sound).items() if k not in _KEPT})
    return sound


def replace_trending(
    conn: sqlite3.Connection, account_id: str, audio_type: AudioType, rows: list[Sound], now: float
) -> None:
    """Instagram's list of ``audio_type`` for the account, read at ``now``, in place of the one before."""
    with transaction(conn):
        conn.execute(
            "DELETE FROM sounds WHERE source = 'ig_trending' AND account_id = ? AND audio_type = ?",
            (account_id, audio_type),
        )
        for sound in rows:
            fixed = {"account_id": account_id, "audio_type": audio_type, "fetched_at": now}
            _insert(conn, "sounds", _columns(sound.model_copy(update=fixed)))


def delete_sound(conn: sqlite3.Connection, sound_id: str) -> bool:
    return conn.execute("DELETE FROM sounds WHERE id = ?", (sound_id,)).rowcount > 0


# Hashtag sets


def _set(row: sqlite3.Row) -> HashtagSet:
    data = dict(row)
    data["tags"] = json.loads(data.pop("tags_json"))
    return HashtagSet.model_validate(data)


def sets(conn: sqlite3.Connection) -> list[HashtagSet]:
    """Every hashtag set, by niche then name."""
    rows = conn.execute(
        "SELECT * FROM hashtag_sets ORDER BY niche COLLATE NOCASE, name COLLATE NOCASE, rowid"
    )
    return [_set(r) for r in rows]


def get_set(conn: sqlite3.Connection, set_id: str) -> HashtagSet | None:
    row = conn.execute("SELECT * FROM hashtag_sets WHERE id = ?", (set_id,)).fetchone()
    return _set(row) if row else None


def put_set(conn: sqlite3.Connection, tags: HashtagSet) -> HashtagSet:
    """Adds the set, or writes over the set with its id (keeping when it was made)."""
    columns = tags.model_dump()
    columns["tags_json"] = json.dumps(columns.pop("tags"))
    if get_set(conn, tags.id) is None:
        _insert(conn, "hashtag_sets", columns)
    else:
        _update(conn, "hashtag_sets", tags.id, {k: v for k, v in columns.items() if k not in _KEPT})
    return get_set(conn, tags.id)  # type: ignore[return-value]


def delete_set(conn: sqlite3.Connection, set_id: str) -> bool:
    return conn.execute("DELETE FROM hashtag_sets WHERE id = ?", (set_id,)).rowcount > 0


# List reads


def read_of(conn: sqlite3.Connection, read_id: str) -> TrendRead | None:
    row = conn.execute("SELECT * FROM trend_reads WHERE id = ?", (read_id,)).fetchone()
    return TrendRead.model_validate(dict(row)) if row else None


def save_read(conn: sqlite3.Connection, read: TrendRead) -> None:
    columns = read.model_dump()
    names = ", ".join(columns)
    marks = ", ".join("?" for _ in columns)
    sets_ = ", ".join(f"{name} = excluded.{name}" for name in columns if name != "id")
    conn.execute(
        f"INSERT INTO trend_reads ({names}) VALUES ({marks}) ON CONFLICT (id) DO UPDATE SET {sets_}",
        list(columns.values()),
    )
