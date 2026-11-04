"""When the clipping channels check (SPEC 10.9): each once a day at its check time in the posting time zone,
once on the next start or wake when the time passed while the Mac was asleep or off, and never twice for
the same day. "Check now" runs one more, at most once every 10 minutes. While a check or the projects it
started are running, the Mac is kept awake."""

from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from clipforge.campaigns import store as campaigns
from clipforge.campaigns.ending import end_reason
from clipforge.errors import ClipForgeError
from clipforge.watch import check, store
from clipforge.watch.models import footage_key

if TYPE_CHECKING:
    from clipforge.app import Engine
    from clipforge.watch.models import ClipChannel, WatchCheck

CHECK_NOW_GAP_S = 600
TOO_SOON = "Checked {ago} ago. Check again after {time}."
SLOWED = "YouTube asked this Mac to slow down, so this clipping channel waits until tomorrow's check."


class TooSoon(ClipForgeError):
    """Check now within 10 minutes of the clipping channel's last check."""


class CampaignEnded(ClipForgeError):
    """Check now on a clipping channel whose campaign ended."""


def _zone(engine: Engine) -> ZoneInfo:
    return ZoneInfo(engine.settings.posting.timezone)


def check_moment(day: date, check_time: str, zone: ZoneInfo) -> datetime:
    """When ``check_time`` comes on ``day``. On the day the clocks go back it is the first of the two; on the
    day they go forward past it, the first moment after the gap."""
    hour, minute = map(int, check_time.split(":"))
    wall = datetime(day.year, day.month, day.day, hour, minute)
    moment = wall.replace(tzinfo=zone)
    if moment.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == wall:  # a time that exists
        return moment
    # In the gap: between ``wall`` read with the offset after the change and with the one before, the
    # first minute whose clock reads ``wall`` or later is the end of the gap
    at = min(wall.replace(tzinfo=zone, fold=f).astimezone(UTC) for f in (0, 1))
    while at.astimezone(zone).replace(tzinfo=None) < wall:
        at += timedelta(minutes=1)
    return at.astimezone(zone)


def _today(now: datetime, zone: ZoneInfo) -> date:
    return now.astimezone(zone).date()


def _watches(engine: Engine, channel: ClipChannel) -> bool:
    """Not paused, and watching a YouTube channel or holding its campaign's footage videos (a source left
    from a campaign it was linked to only keeps that campaign's history)."""
    if channel.paused:
        return False
    footage = footage_key(channel.campaign_id) if channel.campaign_id else None
    return any(
        not s.is_footage or s.yt_channel_id == footage for s in store.list_sources(engine.db(), channel.id)
    )


def ended(engine: Engine, channel: ClipChannel, now: datetime) -> str | None:
    """Why the campaign the clipping channel is linked to is over, or None while it runs or without one."""
    if channel.campaign_id is None:
        return None
    return end_reason(campaigns.get_campaign(engine.db(), channel.campaign_id), _today(now, _zone(engine)))


def _due_day(engine: Engine, channel: ClipChannel, now: datetime) -> date | None:
    """The day whose check is due at ``now``: today's once its time has passed, or else yesterday's when
    that went unchecked, as when the Mac slept through a late check time and past midnight. A clipping
    channel never checked waits for its time."""
    zone = _zone(engine)
    today = _today(now, zone)
    last = channel.last_check_on
    if not _watches(engine, channel) or last == today.isoformat() or ended(engine, channel, now):
        return None
    if now >= check_moment(today, channel.check_time, zone):
        return today
    yesterday = today - timedelta(days=1)
    return yesterday if last is not None and last < yesterday.isoformat() else None


def _due(engine: Engine, channel: ClipChannel, now: datetime) -> bool:
    return _due_day(engine, channel, now) is not None


def due_channels(engine: Engine, now: datetime) -> list[ClipChannel]:
    """The clipping channels whose check is due at ``now``: watching (not paused, with a source), not
    linked to a campaign that ended, and past today's check time and not checked today, or with
    yesterday's check missed."""
    return [c for c in store.list_channels(engine.db()) if _due(engine, c, now)]


