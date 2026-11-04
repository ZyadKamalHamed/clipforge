"""Campaign, submission and view count queries. Every function takes a connection from ``db.connect``."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterable
from typing import Any

from clipforge.campaigns.models import Campaign, Metric, Submission
from clipforge.repo import _check_fields, _insert, _update

_CAMPAIGN_FIELDS = set(Campaign.model_fields) - {"id", "created_at", "updated_at"}
_SUBMISSION_FIELDS = set(Submission.model_fields) - {
    "id",
    "post_id",
    "campaign_id",
    "created_at",
    "updated_at",
}
_JSON = {"platforms": "platforms_json", "footage": "footage_json", "required": "required_json"}
LINK_COLUMNS = ("campaign_url", "submit_url")


# Campaigns


def _campaign_columns(fields: dict[str, Any], current: Campaign | None = None) -> dict[str, Any]:
    """The columns for ``fields``, checked by the model first; banned elements and hand rules share one
    column, so a change to either writes both."""
    columns: dict[str, Any] = {}
    for name, value in fields.items():
        if name in _JSON:
            columns[_JSON[name]] = json.dumps(value)
        elif name not in ("banned", "hand_rules"):
            columns[name] = value
    if "banned" in fields or "hand_rules" in fields:
        banned = fields.get("banned", current.banned if current else [])
        hand = fields.get("hand_rules", current.hand_rules if current else [])
        columns["banned_json"] = json.dumps({"elements": list(banned), "text": list(hand)})
    return columns


def _campaign(row: sqlite3.Row) -> Campaign:
    data = dict(row)
    for name, column in _JSON.items():
        data[name] = json.loads(data.pop(column))
    banned = json.loads(data.pop("banned_json"))
    data["banned"], data["hand_rules"] = banned.get("elements", []), banned.get("text", [])
    return Campaign.model_validate(data)


def add_campaign(conn: sqlite3.Connection, campaign: Campaign) -> Campaign:
    _insert(conn, "campaigns", _campaign_columns(campaign.model_dump()))
    return campaign


def get_campaign(conn: sqlite3.Connection, campaign_id: str) -> Campaign | None:
    row = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
    return _campaign(row) if row else None


def list_campaigns(conn: sqlite3.Connection) -> list[Campaign]:
    """Every campaign, newest first."""
    return [
        _campaign(r) for r in conn.execute("SELECT * FROM campaigns ORDER BY created_at DESC, rowid DESC")
    ]


def stored_link(conn: sqlite3.Connection, campaign_id: str, column: str) -> str | None:
    """The campaign's ``campaign_url`` or ``submit_url`` as the row holds it, unchecked, so whoever opens it
    checks it again; None for no such campaign or no link."""
    if column not in LINK_COLUMNS:
        raise ValueError(column)
    row = conn.execute(f"SELECT {column} FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
    return row[0] if row else None


def update_campaign(conn: sqlite3.Connection, campaign_id: str, **fields: Any) -> Campaign:
    """The campaign with ``fields`` changed, checked as a whole before anything is written."""
    _check_fields("campaign", fields, _CAMPAIGN_FIELDS)
    current = get_campaign(conn, campaign_id)
    if current is None:
        raise KeyError(campaign_id)
    checked = Campaign.model_validate(current.model_dump() | fields)
    dumped = checked.model_dump()
    columns = _campaign_columns({k: dumped[k] for k in fields}, checked)
    columns["updated_at"] = time.time()
    _update(conn, "campaigns", campaign_id, columns)
    return get_campaign(conn, campaign_id)  # type: ignore[return-value]


def linked_channels(conn: sqlite3.Connection, campaign_id: str) -> list[str]:
    """The ids of the clipping channels linked to the campaign."""
    rows = conn.execute("SELECT id FROM clip_channels WHERE campaign_id = ? ORDER BY rowid", (campaign_id,))
    return [r["id"] for r in rows]


def linked_projects(conn: sqlite3.Connection, campaign_id: str) -> list[str]:
    """The ids of the projects linked to the campaign."""
    rows = conn.execute("SELECT id FROM projects WHERE campaign_id = ? ORDER BY rowid", (campaign_id,))
    return [r["id"] for r in rows]


def delete_campaign(conn: sqlite3.Connection, campaign_id: str) -> None:
    """Deletes it; its clipping channels and projects stay, unlinked."""
    conn.execute("DELETE FROM campaigns WHERE id = ?", (campaign_id,))


# Submissions


def _submission(row: sqlite3.Row) -> Submission:
    return Submission.model_validate(dict(row))


def add_submission(conn: sqlite3.Connection, submission: Submission) -> Submission:
    _insert(conn, "submissions", submission.model_dump())
    return submission


def get_submission(conn: sqlite3.Connection, submission_id: str) -> Submission | None:
    row = conn.execute("SELECT * FROM submissions WHERE id = ?", (submission_id,)).fetchone()
    return _submission(row) if row else None


def submission_of_post(conn: sqlite3.Connection, post_id: str) -> Submission | None:
    row = conn.execute("SELECT * FROM submissions WHERE post_id = ?", (post_id,)).fetchone()
    return _submission(row) if row else None


def list_submissions(
    conn: sqlite3.Connection, campaign_id: str | None = None, status: Iterable[str] | None = None
) -> list[Submission]:
    """Submissions, newest first, of one campaign and with these statuses when given."""
    where, args = [], []
    if campaign_id is not None:
        where.append("campaign_id = ?")
        args.append(campaign_id)
    if status is not None:
        wanted = list(status)
        where.append(f"status IN ({', '.join('?' for _ in wanted)})" if wanted else "0")
        args += wanted
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    rows = conn.execute(f"SELECT * FROM submissions {clause} ORDER BY created_at DESC, rowid DESC", args)
    return [_submission(r) for r in rows]


def update_submission(conn: sqlite3.Connection, submission_id: str, **fields: Any) -> Submission:
    _check_fields("submission", fields, _SUBMISSION_FIELDS)
    _update(conn, "submissions", submission_id, fields | {"updated_at": time.time()})
    submission = get_submission(conn, submission_id)
    if submission is None:
        raise KeyError(submission_id)
    return submission


# View counts


def add_metric(conn: sqlite3.Connection, metric: Metric) -> Metric:
    _insert(conn, "post_metrics", metric.model_dump())
    return metric


def metrics_of(conn: sqlite3.Connection, post_id: str) -> list[Metric]:
    """The post's counts, oldest first."""
    rows = conn.execute(
        "SELECT * FROM post_metrics WHERE post_id = ? ORDER BY captured_at, rowid", (post_id,)
    )
    return [Metric.model_validate(dict(r)) for r in rows]


def any_metric(conn: sqlite3.Connection) -> bool:
    """Whether any post has a count."""
    return conn.execute("SELECT 1 FROM post_metrics LIMIT 1").fetchone() is not None


def latest_metric(conn: sqlite3.Connection, post_id: str, source: str | None = None) -> Metric | None:
    """The post's newest count, of one source when given."""
    extra, args = ("AND source = ?", (post_id, source)) if source else ("", (post_id,))
    row = conn.execute(
        f"SELECT * FROM post_metrics WHERE post_id = ? {extra} ORDER BY captured_at DESC, rowid DESC LIMIT 1",
        args,
    ).fetchone()
    return Metric.model_validate(dict(row)) if row else None
