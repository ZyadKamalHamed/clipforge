"""Instagram's own view counts for campaign posts (SPEC 10.10): a reel posted through the API has its
insights read once a day through its account's Keychain token, while its campaign runs and for 30 days at
most, one call a second. A missing permission sends the post to hand entry for good; a limit, or no answer,
stops the round until the next hour."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from clipforge.campaigns import store, tracker
from clipforge.campaigns.ending import end_reason
from clipforge.campaigns.models import Metric
from clipforge.post import queue
from clipforge.post import store as post_store
from clipforge.post.graph import GraphError, action_for
from clipforge.post.instagram_api import _client, _host, _token

if TYPE_CHECKING:
    from clipforge.app import Engine
    from clipforge.post.models import Post

log = logging.getLogger(__name__)

FRESH_S = 20 * 3600.0  # a count newer than this is today's, whatever time the round runs
LIFE_S = 30 * 86400.0  # a post's views stop being read 30 days after it went up
GAP_S = 1.0  # between calls
MEDIA_ID = re.compile(r"[A-Za-z0-9_]{1,64}")
NO_PERMISSION = "no_permission"


def _permission_missing(err: GraphError) -> bool:
    return err.code == 10 or (err.code is not None and 200 <= err.code <= 299)


def _due(engine: Engine) -> list[tuple[Post, str, str, str]]:
    """(post, host, media id, token) of each post whose views are due, oldest submission first."""
    conn, now = engine.db(), engine.now()
    today = tracker.day_of(engine, now)
    due = []
    for sub in reversed(store.list_submissions(conn)):
        if sub.status in tracker.SETTLED:
            continue
        post = post_store.get_post(conn, sub.post_id)
        if post is None or post.status != "posted" or not tracker.read_by_insights(post):
            continue
        media_id = post.external_id or post.state.get("media_id")
        if not isinstance(media_id, str) or not MEDIA_ID.fullmatch(media_id):
            continue
        if post.posted_at is None or now.timestamp() - post.posted_at >= LIFE_S:
            continue
        if end_reason(store.get_campaign(conn, sub.campaign_id), today) is not None:
            continue
        last = store.latest_metric(conn, post.id, "insights")
        if last is not None and now.timestamp() - last.captured_at < FRESH_S:
            continue
        account = post_store.get_account(conn, post.account_id)
        token = _token(account) if account is not None else None
        if token is None:
            continue
        due.append((post, _host(account), media_id, token))
    return due


def _no_permission(engine: Engine, post_id: str) -> None:
    with engine.lock:
        post = post_store.get_post(engine.db(), post_id)
        if post is not None:
            queue.change_post(engine, post_id, state=post.state | {"insights": NO_PERMISSION})


def read_due(engine: Engine, *, pause: Callable[[float], None] = time.sleep) -> int:
    """Each due post's insights read and kept as a count: how many were read."""
    due = _due(engine)
    if not due:
        return 0
    read = 0
    with _client(engine) as client:
        for n, (post, host, media_id, token) in enumerate(due):
            if n:
                pause(GAP_S)
            try:
                counts = client.insights(host, media_id, token)
            except GraphError as err:
                if _permission_missing(err):
                    _no_permission(engine, post.id)
                    continue
                if action_for(err) in ("rate_limited", "retry"):
                    log.warning("reading the Instagram insights stopped at post %s: %s", post.id, err)
                    break
                log.warning("reading the Instagram insights of post %s failed: %s", post.id, err)
                continue
            views = counts.get("views")
            if views is None or views > tracker.VIEWS_MAX:
                continue
            store.add_metric(
                engine.db(),
                Metric(
                    post_id=post.id,
                    captured_at=engine.now().timestamp(),
                    source="insights",
                    views=views,
                    likes=counts.get("likes"),
                    comments=counts.get("comments"),
                    shares=counts.get("shares"),
                    saves=counts.get("saved"),
                ),
            )
            submission = store.submission_of_post(engine.db(), post.id)
            if submission is not None:
                tracker.submission_event(engine, submission)
            read += 1
    return read