def tick(engine: Engine) -> list[WatchCheck]:
    """The minute's look at the clock: a check for each clipping channel that is due."""
    started = []
    now = engine.now()
    for channel in due_channels(engine, now):
        with engine.lock:  # its day is marked as the check starts, so a second tick starts none
            fresh = store.get_channel(engine.db(), channel.id)
            day = _due_day(engine, fresh, now) if fresh is not None else None
            if day is not None:
                started.append(check.start_check(engine, channel.id, manual=False, day=day.isoformat()))
    return started


def next_check_at(engine: Engine, channel: ClipChannel, now: datetime) -> datetime | None:
    """When the clipping channel checks next: today's check time, or tomorrow's once today's check ran;
    ``now`` for a check that is due. None while it doesn't watch (paused, or no source) or its campaign
    ended."""
    if not _watches(engine, channel) or ended(engine, channel, now):
        return None
    if _due(engine, channel, now):
        return now
    zone = _zone(engine)
    today = _today(now, zone)
    if channel.last_check_on == today.isoformat():
        return check_moment(today + timedelta(days=1), channel.check_time, zone)
    return max(check_moment(today, channel.check_time, zone), now)


def _opens(engine: Engine, channel: ClipChannel, last: WatchCheck | None) -> datetime | None:
    """When Check now opens after ``last``: 10 minutes after it started, or, when YouTube asked this Mac to
    slow down during it, at the next day's check time."""
    if last is None:
        return None
    zone = _zone(engine)
    if last.error == "rate_limited":
        return check_moment(date.fromisoformat(last.check_on) + timedelta(days=1), channel.check_time, zone)
    return datetime.fromtimestamp(last.started_at + CHECK_NOW_GAP_S, zone)


def check_now_after(engine: Engine, channel: ClipChannel, now: datetime) -> datetime | None:
    """When Check now opens again, or None while it is open."""
    opens = _opens(engine, channel, next(iter(store.checks_for(engine.db(), channel.id, limit=1)), None))
    return opens if opens is not None and now < opens else None


def check_now(engine: Engine, channel_id: str) -> WatchCheck:
    """A check of the clipping channel now, beside its daily one. A check still running is given back
    instead of a second; within 10 minutes of the last check's start, TooSoon says when it can run, and
    after YouTube asked this Mac to slow down, it waits for the next day's check. CampaignEnded says why a
    channel whose campaign ended doesn't check."""
    conn = engine.db()
    with engine.lock:
        channel = store.get_channel(conn, channel_id)
        if channel is None:
            raise LookupError(channel_id)
        last = next(iter(store.checks_for(conn, channel_id, limit=1)), None)
        if last is not None and last.finished_at is None:
            return last
        now = engine.now()
        reason = ended(engine, channel, now)
        if reason is not None:
            raise CampaignEnded("campaign_ended", reason)
        opens = _opens(engine, channel, last)
        if last is not None and opens is not None and now < opens:
            if last.error == "rate_limited":
                raise TooSoon("too_soon", SLOWED)
            minutes = int((now.timestamp() - last.started_at) // 60)
            ago = f"{minutes} minute" if minutes == 1 else f"{minutes} minutes"
            raise TooSoon("too_soon", TOO_SOON.format(ago=ago, time=f"{opens:%-I:%M %p}".lower()))
        return check.start_check(engine, channel_id, manual=True)


def busy(engine: Engine) -> bool:
    """Whether a check, or a project a check started, has a job running or ready to run. One waiting out a
    usage limit doesn't count: nothing runs until it is over."""
    row = (
        engine.db()
        .execute(
            "SELECT 1 FROM jobs WHERE (status = 'running' OR (status = 'queued' AND not_before <= ?))"
            " AND (kind = ? OR ref_id IN (SELECT id FROM projects WHERE clip_channel_id IS NOT NULL)"
            " OR json_extract(payload_json, '$.project') IN"
            " (SELECT id FROM projects WHERE clip_channel_id IS NOT NULL)) LIMIT 1",
            (time.time(), check.KIND),
        )
        .fetchone()
    )
    return row is not None
