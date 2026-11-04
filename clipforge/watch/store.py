"""Clipping channel, source, item and check queries. Every function takes a connection from ``db.connect``."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterable
from typing import Any

from clipforge.repo import _check_fields, _insert, _update
from clipforge.watch.models import ChannelOptions, ClipChannel, WatchCheck, WatchedSource, WatchItem

_CHANNEL_FIELDS = set(ClipChannel.model_fields) - {"id", "created_at", "updated_at"}
_SOURCE_FIELDS = set(WatchedSource.model_fields) - {"id", "clip_channel_id", "created_at"}
_ITEM_FIELDS = set(WatchItem.model_fields) - {
    "id",
    "source_id",
    "clip_channel_id",
    "first_seen_at",
    "updated_at",
}
_CHECK_FIELDS = set(WatchCheck.model_fields) - {"id", "clip_channel_id"}
_IN_ORDER = "ORDER BY created_at, rowid"


# Clipping channels


def _channel_value(name: str, value: Any) -> tuple[str, Any]:
    if name == "account_ids":
        return "account_ids_json", json.dumps(list(value))
    if name == "options":
        return "options_json", ChannelOptions.model_validate(value).model_dump_json()
    if name == "paused":
        return name, int(value)
    return name, value


def _channel(row: sqlite3.Row) -> ClipChannel:
    data = dict(row)
    data["account_ids"] = json.loads(data.pop("account_ids_json"))
    data["options"] = ChannelOptions.model_validate_json(data.pop("options_json"))
    return ClipChannel.model_validate(data)


def add_channel(conn: sqlite3.Connection, channel: ClipChannel) -> ClipChannel:
    _insert(conn, "clip_channels", dict(_channel_value(k, v) for k, v in channel.model_dump().items()))
    return channel


def get_channel(conn: sqlite3.Connection, channel_id: str) -> ClipChannel | None:
    row = conn.execute("SELECT * FROM clip_channels WHERE id = ?", (channel_id,)).fetchone()
    return _channel(row) if row else None


def list_channels(conn: sqlite3.Connection) -> list[ClipChannel]:
    """Every clipping channel, oldest first."""
    return [_channel(r) for r in conn.execute(f"SELECT * FROM clip_channels {_IN_ORDER}")]


def update_channel(conn: sqlite3.Connection, channel_id: str, **fields: Any) -> ClipChannel:
    _check_fields("clipping channel", fields, _CHANNEL_FIELDS)
    columns = dict(_channel_value(k, v) for k, v in fields.items())
    columns["updated_at"] = time.time()
    _update(conn, "clip_channels", channel_id, columns)
    channel = get_channel(conn, channel_id)
    if channel is None:
        raise KeyError(channel_id)
    return channel


def delete_channel(conn: sqlite3.Connection, channel_id: str) -> None:
    """Deletes it with its sources, items and checks; its projects stay, as ordinary ones."""
    conn.execute("DELETE FROM clip_channels WHERE id = ?", (channel_id,))


# Sources


def add_source(conn: sqlite3.Connection, source: WatchedSource) -> WatchedSource:
    """Raises ``sqlite3.IntegrityError`` when the clipping channel already watches that YouTube channel."""
    _insert(conn, "watched_sources", source.model_dump())
    return source


def get_source(conn: sqlite3.Connection, source_id: str) -> WatchedSource | None:
    row = conn.execute("SELECT * FROM watched_sources WHERE id = ?", (source_id,)).fetchone()
    return WatchedSource.model_validate(dict(row)) if row else None


def list_sources(conn: sqlite3.Connection, channel_id: str) -> list[WatchedSource]:
    rows = conn.execute(f"SELECT * FROM watched_sources WHERE clip_channel_id = ? {_IN_ORDER}", (channel_id,))
    return [WatchedSource.model_validate(dict(r)) for r in rows]


def update_source(conn: sqlite3.Connection, source_id: str, **fields: Any) -> None:
    _check_fields("source", fields, _SOURCE_FIELDS)
    if fields:
        _update(conn, "watched_sources", source_id, fields)


def delete_source(conn: sqlite3.Connection, source_id: str) -> None:
    conn.execute("DELETE FROM watched_sources WHERE id = ?", (source_id,))


# Items


def add_item(conn: sqlite3.Connection, item: WatchItem) -> WatchItem:
    """Raises ``sqlite3.IntegrityError`` when the clipping channel already has an item for the video."""
    _insert(conn, "watch_items", item.model_dump())
    return item


def get_item(conn: sqlite3.Connection, item_id: str) -> WatchItem | None:
    row = conn.execute("SELECT * FROM watch_items WHERE id = ?", (item_id,)).fetchone()
    return WatchItem.model_validate(dict(row)) if row else None


def items_for(
    conn: sqlite3.Connection, channel_id: str, status: Iterable[str] | None = None
) -> list[WatchItem]:
    """The clipping channel's items, newest first by when they were published (else first seen)."""
    where, args = "clip_channel_id = ?", [channel_id]
    if status is not None:
        statuses = list(status)
        where += f" AND status IN ({', '.join('?' for _ in statuses)})"
        args += statuses
    rows = conn.execute(
        f"SELECT * FROM watch_items WHERE {where} "
        "ORDER BY COALESCE(published_at, first_seen_at) DESC, rowid DESC",
        args,
    )
    return [WatchItem.model_validate(dict(r)) for r in rows]


