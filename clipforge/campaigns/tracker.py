"""Submissions and view counts (SPEC 10.10): each posted post of a campaign's clip gets one submission to
track by hand, however it got posted; a submission moves only along the ways a marketplace can answer, and
back again when you pressed the wrong one; and a post's views are asked for by hand at 2 days, 7 days and
the end date, unless Instagram's insights read them."""

from __future__ import annotations

import math
import sqlite3
import time
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from clipforge import repo
from clipforge.campaigns import store
from clipforge.campaigns.models import Metric, Submission
from clipforge.campaigns.rules import campaign_of_post
from clipforge.errors import ClipForgeError
from clipforge.post import store as post_store

if TYPE_CHECKING:
    from clipforge.app import Engine
    from clipforge.campaigns.models import Campaign
    from clipforge.post.models import Post

MOVES: dict[str, frozenset[str]] = {
    "to_submit": frozenset({"submitted"}),
    "submitted": frozenset({"approved", "rejected", "to_submit"}),
    "approved": frozenset({"paid", "rejected", "submitted"}),
    "rejected": frozenset({"submitted", "approved"}),
    "paid": frozenset({"approved"}),
}
NAMES = {
    "to_submit": "To submit",
    "submitted": "Submitted",
    "approved": "Approved",
    "rejected": "Rejected",
    "paid": "Paid",
}
REASON_MAX = 300
VIEWS_MAX = 10**10
# When a post's views are asked for by hand: days after the day it was posted, then the campaign's end date
MARKS = ((2, "2 days"), (7, "7 days"))
END_MARK = "end date"
SETTLED = ("rejected", "paid")  # a submission whose views no longer matter


def day_of(engine: Engine, t: datetime | float) -> date:
    """The day ``t`` falls on in the posting time zone."""
    zone = ZoneInfo(engine.settings.posting.timezone)
    at = t if isinstance(t, datetime) else datetime.fromtimestamp(t, zone)
    return at.astimezone(zone).date()


def views_on(engine: Engine, metrics: list[Metric], posted_at: float, days: int, before: int) -> int | None:
    """The count the tracker asks for ``days`` days after a post went up: the first of ``metrics`` (oldest
    first) taken on or after that day and before day ``before``, by the posting time zone's days, as
    views_due counts them. None when there is none in that span."""
    start = day_of(engine, posted_at)
    first, last = start + timedelta(days=days), start + timedelta(days=before)
    for m in metrics:
        if first <= day_of(engine, m.captured_at) < last:
            return m.views
    return None


def submission_event(engine: Engine, submission: Submission) -> None:
    engine.events.publish(
        "submission",
        {
            "id": submission.id,
            "post_id": submission.post_id,
            "campaign_id": submission.campaign_id,
            "status": submission.status,
        },
    )


def on_post(engine: Engine, post: Post) -> Submission | None:
    """A posted post of a campaign's clip gets its submission, once: the new one, or None."""
    if post.status != "posted":
        return None
    conn = engine.db()
    if store.submission_of_post(conn, post.id) is not None:
        return None
    campaign = campaign_of_post(engine, post)
    if campaign is None:
        return None
    now = time.time()
    made = Submission(
        id=repo.new_id("sb"), post_id=post.id, campaign_id=campaign.id, created_at=now, updated_at=now
    )
    try:
        store.add_submission(conn, made)
    except sqlite3.IntegrityError:  # another thread made it first
        return None
    submission_event(engine, made)
    return made


def sweep(engine: Engine) -> int:
    """Submissions for every posted post that should have one and doesn't: how many were made."""
    rows = engine.db().execute(
        "SELECT id FROM posts WHERE status = 'posted' AND id NOT IN (SELECT post_id FROM submissions)"
        " ORDER BY created_at, rowid"
    )
    made = 0
    for (post_id,) in rows.fetchall():
        post = post_store.get_post(engine.db(), post_id)
        if post is not None and on_post(engine, post) is not None:
            made += 1
    return made


def _refused(old: str, new: str) -> ClipForgeError:
    return ClipForgeError(
        "wrong_status", f"A submission can't go from {NAMES.get(old, old)} to {NAMES.get(new, new)}."
    )


