"""Account, batch and post queries. Every function takes a connection from ``db.connect``."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterable
from typing import Any

from clipforge.post.models import ACTIVE, Account, AccountSettings, Post, PostOptions
from clipforge.repo import _check_fields, _insert, _update, new_id

_ACCOUNT_FIELDS = set(Account.model_fields) - {"id", "created_at"}
_POST_FIELDS = set(Post.model_fields) - {"id", "created_at", "updated_at"}
_POST_JSON = {"hashtags", "options", "state"}
_IN_ORDER = "ORDER BY created_at, rowid"
_BY_TIME = "ORDER BY scheduled_at, rowid"


# Accounts


def _account_value(name: str, value: Any) -> tuple[str, Any]:
    if name == "settings":
        return "settings_json", AccountSettings.model_validate(value).model_dump_json()
    if name == "paused":
        return name, int(value)
    return name, value


def _account(row: sqlite3.Row) -> Account:
    data = dict(row)
    data["settings"] = AccountSettings.model_validate_json(data.pop("settings_json"))
    return Account.model_validate(data)


def add_account(conn: sqlite3.Connection, a: Account) -> Account:
    """Raises ``sqlite3.IntegrityError`` when a live account on the platform already has the handle."""
    _insert(conn, "accounts", dict(_account_value(k, v) for k, v in a.model_dump().items()))
    return a


def get_account(conn: sqlite3.Connection, account_id: str, *, deleted: bool = False) -> Account | None:
    """The account, or None; a deleted one only with ``deleted``."""
    sql = "SELECT * FROM accounts WHERE id = ?" + ("" if deleted else " AND deleted_at IS NULL")
    row = conn.execute(sql, (account_id,)).fetchone()
    return _account(row) if row else None


def list_accounts(conn: sqlite3.Connection) -> list[Account]:
    """The live accounts, oldest first."""
    rows = conn.execute(f"SELECT * FROM accounts WHERE deleted_at IS NULL {_IN_ORDER}")
    return [_account(r) for r in rows]


def update_account(conn: sqlite3.Connection, account_id: str, **fields: Any) -> Account:
    _check_fields("account", fields, _ACCOUNT_FIELDS)
    if fields:
        _update(conn, "accounts", account_id, dict(_account_value(k, v) for k, v in fields.items()))
    account = get_account(conn, account_id, deleted=True)
    if account is None:
        raise KeyError(account_id)
    return account


def delete_account(conn: sqlite3.Connection, account_id: str) -> None:
    """Sets ``deleted_at``: the account's posts keep their history, and its handle can be added again."""
    conn.execute(
        "UPDATE accounts SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL", (time.time(), account_id)
    )


# Batches


def add_batch(conn: sqlite3.Connection, plan_id: str, mode: str, timing: str, acks: dict[str, Any]) -> str:
    """A new batch for one Confirm and post. Raises ``sqlite3.IntegrityError`` when ``plan_id`` was used."""
    bid = new_id("b")
    _insert(
        conn,
        "batches",
        {
            "id": bid,
            "plan_id": plan_id,
            "mode": mode,
            "schedule_mode": timing,
            "acknowledgements_json": json.dumps(acks),
            "created_at": time.time(),
        },
    )
    return bid


def batch_for_plan(conn: sqlite3.Connection, plan_id: str) -> str | None:
    row = conn.execute("SELECT id FROM batches WHERE plan_id = ?", (plan_id,)).fetchone()
    return row[0] if row else None


# Posts


def _post_value(name: str, value: Any) -> tuple[str, Any]:
    if name == "options":
        return "options_json", PostOptions.model_validate(value).model_dump_json()
    if name in _POST_JSON:
        return f"{name}_json", json.dumps(value)
    return name, value


def _post(row: sqlite3.Row) -> Post:
    data = dict(row)
    for name in _POST_JSON:
        data[name] = json.loads(data.pop(f"{name}_json"))
    return Post.model_validate(data)


def add_posts(conn: sqlite3.Connection, posts: Iterable[Post]) -> None:
    for p in posts:
        _insert(conn, "posts", dict(_post_value(k, v) for k, v in p.model_dump().items()))


def get_post(conn: sqlite3.Connection, post_id: str) -> Post | None:
    row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    return _post(row) if row else None


def update_post(conn: sqlite3.Connection, post_id: str, **fields: Any) -> Post:
    _check_fields("post", fields, _POST_FIELDS)
    columns = dict(_post_value(k, v) for k, v in fields.items())
    columns["updated_at"] = time.time()
    _update(conn, "posts", post_id, columns)
    post = get_post(conn, post_id)
    if post is None:
        raise KeyError(post_id)
    return post


def list_posts(
    conn: sqlite3.Connection,
    *,
    status: Iterable[str] | None = None,
    account_id: str | None = None,
    batch_id: str | None = None,
    platform: str | None = None,
) -> list[Post]:
    """Posts by time, narrowed by each filter given."""
    where, args = [], []
    if status is not None:
        statuses = list(status)
        where.append(f"status IN ({', '.join('?' for _ in statuses)})")
        args += statuses
    for column, value in (("account_id", account_id), ("batch_id", batch_id), ("platform", platform)):
        if value is not None:
            where.append(f"{column} = ?")
            args.append(value)
    clause = f"WHERE {' AND '.join(where)} " if where else ""
    return [_post(r) for r in conn.execute(f"SELECT * FROM posts {clause}{_BY_TIME}", args)]


def site_ids(conn: sqlite3.Connection, platform: str, *, besides: str) -> list[str]:
    """The ids the site gave the platform's other posts: a link already recorded is never another's."""
    rows = conn.execute(
        "SELECT external_id FROM posts WHERE platform = ? AND id != ? AND external_id IS NOT NULL",
        (platform, besides),
    )
    return [r[0] for r in rows]


def posts_for_clip(conn: sqlite3.Connection, clip_id: str) -> list[Post]:
    return [_post(r) for r in conn.execute(f"SELECT * FROM posts WHERE clip_id = ? {_BY_TIME}", (clip_id,))]


def posts_between(conn: sqlite3.Connection, account_id: str, start: float, end: float) -> list[Post]:
    """The account's posts on their way or posted with a time from ``start`` up to ``end``, the ones its
    daily cap and spacing count."""
    statuses = (*ACTIVE, "posted")
    rows = conn.execute(
        f"SELECT * FROM posts WHERE account_id = ? AND scheduled_at >= ? AND scheduled_at < ?"
        f" AND status IN ({', '.join('?' for _ in statuses)}) {_BY_TIME}",
        (account_id, start, end, *statuses),
    )
    return [_post(r) for r in rows]
