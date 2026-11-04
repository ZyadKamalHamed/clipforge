"""When a post can go out (SPEC 10.4, call 8): inside a posting window in Sydney wall-clock time, under the
account's daily cap for that Sydney day, far enough from the account's other posts and from every other
account's post on the platform. All times are aware UTC in and out."""

from __future__ import annotations

import random
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from clipforge.config import PostingSettings
from clipforge.post.models import Account

STEP = timedelta(minutes=5)
SEARCH = timedelta(days=14)
NOW_STAGGER_S = (120.0, 360.0)  # Post now: each next account 2 to 6 minutes after the one before
# What TikTok Studio's own schedule takes (research 02, 2026-09-30): 20 minutes to 10 days ahead.
STUDIO_LEAD = timedelta(minutes=20)
STUDIO_AHEAD = timedelta(days=10)


@dataclass(frozen=True)
class Rules:
    cap: int
    spacing: timedelta
    platform_gap: timedelta
    windows: tuple[tuple[time, time], ...]
    tz: ZoneInfo
    jitter: timedelta


def _clock(text: str) -> time:
    hours, minutes = text.split(":")
    return time(int(hours), int(minutes))


def rules_for(settings: PostingSettings, account: Account) -> Rules:
    """The account's own cap and windows where it has them, else the platform's, the cap held to its
    route's most a day."""
    from clipforge.post.plan import caps_for  # here, since the plan reads its rules from this module

    tiktok = account.platform == "tiktok"
    cap = account.settings.daily_cap or (
        settings.tiktok_daily_cap if tiktok else settings.instagram_daily_cap
    )
    cap = min(cap, caps_for(account).daily_cap)
    windows = account.settings.windows if account.settings.windows is not None else settings.windows
    return Rules(
        cap=cap,
        spacing=timedelta(hours=settings.tiktok_spacing_h if tiktok else settings.instagram_spacing_h),
        platform_gap=timedelta(minutes=settings.platform_gap_min),
        windows=tuple((_clock(w.start), _clock(w.end)) for w in windows),
        tz=ZoneInfo(settings.timezone),
        jitter=timedelta(minutes=settings.jitter_min),
    )


def sydney_day(t: datetime, tz: ZoneInfo) -> date:
    """The day ``t`` falls on in ``tz``, the day a daily cap counts by."""
    return t.astimezone(tz).date()


def in_window(t: datetime, rules: Rules) -> bool:
    """Whether ``t`` is inside a window, start included and end not, in wall-clock time."""
    clock = t.astimezone(rules.tz).time()
    return any(start <= clock < end for start, end in rules.windows)


def check_slot(
    at: datetime, rules: Rules, *, mine: Iterable[datetime], platform: Iterable[datetime]
) -> list[str]:
    """The rules ``at`` breaks, in order: ``outside``, ``cap``, ``spacing``, ``gap``. Empty when it is free.
    ``mine`` are the account's posts, ``platform`` the other accounts' posts on the platform."""
    mine, platform = list(mine), list(platform)
    codes = []
    if not in_window(at, rules):
        codes.append("outside")
    day = sydney_day(at, rules.tz)
    if sum(sydney_day(m, rules.tz) == day for m in mine) >= rules.cap:
        codes.append("cap")
    if any(abs(at - m) < rules.spacing for m in mine):
        codes.append("spacing")
    if any(abs(at - p) < rules.platform_gap for p in platform):
        codes.append("gap")
    return codes


def _steps(after: datetime):
    """Every 5-minute step from ``after`` (rounded up to one) for 14 days, in UTC."""
    after = after.astimezone(UTC)
    first = after.replace(second=0, microsecond=0)
    first -= timedelta(minutes=first.minute % 5)
    if first < after:
        first += STEP
    t, end = first, after + SEARCH
    while t < end:
        yield t
        t += STEP


def next_free(
    rules: Rules, *, after: datetime, mine: Iterable[datetime], platform: Iterable[datetime]
) -> datetime | None:
    """The first free 5-minute step from ``after``, without jitter; None when 14 days hold none."""
    mine, platform = list(mine), list(platform)
    for t in _steps(after):
        if in_window(t, rules) and not check_slot(t, rules, mine=mine, platform=platform):
            return t
    return None


def smart_slot(
    rules: Rules,
    *,
    after: datetime,
    mine: Iterable[datetime],
    platform: Iterable[datetime],
    rng: random.Random,
) -> datetime | None:
    """The first free step from ``after`` moved by up to ``jitter`` either way, when the moved time is still
    free and not before ``after``; else the step itself. None when 14 days hold no free step."""
    mine, platform = list(mine), list(platform)
    step = next_free(rules, after=after, mine=mine, platform=platform)
    if step is None:
        return None
    if rules.jitter:
        shift = rng.uniform(-rules.jitter.total_seconds(), rules.jitter.total_seconds())
        moved = (step + timedelta(seconds=round(shift))).astimezone(UTC)
        if moved >= after and not check_slot(moved, rules, mine=mine, platform=platform):
            return moved
    return step


def now_slots(count: int, *, now: datetime, rng: random.Random) -> list[datetime]:
    """Post now's times: the first account at ``now``, each next one 2 to 6 minutes after the one before."""
    times: list[datetime] = []
    t = now.astimezone(UTC)
    for k in range(count):
        if k:
            t += timedelta(seconds=round(rng.uniform(*NOW_STAGGER_S)))
        times.append(t)
    return times


def studio_round(slot: datetime) -> datetime:
    """``slot`` as TikTok Studio's schedule holds it: rounded to the nearest 5 minutes, halfway up, as its
    minutes go in fives."""
    utc = slot.astimezone(UTC)
    down = utc.replace(second=0, microsecond=0) - timedelta(minutes=utc.minute % 5)
    return down + STEP if utc - down >= STEP / 2 else down


def studio_slot(slot: datetime, now: datetime) -> datetime | None:
    """The time TikTok Studio's schedule is set to for ``slot`` (``studio_round``). None when the slot or
    that time is under 20 minutes from ``now`` or over 10 days, which TikTok Studio won't take."""
    utc, rounded = slot.astimezone(UTC), studio_round(slot)
    if min(utc, rounded) - now < STUDIO_LEAD or max(utc, rounded) - now > STUDIO_AHEAD:
        return None
    return rounded