def items_of_check(conn: sqlite3.Connection, check_id: str) -> list[WatchItem]:
    rows = conn.execute("SELECT * FROM watch_items WHERE check_id = ? ORDER BY rowid", (check_id,))
    return [WatchItem.model_validate(dict(r)) for r in rows]


def item_for_video(conn: sqlite3.Connection, channel_id: str, video_id: str) -> WatchItem | None:
    row = conn.execute(
        "SELECT * FROM watch_items WHERE clip_channel_id = ? AND video_id = ?", (channel_id, video_id)
    ).fetchone()
    return WatchItem.model_validate(dict(row)) if row else None


def item_of_project(conn: sqlite3.Connection, project_id: str) -> WatchItem | None:
    """The item a check turned into the project, if one did."""
    row = conn.execute("SELECT * FROM watch_items WHERE project_id = ?", (project_id,)).fetchone()
    return WatchItem.model_validate(dict(row)) if row else None


def delete_item(conn: sqlite3.Connection, item_id: str) -> None:
    conn.execute("DELETE FROM watch_items WHERE id = ?", (item_id,))


def update_item(conn: sqlite3.Connection, item_id: str, **fields: Any) -> None:
    _check_fields("item", fields, _ITEM_FIELDS)
    _update(conn, "watch_items", item_id, {**fields, "updated_at": time.time()})


# Checks


def _check_value(name: str, value: Any) -> tuple[str, Any]:
    return (name, int(value)) if name == "manual" else (name, value)


def _check(row: sqlite3.Row) -> WatchCheck:
    return WatchCheck.model_validate(dict(row))


def add_check(conn: sqlite3.Connection, check: WatchCheck) -> WatchCheck:
    _insert(conn, "watch_checks", dict(_check_value(k, v) for k, v in check.model_dump().items()))
    return check


def get_check(conn: sqlite3.Connection, check_id: str) -> WatchCheck | None:
    row = conn.execute("SELECT * FROM watch_checks WHERE id = ?", (check_id,)).fetchone()
    return _check(row) if row else None


def checks_for(conn: sqlite3.Connection, channel_id: str, limit: int = 30) -> list[WatchCheck]:
    """The clipping channel's newest ``limit`` checks, newest first."""
    rows = conn.execute(
        "SELECT * FROM watch_checks WHERE clip_channel_id = ? ORDER BY started_at DESC, rowid DESC LIMIT ?",
        (channel_id, limit),
    )
    return [_check(r) for r in rows]


def clipped_on(conn: sqlite3.Connection, channel_id: str, day: str, *, besides: str) -> int:
    """How many videos the clipping channel's checks for ``day`` clipped, the check ``besides`` left out."""
    row = conn.execute(
        "SELECT COALESCE(SUM(clipped), 0) FROM watch_checks"
        " WHERE clip_channel_id = ? AND check_on = ? AND id != ?",
        (channel_id, day, besides),
    ).fetchone()
    return int(row[0])


def update_check(conn: sqlite3.Connection, check_id: str, **fields: Any) -> None:
    _check_fields("check", fields, _CHECK_FIELDS)
    if fields:
        _update(conn, "watch_checks", check_id, dict(_check_value(k, v) for k, v in fields.items()))


def checks_on(conn: sqlite3.Connection, day: str) -> list[WatchCheck]:
    """Every clipping channel's checks for ``day``, oldest first."""
    rows = conn.execute("SELECT * FROM watch_checks WHERE check_on = ? ORDER BY started_at, rowid", (day,))
    return [_check(r) for r in rows]


def drafted_between(conn: sqlite3.Connection, start: float, end: float) -> list[WatchCheck]:
    """The checks whose drafts were made from ``start`` up to ``end``, oldest first."""
    rows = conn.execute(
        "SELECT * FROM watch_checks WHERE drafted_at >= ? AND drafted_at < ? ORDER BY drafted_at, rowid",
        (start, end),
    )
    return [_check(r) for r in rows]


def open_checks(conn: sqlite3.Connection) -> list[WatchCheck]:
    """The checks whose drafts aren't made yet, oldest first."""
    rows = conn.execute("SELECT * FROM watch_checks WHERE drafted_at IS NULL ORDER BY started_at, rowid")
    return [_check(r) for r in rows]
