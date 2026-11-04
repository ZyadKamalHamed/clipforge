"""To-do item, timing and day queries. Every function takes a connection from ``db.connect``."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from clipforge.repo import _check_fields, _insert, _update
from clipforge.today.models import Day, Timing, TodoItem

# What lives in its own column; the rest of an item is kept in ref_json
_COLUMNS = {
    "id",
    "day",
    "kind",
    "ref",
    "title",
    "estimate_s",
    "value",
    "optional",
    "carried_from",
    "done_at",
    "done_how",
    "created_at",
}
_IN_JSON = (
    "ref",
    "ids",
    "detail",
    "currency",
    "as_if_usd",
    "guess",
    "due",
    "campaigns",
    "started_at",
    "units_at_start",
    "done_ids",
)
_ITEM_FIELDS = set(TodoItem.model_fields) - {"id", "day", "kind", "ref", "created_at"}


def _columns(item: TodoItem) -> dict[str, Any]:
    data = item.model_dump()
    columns = {name: data[name] for name in _COLUMNS}
    columns["optional"] = int(item.optional)
    columns["ref_json"] = json.dumps({name: data[name] for name in _IN_JSON})
    return columns


def _item(row: sqlite3.Row) -> TodoItem:
    data = {name: row[name] for name in _COLUMNS}
    data.update(json.loads(row["ref_json"]))
    data["optional"] = bool(row["optional"])
    return TodoItem.model_validate(data)


def get_item(conn: sqlite3.Connection, item_id: str) -> TodoItem | None:
    row = conn.execute("SELECT * FROM todo_items WHERE id = ?", (item_id,)).fetchone()
    return _item(row) if row else None


def items_of(conn: sqlite3.Connection, day: str) -> list[TodoItem]:
    """The day's items in the order they joined it."""
    rows = conn.execute("SELECT * FROM todo_items WHERE day = ? ORDER BY created_at, rowid", (day,))
    return [_item(r) for r in rows]


def upsert_item(conn: sqlite3.Connection, item: TodoItem) -> TodoItem:
    """Adds the item, or updates the day's row for the same kind and ref, which keeps its id and when it
    joined. Returns the stored item."""
    columns = _columns(item)
    names = ", ".join(columns)
    marks = ", ".join("?" for _ in columns)
    sets = ", ".join(
        f"{n} = excluded.{n}" for n in columns if n not in ("id", "day", "kind", "ref", "created_at")
    )
    conn.execute(
        f"INSERT INTO todo_items ({names}) VALUES ({marks})"
        f" ON CONFLICT (day, kind, ref) DO UPDATE SET {sets}",
        list(columns.values()),
    )
    row = conn.execute(
        "SELECT * FROM todo_items WHERE day = ? AND kind = ? AND ref = ?", (item.day, item.kind, item.ref)
    ).fetchone()
    return _item(row)


def delete_item(conn: sqlite3.Connection, item_id: str) -> None:
    conn.execute("DELETE FROM todo_items WHERE id = ?", (item_id,))


def update_item(conn: sqlite3.Connection, item_id: str, **fields: Any) -> TodoItem:
    """The item with ``fields`` changed, checked as a whole before anything is written."""
    _check_fields("to-do item", fields, _ITEM_FIELDS)
    current = get_item(conn, item_id)
    if current is None:
        raise KeyError(item_id)
    checked = TodoItem.model_validate(current.model_dump() | fields)
    columns = _columns(checked)
    _update(conn, "todo_items", item_id, {k: v for k, v in columns.items() if k not in ("id", "created_at")})
    return checked


def add_timing(conn: sqlite3.Connection, timing: Timing) -> Timing:
    _insert(conn, "item_timings", timing.model_dump())
    return timing


def timings_of(conn: sqlite3.Connection, kind: str, limit: int = 20) -> list[Timing]:
    """The kind's last ``limit`` timings, newest first."""
    rows = conn.execute(
        "SELECT * FROM item_timings WHERE kind = ? ORDER BY finished_at DESC, rowid DESC LIMIT ?",
        (kind, limit),
    )
    return [Timing.model_validate(dict(r)) for r in rows]


def get_day(conn: sqlite3.Connection, day: str) -> Day | None:
    row = conn.execute("SELECT * FROM days WHERE day = ?", (day,)).fetchone()
    return Day.model_validate(dict(row)) if row else None


def put_day(conn: sqlite3.Connection, day: Day) -> Day:
    """Adds the day's plan or replaces it."""
    columns = day.model_dump()
    names = ", ".join(columns)
    marks = ", ".join("?" for _ in columns)
    sets = ", ".join(f"{n} = excluded.{n}" for n in columns if n != "day")
    conn.execute(
        f"INSERT INTO days ({names}) VALUES ({marks}) ON CONFLICT (day) DO UPDATE SET {sets}",
        list(columns.values()),
    )
    return day


def last_days(conn: sqlite3.Connection, before: str, n: int) -> list[Day]:
    """The ``n`` days before ``before`` that have a plan, newest first."""
    rows = conn.execute("SELECT * FROM days WHERE day < ? ORDER BY day DESC LIMIT ?", (before, n))
    return [Day.model_validate(dict(r)) for r in rows]


def timings_between(conn: sqlite3.Connection, start: float, end: float) -> list[Timing]:
    """The timings that finished from ``start`` up to ``end``, oldest first."""
    rows = conn.execute(
        "SELECT * FROM item_timings WHERE finished_at >= ? AND finished_at < ? ORDER BY finished_at, rowid",
        (start, end),
    )
    return [Timing.model_validate(dict(r)) for r in rows]


def claim_day(conn: sqlite3.Connection, day: str, at: float) -> bool:
    """Marks the day's notification as going at ``at``, once: False when it went already, so two copies
    of ClipForge on one database never both build the morning."""
    conn.execute("INSERT OR IGNORE INTO days (day) VALUES (?)", (day,))
    cur = conn.execute("UPDATE days SET notified_at = ? WHERE day = ? AND notified_at IS NULL", (at, day))
    return cur.rowcount == 1


def release_day(conn: sqlite3.Connection, day: str) -> None:
    """The claim on the day's notification let go, for a morning that failed to build."""
    conn.execute("UPDATE days SET notified_at = NULL WHERE day = ?", (day,))


def path_done(conn: sqlite3.Connection) -> dict[str, str]:
    """Each done step of the path and how it was done, "app" or "hand"."""
    return {r["step"]: r["done_how"] for r in conn.execute("SELECT step, done_how FROM path_steps")}


def put_path_step(
    conn: sqlite3.Connection, step: str, at: float, how: str, plan: dict[str, Any] | None = None
) -> None:
    """The step done at ``at``, with the plan it was done to when given; one already done keeps how and
    when it was, and its plan."""
    conn.execute(
        "INSERT OR IGNORE INTO path_steps (step, done_at, done_how, plan_json) VALUES (?, ?, ?, ?)",
        (step, at, how, None if plan is None else json.dumps(plan)),
    )


def path_plan(conn: sqlite3.Connection, step: str) -> dict[str, Any] | None:
    """The plan the step was done to, or None."""
    row = conn.execute("SELECT plan_json FROM path_steps WHERE step = ?", (step,)).fetchone()
    return json.loads(row[0]) if row and row[0] else None
