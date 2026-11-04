"""Project, clip and settings queries. Every function takes a connection from ``db.connect``."""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from typing import Any

from clipforge.config import Settings
from clipforge.db import transaction
from clipforge.models import ClipRow, GameplayUse, LibraryItem, Project, ProjectOptions, RenderRow

# ClipRow fields stored as JSON in a column named after the field plus "_json".
_CLIP_JSON = {"scores", "evidence", "flags", "packaging", "review", "edits", "hook_titles", "topic_tags"}
_FIXED = {"id", "project_id", "created_at", "updated_at"}
_PROJECT_FIELDS = set(Project.model_fields) - _FIXED
_CLIP_FIELDS = set(ClipRow.model_fields) - _FIXED
_LIBRARY_FIELDS = set(LibraryItem.model_fields) - {"id", "kind", "path", "created_at"}
_NEWEST_FIRST = "ORDER BY created_at DESC, rowid DESC"
_BUSY = ("analyzing", "rendering")  # a rerun keeps the clips a job is working on


def new_id(prefix: str) -> str:
    return prefix + secrets.token_hex(5)


def _insert(conn: sqlite3.Connection, table: str, columns: dict[str, Any]) -> None:
    names = ", ".join(columns)
    marks = ", ".join("?" for _ in columns)
    conn.execute(f"INSERT INTO {table} ({names}) VALUES ({marks})", list(columns.values()))


def _update(conn: sqlite3.Connection, table: str, key: str, columns: dict[str, Any]) -> None:
    sets = ", ".join(f"{name} = ?" for name in columns)
    conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?", [*columns.values(), key])


