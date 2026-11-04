"""The morning (SPEC 14.10, 18). From your notify time on the posting time zone's day, the day's list is
built once, as soon as the day's clipping checks have made their drafts and the day's brief sources are read,
or two hours on, and the one daily notification says what waits. A start later in the day builds at once.
Building records the seconds the list plans, which set the next day's drafts cap: after a day over 15
minutes, the drafts it held less as many as the time over would take, so the next day fits; after three days
in a row with 2 minutes to spare, one more, never past what the clipping channels make in a day; with no cap
set, none."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta
from math import ceil
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from clipforge.briefs import run as brief_run
from clipforge.today import day as today_day
from clipforge.today import store
from clipforge.today.day import BUDGET_S
from clipforge.today.models import Day, TodoItem
from clipforge.watch import drafts as draft_store
from clipforge.watch import store as watch_store

if TYPE_CHECKING:
    from clipforge.app import Engine

WAIT = timedelta(hours=2)  # the longest the morning waits for the checks' drafts and the briefs
SPARE_S = 120  # a short day leaves 2 minutes of the 15 over
SHORT_DAYS = 3  # short days in a row before the cap goes up
LOOK_BACK = 30  # the days looked through for the last built ones
# What the notification calls each kind's count, for one and for more, in the order it names them
SAID = {
    "drafts": ("{} draft", "{} drafts"),
    "submit": ("{} to submit", "{} to submit"),
    "views": ("{} view", "{} views"),
    "decide": ("{} decision", "{} decisions"),
    "payout": ("{} payout", "{} payouts"),
    "needs_you": ("{} post needs you", "{} posts need you"),
    "login": ("{} login", "{} logins"),
}
BY_ITEM = ("payout", "login")  # counted by item; the others by draft or post


def notify_time(engine: Engine, today: date) -> datetime:
    """When the day's notification is due, on the posting time zone's clock."""
    zone = ZoneInfo(engine.settings.posting.timezone)
    return datetime.combine(today, time.fromisoformat(engine.settings.today.notify_at), zone)


def _waiting(engine: Engine, day: str) -> bool:
    """Whether one of the day's clipping checks is still running or making its drafts, or a brief source is
    still to be read."""
    return brief_run.checks_waiting(engine, day) or not brief_run.reading_done(engine, day)


def tick(engine: Engine) -> bool:
    """The timer's look at the clock: builds the day once its notify time has come and the morning's drafts
    and briefs are in (or two hours have passed), and only once a day. True when it built."""
    now = engine.now()
    today = today_day.today_of(engine)
    day = today.isoformat()
    due = notify_time(engine, today)
    if now < due:
        return False
    conn = engine.db()
    plan = store.get_day(conn, day)
    if plan is not None and plan.notified_at is not None:
        return False
    if now < due + WAIT and _waiting(engine, day):
        return False
    if not store.claim_day(conn, day, now.timestamp()):
        return False
    try:
        build(engine, day)
    except Exception:
        store.release_day(conn, day)
        raise
    return True


def build(engine: Engine, day: str) -> Day:
    """Today's list brought up to date, with the seconds it plans and when it went, the next day's drafts
    cap set, and the notification sent when the daily one is on and something is still to do. The path's
    steps aren't planned seconds: they are set aside before anything else, so they never cut the drafts."""
    with engine.lock:
        conn = engine.db()
        items = today_day.refresh(engine)
        plan = store.get_day(conn, day) or Day(day=day)
        changes = {"planned_s": sum(i.estimate_s for i in items if i.kind != "path")}
        if plan.notified_at is None:
            changes["notified_at"] = engine.now().timestamp()
        built = store.put_day(conn, plan.model_copy(update=changes))
        cap = drafts_cap(engine, day)
        tomorrow = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
        after = store.get_day(conn, tomorrow)
        if cap is not None or after is not None:
            store.put_day(conn, (after or Day(day=tomorrow)).model_copy(update={"drafts_cap": cap}))
    if engine.settings.today.daily_notification and any(not i.done for i in items):
        engine.notifier.daily(body(items))
    return built


def body(items: list[TodoItem]) -> str:
    """The notification's words: the counts of what the 15 minutes hold, by kind, the rest as "N more"."""
    undone = [i for i in items if not i.done]
    named = [i for i in undone if not i.optional] or undone
    counts: Counter[str] = Counter()
    for item in named:
        counts[item.kind] += 1 if item.kind in BY_ITEM else max(1, len(item.ids))
    parts = [SAID[kind][counts[kind] != 1].format(counts[kind]) for kind in SAID if counts[kind]]
    more = sum(1 for i in named if i.kind not in SAID)
    if more:
        parts.append(f"{more} more")
    return "Your 15 minutes is ready: " + ", ".join(parts)


def drafts_limit(engine: Engine) -> int:
    """The most drafts the clipping channels that aren't paused make in a day, added up."""
    total = 0
    for channel in watch_store.list_channels(engine.db()):
        if channel.paused:
            continue
        for room in draft_store.day_capacity(engine, channel).values():
            total += room if channel.clips_per_day is None else min(room, channel.clips_per_day)
    return total


def drafts_cap(engine: Engine, day: str) -> int | None:
    """The drafts cap for the day after ``day``, from how ``day`` was built: lowered after a day over 15
    minutes, up one once the last three built days kept 2 minutes over at the same cap (while the clipping
    channels make more than it), else the same."""
    conn = engine.db()
    plan = store.get_day(conn, day)
    if plan is None or plan.planned_s is None:
        return plan.drafts_cap if plan is not None else None
    if plan.planned_s > BUDGET_S:
        held = sum(len(i.ids) for i in store.items_of(conn, day) if i.kind == "drafts")
        over = ceil((plan.planned_s - BUDGET_S) / today_day.unit_seconds(engine, "drafts"))
        return max(1, held - over)
    if plan.drafts_cap is None:
        return None
    built = [d for d in store.last_days(conn, day, LOOK_BACK) if d.planned_s is not None]
    streak = [plan, *built[: SHORT_DAYS - 1]]
    short = all(d.planned_s <= BUDGET_S - SPARE_S and d.drafts_cap == plan.drafts_cap for d in streak)
    if len(streak) == SHORT_DAYS and short and drafts_limit(engine) > plan.drafts_cap:
        return plan.drafts_cap + 1
    return plan.drafts_cap