def move_submission(
    engine: Engine,
    submission_id: str,
    status: str,
    *,
    reason: str | None = None,
    paid_amount: float | None = None,
) -> Submission:
    """The submission moved to ``status``, with the times its status says: sent when submitted, decided when
    approved or rejected (an approval after payment keeps its decision), the reason only while rejected and
    the amount and today's date only while paid. Moving back undoes what the move forward set."""
    now = engine.now()
    with engine.lock:
        conn = engine.db()
        current = store.get_submission(conn, submission_id)
        if current is None:
            raise ClipForgeError("not_found", "That isn't here.")
        if status not in MOVES.get(current.status, ()):
            raise _refused(current.status, status)
        fields: dict[str, Any] = {"status": status, "reason": None, "paid_amount": None, "paid_on": None}
        if status == "to_submit":
            fields |= {"submitted_at": None, "decided_at": None}
        elif status == "submitted":
            fields |= {"submitted_at": current.submitted_at or now.timestamp(), "decided_at": None}
        elif status == "approved" and current.status == "paid":
            pass  # the payment was a mistake; the approval stands as it was
        elif status in ("approved", "rejected"):
            fields["decided_at"] = now.timestamp()
        if status == "rejected":
            said = (reason or "").strip()
            if len(said) > REASON_MAX:
                raise ClipForgeError("reason", f"Keep the reason to {REASON_MAX} characters.")
            fields["reason"] = said or None
        if status == "paid":
            if paid_amount is None or not math.isfinite(paid_amount) or paid_amount < 0:
                raise ClipForgeError("paid_amount", "Enter the amount paid.")
            fields |= {"paid_amount": round(paid_amount, 2), "paid_on": day_of(engine, now).isoformat()}
        moved = store.update_submission(conn, submission_id, **fields)
    submission_event(engine, moved)
    return moved


def add_views(engine: Engine, post_id: str, views: int, source: str = "hand") -> Metric:
    """A count of the posted post's views, taken now."""
    if isinstance(views, bool) or not isinstance(views, int) or not 0 <= views <= VIEWS_MAX:
        raise ClipForgeError("views", "Enter the views as a whole number.")
    conn = engine.db()
    post = post_store.get_post(conn, post_id)
    if post is None:
        raise ClipForgeError("not_found", "That isn't here.")
    if post.status != "posted":
        raise ClipForgeError("wrong_status", "Only a posted post has views.")
    count = store.add_metric(
        conn, Metric(post_id=post_id, captured_at=engine.now().timestamp(), source=source, views=views)
    )
    submission = store.submission_of_post(conn, post_id)
    if submission is not None:
        submission_event(engine, submission)  # its views and estimate changed
    return count


def read_by_insights(post: Post) -> bool:
    """Whether Instagram's insights read the post's views, so none are asked for by hand."""
    return (
        post.platform == "instagram"
        and post.method == "api"
        and post.state.get("insights") != "no_permission"
    )


def views_due(engine: Engine, post: Post, campaign: Campaign, today: date) -> str | None:
    """Which count of the post's views is due by hand today: "2 days", "7 days" or "end date", the latest
    whose day has come, once no count was entered on or after that day. None before the first, for a post
    insights read and for a submission rejected or paid. A count due after the end date isn't asked for."""
    if post.status != "posted" or post.posted_at is None or read_by_insights(post):
        return None
    conn = engine.db()
    submission = store.submission_of_post(conn, post.id)
    if submission is not None and submission.status in SETTLED:
        return None
    start = day_of(engine, post.posted_at)
    end = date.fromisoformat(campaign.ends_on) if campaign.ends_on else None
    marks = [
        (start + timedelta(days=n), 0, label)
        for n, label in MARKS
        if end is None or start + timedelta(days=n) <= end
    ]
    if end is not None:
        marks.append((end, 1, END_MARK))  # on the same day as another, the end date wins
    come = [m for m in sorted(marks) if m[0] <= today]
    if not come:
        return None
    mark, _, label = come[-1]
    last = store.latest_metric(conn, post.id, "hand")
    if last is not None and day_of(engine, last.captured_at) >= mark:
        return None
    return label