def _check_fields(kind: str, fields: dict[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(fields) - allowed)
    if unknown:
        raise ValueError(f"unknown {kind} fields: {', '.join(unknown)}")


# Projects


def _project_value(name: str, value: Any) -> tuple[str, Any]:
    if name == "options":
        return "options_json", ProjectOptions.model_validate(value).model_dump_json()
    return name, value


def _project(row: sqlite3.Row) -> Project:
    data = dict(row)
    data["options"] = ProjectOptions.model_validate_json(data.pop("options_json"))
    return Project.model_validate(data)


def create_project(
    conn: sqlite3.Connection,
    *,
    source_kind: str,
    source_url: str | None,
    video_id: str | None,
    options: ProjectOptions,
    focus_s: float | None,
    prompt: str | None,
    title: str | None = None,
) -> Project:
    now = time.time()
    project = Project(
        id=new_id("p"),
        source_kind=source_kind,
        source_url=source_url,
        video_id=video_id,
        title=title,
        focus_s=focus_s,
        prompt=prompt,
        options=options,
        created_at=now,
        updated_at=now,
    )
    _insert(conn, "projects", dict(_project_value(k, v) for k, v in project.model_dump().items()))
    return project


def get_project(conn: sqlite3.Connection, pid: str) -> Project | None:
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
    return _project(row) if row else None


def list_projects(
    conn: sqlite3.Connection, limit: int = 50, status: list[str] | None = None
) -> list[Project]:
    """The newest ``limit`` projects, or with ``status`` the newest of those in one of its statuses."""
    if status is None:
        rows = conn.execute(f"SELECT * FROM projects {_NEWEST_FIRST} LIMIT ?", (limit,))
    else:
        marks = ", ".join("?" for _ in status)
        rows = conn.execute(
            f"SELECT * FROM projects WHERE status IN ({marks}) {_NEWEST_FIRST} LIMIT ?", (*status, limit)
        )
    return [_project(r) for r in rows]


def delete_project(conn: sqlite3.Connection, pid: str) -> None:
    """Delete the project and, through the cascades, its clips and their renders. Its clips' gameplay
    uses that no post made go too, so the library doesn't count them."""
    for (cid,) in conn.execute("SELECT id FROM clips WHERE project_id = ?", (pid,)).fetchall():
        clear_gameplay_uses(conn, cid)
    conn.execute("DELETE FROM projects WHERE id = ?", (pid,))


def find_projects_by_video(conn: sqlite3.Connection, video_id: str) -> list[Project]:
    rows = conn.execute(f"SELECT * FROM projects WHERE video_id = ? {_NEWEST_FIRST}", (video_id,))
    return [_project(r) for r in rows]


def unfinished_project_ids(conn: sqlite3.Connection) -> list[str]:
    """The projects still on their way (queued, running or waiting), oldest first."""
    rows = conn.execute(
        "SELECT id FROM projects WHERE status IN ('queued', 'running', 'waiting') ORDER BY created_at, rowid"
    )
    return [row["id"] for row in rows]


def update_project(conn: sqlite3.Connection, pid: str, **fields: Any) -> None:
    _check_fields("project", fields, _PROJECT_FIELDS)
    columns = dict(_project_value(k, v) for k, v in fields.items())
    columns["updated_at"] = time.time()
    _update(conn, "projects", pid, columns)


# Clips


def _clip_value(name: str, value: Any) -> tuple[str, Any]:
    if name in _CLIP_JSON:
        return f"{name}_json", None if value is None else json.dumps(value)
    if name == "sentence_ids":
        return name, json.dumps(value)
    if name == "passes_gates":
        return name, None if value is None else int(value)
    return name, value


def _clip(row: sqlite3.Row) -> ClipRow:
    data = dict(row)
    for name in _CLIP_JSON:
        raw = data.pop(f"{name}_json")
        if raw is not None:
            data[name] = json.loads(raw)
    data["sentence_ids"] = json.loads(data["sentence_ids"])
    return ClipRow.model_validate(data)


def posted_clips(conn: sqlite3.Connection, pid: str) -> set[str]:
    """The ids of this project's clips with a post you haven't cancelled: in the Queue, a draft, gone up or
    stopped, as deleting the clip would fail the post or take its record's video."""
    rows = conn.execute(
        "SELECT DISTINCT posts.clip_id FROM posts JOIN clips ON clips.id = posts.clip_id"
        " WHERE clips.project_id = ? AND posts.status NOT IN ('cancelled', 'discarded')",
        (pid,),
    )
    return {row[0] for row in rows}


def _replaceable(clip: ClipRow, posted: set[str]) -> bool:
    """Whether a rerun swaps this clip out: it has no edits, no post and no job working on it."""
    return not clip.edits and clip.id not in posted and clip.status not in _BUSY


def rerun_drops(clips: list[ClipRow], posted: set[str]) -> int:
    """How many of these rendered clips a rerun would delete, videos and all, set-aside ones included.
    ``posted`` is from ``posted_clips``."""
    return sum(c.status == "rendered" and _replaceable(c, posted) for c in clips)


def replace_ideas(conn: sqlite3.Connection, pid: str, rows: list[ClipRow]) -> list[str]:
    """Swap this project's clips for ``rows``, keeping the clips with edits, those with a post and those
    being analysed or rendered, and return the ids of the clips removed."""
    now = time.time()
    with transaction(conn):
        posted = posted_clips(conn, pid)
        gone = [c.id for c in list_clips(conn, pid) if _replaceable(c, posted)]
        conn.executemany("DELETE FROM clips WHERE id = ?", [(cid,) for cid in gone])
        for clip in rows:
            columns = dict(_clip_value(k, v) for k, v in clip.model_dump().items())
            columns["created_at"] = clip.created_at or now
            columns["updated_at"] = now
            _insert(conn, "clips", columns)
    return gone


def list_clips(conn: sqlite3.Connection, pid: str) -> list[ClipRow]:
    rows = conn.execute("SELECT * FROM clips WHERE project_id = ? ORDER BY rank IS NULL, rank, rowid", (pid,))
    return [_clip(r) for r in rows]


def get_clip(conn: sqlite3.Connection, cid: str) -> ClipRow | None:
    row = conn.execute("SELECT * FROM clips WHERE id = ?", (cid,)).fetchone()
    return _clip(row) if row else None


def clip_project_id(conn: sqlite3.Connection, cid: str) -> str | None:
    row = conn.execute("SELECT project_id FROM clips WHERE id = ?", (cid,)).fetchone()
    return row[0] if row else None


def update_clip(conn: sqlite3.Connection, cid: str, **fields: Any) -> None:
    _check_fields("clip", fields, _CLIP_FIELDS)
    columns = dict(_clip_value(k, v) for k, v in fields.items())
    columns["updated_at"] = time.time()
    _update(conn, "clips", cid, columns)


# Renders


def _render(row: sqlite3.Row) -> RenderRow:
    data = dict(row)
    data["qa"] = json.loads(data.pop("qa_json"))
    return RenderRow.model_validate(data)


def add_render(conn: sqlite3.Connection, render: RenderRow) -> RenderRow:
    render = render.model_copy(update={"created_at": render.created_at or time.time()})
    columns = render.model_dump(mode="json")
    columns["qa_json"] = json.dumps(columns.pop("qa"))
    _insert(conn, "renders", columns)
    return render


def list_renders(conn: sqlite3.Connection, cid: str) -> list[RenderRow]:
    """The clip's rendered versions, oldest first."""
    rows = conn.execute("SELECT * FROM renders WHERE clip_id = ? ORDER BY version", (cid,))
    return [_render(r) for r in rows]


def get_render(conn: sqlite3.Connection, rid: str) -> RenderRow | None:
    row = conn.execute("SELECT * FROM renders WHERE id = ?", (rid,)).fetchone()
    return _render(row) if row else None


# The library


def _library_item(row: sqlite3.Row) -> LibraryItem:
    data = dict(row)
    data["tags"] = json.loads(data.pop("tags_json"))
    return LibraryItem.model_validate(data)


def add_library_item(conn: sqlite3.Connection, item: LibraryItem) -> LibraryItem:
    item = item.model_copy(update={"created_at": item.created_at or time.time()})
    columns = item.model_dump()
    columns["tags_json"] = json.dumps(columns.pop("tags"))
    _insert(conn, "library_items", columns)
    return item


def list_library(conn: sqlite3.Connection, kind: str | None = None) -> list[LibraryItem]:
    """The library's items (of ``kind``, when given), oldest first."""
    where, args = ("WHERE kind = ?", (kind,)) if kind is not None else ("", ())
    rows = conn.execute(f"SELECT * FROM library_items {where} ORDER BY created_at, rowid", args)
    return [_library_item(r) for r in rows]


def get_library_item(conn: sqlite3.Connection, item_id: str) -> LibraryItem | None:
    row = conn.execute("SELECT * FROM library_items WHERE id = ?", (item_id,)).fetchone()
    return _library_item(row) if row else None


def update_library_item(conn: sqlite3.Connection, item_id: str, **fields: Any) -> LibraryItem | None:
    """The item with ``fields`` changed, or None when there is no such item."""
    _check_fields("library item", fields, _LIBRARY_FIELDS)
    columns = dict(fields)
    if "tags" in columns:
        columns["tags_json"] = json.dumps(columns.pop("tags"))
    if columns:
        _update(conn, "library_items", item_id, columns)
    return get_library_item(conn, item_id)


def delete_library_item(conn: sqlite3.Connection, item_id: str) -> bool:
    """Forget the item and, with it, every use of it. False when there was no such item."""
    with transaction(conn):
        return conn.execute("DELETE FROM library_items WHERE id = ?", (item_id,)).rowcount > 0


def record_gameplay_use(
    conn: sqlite3.Connection,
    item_id: str,
    start_s: float,
    end_s: float,
    *,
    clip_id: str | None,
    account_id: str | None = None,
    post_id: str | None = None,
) -> GameplayUse:
    """Write down that a clip, or a post to an account, shows ``item_id`` from ``start_s`` to ``end_s``. A
    clip holds one use until it is posted, and one more for each account's own version of it, so a
    re-render's use replaces the one before for the same clip and account; a post holds one too."""
    use = GameplayUse(
        id=new_id("u"),
        item_id=item_id,
        start_s=start_s,
        end_s=end_s,
        clip_id=clip_id,
        account_id=account_id,
        post_id=post_id,
        created_at=time.time(),
    )
    with transaction(conn):
        if post_id is not None:  # a post shows one stretch, so trying it again replaces the one before
            conn.execute("DELETE FROM gameplay_uses WHERE post_id = ?", (post_id,))
        elif clip_id is not None:
            drop_gameplay_use(conn, clip_id, account_id)
        _insert(conn, "gameplay_uses", use.model_dump())
    return use


def clear_gameplay_uses(conn: sqlite3.Connection, clip_id: str) -> None:
    """Forget the clip's gameplay uses that no post has made, its own and its accounts' versions'."""
    conn.execute("DELETE FROM gameplay_uses WHERE clip_id = ? AND post_id IS NULL", (clip_id,))


def drop_gameplay_use(conn: sqlite3.Connection, clip_id: str, account_id: str | None) -> None:
    """Forget the gameplay use no post has made of the clip's own render (``account_id`` None) or of that
    account's version of it."""
    conn.execute(
        "DELETE FROM gameplay_uses WHERE clip_id = ? AND post_id IS NULL AND account_id IS ?",
        (clip_id, account_id),
    )


def gameplay_use_of(
    conn: sqlite3.Connection, clip_id: str, account_id: str | None = None
) -> GameplayUse | None:
    """The clip's gameplay use that no post has made, if it has one: its own, or with ``account_id`` that
    account's version's."""
    row = conn.execute(
        "SELECT * FROM gameplay_uses WHERE clip_id = ? AND post_id IS NULL AND account_id IS ?"
        " ORDER BY created_at DESC, rowid DESC",
        (clip_id, account_id),
    ).fetchone()
    return GameplayUse.model_validate(dict(row)) if row else None


def gameplay_uses(conn: sqlite3.Connection, item_id: str) -> list[GameplayUse]:
    """Every recorded use of ``item_id``, oldest first."""
    rows = conn.execute(
        "SELECT * FROM gameplay_uses WHERE item_id = ? ORDER BY created_at, rowid", (item_id,)
    )
    return [GameplayUse.model_validate(dict(r)) for r in rows]


# Settings


def load_settings(conn: sqlite3.Connection) -> Settings:
    """Defaults overlaid by stored values. Unknown keys and values that no longer validate are skipped."""
    stored: dict[str, Any] = {}
    for row in conn.execute("SELECT key, value_json FROM settings ORDER BY key"):
        key = row["key"]
        if key not in Settings.model_fields:
            continue
        try:
            value = json.loads(row["value_json"])
            Settings.model_validate({**stored, key: value})
        except ValueError:  # bad JSON or a failed validation (pydantic's ValidationError is a ValueError)
            continue
        stored[key] = value
    return Settings.model_validate(stored)


def save_settings(conn: sqlite3.Connection, s: Settings) -> None:
    rows = [(k, json.dumps(v)) for k, v in s.model_dump(mode="json").items()]
    with transaction(conn):
        conn.executemany(
            "INSERT INTO settings (key, value_json) VALUES (?, ?)"
            " ON CONFLICT (key) DO UPDATE SET value_json = excluded.value_json",
            rows,
        )
