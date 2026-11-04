"""Brief source, brief, hidden advice and site read queries. Every function takes a connection from
``db.connect``."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from clipforge import repo
from clipforge.briefs.models import Brief, BriefSource
from clipforge.repo import _check_fields, _insert, _update

_JSON = {
    "rates": "rates_json",
    "caps": "caps_json",
    "platforms": "platforms_json",
    "footage": "footage_json",
    "fields": "fields_json",
    "notes": "notes_json",
}
_SOURCE_FIELDS = set(BriefSource.model_fields) - {"id", "created_at", "updated_at"}
_BRIEF_FIELDS = set(Brief.model_fields) - {"id"}
_KEPT = ("id", "first_seen_at")  # what a put over a known brief leaves as it was


# Sources


def _source(row: sqlite3.Row) -> BriefSource:
    return BriefSource.model_validate(dict(row))


def sources(conn: sqlite3.Connection) -> list[BriefSource]:
    """Every source, in the order they were added."""
    return [_source(r) for r in conn.execute("SELECT * FROM brief_sources ORDER BY created_at, rowid")]


def get_source(conn: sqlite3.Connection, source_id: str) -> BriefSource | None:
    row = conn.execute("SELECT * FROM brief_sources WHERE id = ?", (source_id,)).fetchone()
    return _source(row) if row else None


def add_source(conn: sqlite3.Connection, source: BriefSource) -> BriefSource:
    _insert(conn, "brief_sources", source.model_dump())
    return source


def update_source(conn: sqlite3.Connection, source_id: str, **fields: Any) -> BriefSource:
    _check_fields("brief source", fields, _SOURCE_FIELDS)
    current = get_source(conn, source_id)
    if current is None:
        raise KeyError(source_id)
    checked = BriefSource.model_validate(current.model_dump() | fields).model_dump()
    _update(conn, "brief_sources", source_id, {k: checked[k] for k in fields} | {"updated_at": time.time()})
    return get_source(conn, source_id)  # type: ignore[return-value]


# Briefs


def _columns(brief: Brief) -> dict[str, Any]:
    dumped = brief.model_dump()
    return {_JSON.get(k, k): json.dumps(v) if k in _JSON else v for k, v in dumped.items()}


def _brief(row: sqlite3.Row) -> Brief:
    data = dict(row)
    for name, column in _JSON.items():
        data[name] = json.loads(data.pop(column))
    return Brief.model_validate(data)


def get_brief(conn: sqlite3.Connection, brief_id: str) -> Brief | None:
    row = conn.execute("SELECT * FROM briefs WHERE id = ?", (brief_id,)).fetchone()
    return _brief(row) if row else None


def brief_by_key(conn: sqlite3.Connection, url: str | None, source_id: str, external_id: str) -> Brief | None:
    """The known brief with this link, whichever source found it; without a link, the one this source gave
    this id."""
    if url is not None:
        row = conn.execute("SELECT * FROM briefs WHERE url = ?", (url,)).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM briefs WHERE source_id = ? AND external_id = ?", (source_id, external_id)
        ).fetchone()
    return _brief(row) if row else None


def put_brief(conn: sqlite3.Connection, brief: Brief) -> Brief:
    """Adds the brief, or, when one with its key is known, writes its data over that row (keeping that row's
    id and when it was first seen). Returns what is stored."""
    known = brief_by_key(conn, brief.url, brief.source_id, brief.external_id)
    if known is None:
        _insert(conn, "briefs", _columns(brief))
        return brief
    columns = _columns(brief.model_copy(update={k: getattr(known, k) for k in _KEPT}))
    _update(conn, "briefs", known.id, {k: v for k, v in columns.items() if k not in _KEPT})
    return get_brief(conn, known.id)  # type: ignore[return-value]


def update_brief(conn: sqlite3.Connection, brief_id: str, **fields: Any) -> Brief:
    """The brief with ``fields`` changed, checked as a whole before anything is written."""
    _check_fields("brief", fields, _BRIEF_FIELDS)
    current = get_brief(conn, brief_id)
    if current is None:
        raise KeyError(brief_id)
    checked = _columns(Brief.model_validate(current.model_dump() | fields))
    _update(conn, "briefs", brief_id, {_JSON.get(k, k): checked[_JSON.get(k, k)] for k in fields})
    return get_brief(conn, brief_id)  # type: ignore[return-value]


def briefs(
    conn: sqlite3.Connection,
    *,
    status: list[str] | None = None,
    niche: str | None = None,
    marketplace: str | None = None,
    source_id: str | None = None,
    campaign_id: str | None = None,
) -> list[Brief]:
    """Briefs in the order they were first seen, narrowed by whichever filters are given."""
    where, args = [], []
    if status is not None:
        where.append(f"status IN ({', '.join('?' for _ in status)})" if status else "0")
        args += status
    filters = (
        ("niche", niche),
        ("marketplace", marketplace),
        ("source_id", source_id),
        ("campaign_id", campaign_id),
    )
    for column, value in filters:
        if value is not None:
            where.append(f"{column} = ?")
            args.append(value)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    rows = conn.execute(f"SELECT * FROM briefs {clause} ORDER BY first_seen_at, rowid", args)
    return [_brief(r) for r in rows]


# Hidden advice


def hide_advice(conn: sqlite3.Connection, kind: str, niche: str, language: str, until: str) -> None:
    """Hides a suggestion or a whole niche until ``until`` (a day), when it may come back."""
    conn.execute(
        "INSERT INTO advice_hidden (id, kind, niche, language, until) VALUES (?, ?, ?, ?, ?)",
        (repo.new_id("ah"), kind, niche, language, until),
    )


def hidden_advice(conn: sqlite3.Connection, today: str) -> set[tuple[str, str, str]]:
    """The (kind, niche, language) still hidden on ``today``."""
    rows = conn.execute("SELECT kind, niche, language FROM advice_hidden WHERE until > ?", (today,))
    return {(r["kind"], r["niche"], r["language"]) for r in rows}


# Site reads


def site_read(conn: sqlite3.Connection, site: str, day: str) -> tuple[int, float | None]:
    """The brief pages read from ``site`` on ``day`` and when its last request went."""
    row = conn.execute(
        "SELECT pages, last_at FROM site_reads WHERE site = ? AND day = ?", (site, day)
    ).fetchone()
    return (row["pages"], row["last_at"]) if row else (0, None)


def count_read(conn: sqlite3.Connection, site: str, day: str, *, page: bool, at: float) -> None:
    """Records a request to ``site`` at ``at``, counted as a brief page when ``page``."""
    conn.execute(
        "INSERT INTO site_reads (site, day, pages, last_at) VALUES (?, ?, ?, ?)"
        " ON CONFLICT (site, day) DO UPDATE SET pages = pages + excluded.pages, last_at = excluded.last_at",
        (site, day, int(page), at),
    )
