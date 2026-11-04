"""Where a post can go from where it is (SPEC 10.6), and the step log each post keeps."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from clipforge.log import MASK, redact
from clipforge.post import store
from clipforge.post.errors import LifecycleError
from clipforge.post.models import Post

MOVES: dict[str, frozenset[str]] = {
    "scheduled": frozenset(
        {"rendering", "uploading", "needs_you", "failed", "cancelled", "exported", "scheduled"}
    ),
    "rendering": frozenset({"scheduled", "uploading", "needs_you", "failed", "cancelled"}),
    # back to scheduled only when posting was paused before the step that makes it public
    "uploading": frozenset(
        {"processing", "posted", "scheduled", "needs_you", "failed", "scheduled_on_tiktok"}
    ),
    "processing": frozenset({"posted", "needs_you", "failed"}),
    # TikTok Studio holds it to put up at its slot; ClipForge only reads its link, or stops following it
    "scheduled_on_tiktok": frozenset({"posted", "needs_you", "cancelled"}),
    "needs_you": frozenset({"scheduled", "posted", "cancelled", "exported", "failed"}),
    "failed": frozenset({"scheduled", "cancelled", "exported"}),
    "posted": frozenset(),
    "cancelled": frozenset(),
    "exported": frozenset({"posted"}),  # you posted it yourself and marked it so
    "draft": frozenset({"scheduled", "discarded"}),  # approval, or discard
    "discarded": frozenset({"draft"}),  # Undo
}
# Failures a retry alone can't change: the site turned the post down, or its clip, account or route is gone.
# Retry stays allowed (the site may change its mind), but the Queue leads such a post with Export instead.
NO_RETRY = ("rejected", "clip_deleted", "account_deleted", "no_route")
WAITING = ("needs_you", "failed")  # the posts that wait for the person, counted in the rail

_SECRET_NAME = re.compile(
    r"(?i)^(access_token|refresh_token|client_secret|api_key|fb_exchange_token|token|password|authorization"
    r"|cookie|set-cookie)$"
)


def can_move(old: str, new: str) -> bool:
    return new in MOVES.get(old, frozenset())


def move(conn: sqlite3.Connection, post_id: str, new: str, **fields: Any) -> Post:
    """Move the post to ``new`` with ``fields`` written in the same statement. Raises ``LifecycleError``
    when the post isn't there or can't go there from its status."""
    post = store.get_post(conn, post_id)
    if post is None:
        raise LifecycleError(f"no post {post_id}")
    if not can_move(post.status, new):
        raise LifecycleError(f"a {post.status} post can't become {new}")
    return store.update_post(conn, post_id, status=new, **fields)


def _clean(value: Any, name: str = "") -> Any:
    """``value`` with every secret masked: a field named like a secret whole, any string by ``redact``."""
    if name and _SECRET_NAME.match(name) and value is not None:
        return MASK
    if isinstance(value, dict):
        return {str(k): _clean(v, str(k)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_clean(v) for v in value]
    if isinstance(value, str):
        return redact(value)
    if value is None or isinstance(value, bool | int | float):
        return value
    return redact(str(value))


class StepLog:
    """One JSON line per step a post takes, with ``step``, ``at`` and the step's data, secrets masked."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def write(self, step: str, **data: Any) -> None:
        line = {"step": step, "at": time.time(), **_clean(data)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
