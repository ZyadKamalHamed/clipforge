import random
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from clipforge.config import PostingSettings, Window
from clipforge.post import slots
from clipforge.post.models import Account, AccountSettings

SYD = ZoneInfo("Australia/Sydney")
ALL_DAY = [Window(start="00:00", end="23:59")]


def local(y, m, d, hh, mm=0):
    """A Sydney wall-clock time as aware UTC."""
    return datetime(y, m, d, hh, mm, tzinfo=SYD).astimezone(UTC)


def account(platform="tiktok", **settings):
    return Account(
        id=f"a-{platform}",
        platform=platform,
        method="browser" if platform == "tiktok" else "api",
        settings=AccountSettings(**settings),
        created_at=0.0,
    )


def rules(platform="tiktok", *, jitter=0, windows=None, **account_settings):
    settings = PostingSettings(jitter_min=jitter, **({"windows": windows} if windows is not None else {}))
    return slots.rules_for(settings, account(platform, **account_settings))


def test_rules_come_from_settings_and_the_account():
    r = rules()
    assert (r.cap, r.spacing, r.platform_gap, r.jitter) == (
        2,
        timedelta(hours=3),
        timedelta(minutes=15),
        timedelta(0),
    )
    assert r.tz == SYD
    assert rules("instagram").cap == 1 and rules("instagram").spacing == timedelta(hours=6)
    own = rules(daily_cap=5, windows=[Window(start="10:00", end="11:00")])
    assert own.cap == 5
    assert slots.in_window(local(2026, 11, 3, 10, 30), own)
    assert not slots.in_window(local(2026, 11, 3, 7, 30), own)


def test_a_routes_ceiling_caps_the_account():
    """Instagram through Chrome posts once a day at most, whatever the account or Settings asks for."""
    settings = PostingSettings(instagram_daily_cap=3)
    chrome = account("instagram", daily_cap=5).model_copy(update={"method": "browser"})
    assert slots.rules_for(settings, chrome).cap == 1
    assert slots.rules_for(settings, chrome.model_copy(update={"settings": AccountSettings()})).cap == 1
    assert slots.rules_for(settings, account("instagram", daily_cap=5)).cap == 5  # Meta's own limit is 50
    assert slots.rules_for(settings, account("tiktok", daily_cap=10)).cap == 10


def test_windows_are_sydney_wall_clock():
    r = rules()
    assert slots.in_window(local(2026, 11, 3, 7, 30), r)
    assert not slots.in_window(local(2026, 11, 3, 6, 59), r)
    assert slots.in_window(local(2026, 11, 3, 21, 59), r)
    assert not slots.in_window(local(2026, 11, 3, 22, 0), r)
    assert not slots.in_window(local(2026, 11, 3, 10, 0), r)


def test_dst_start_and_end_keep_wall_clock():
    r = rules()
    for day in (date(2026, 10, 3), date(2026, 10, 4), date(2026, 10, 5), date(2027, 4, 3), date(2027, 4, 4)):
        seven = local(day.year, day.month, day.day, 7)
        assert slots.in_window(seven, r), day
        assert not slots.in_window(seven - timedelta(minutes=5), r), day
        found = slots.smart_slot(
            r, after=local(day.year, day.month, day.day, 0), mine=[], platform=[], rng=random.Random(1)
        )
        assert found == seven, day
        assert (found.astimezone(SYD).hour, found.astimezone(SYD).minute) == (7, 0)
    # the offsets really do differ across each change
    assert local(2026, 10, 3, 7).utcoffset() == timedelta(0)
    assert local(2026, 10, 3, 7).astimezone(SYD).utcoffset() == timedelta(hours=10)
    assert local(2026, 10, 4, 7).astimezone(SYD).utcoffset() == timedelta(hours=11)
    assert local(2027, 4, 4, 7).astimezone(SYD).utcoffset() == timedelta(hours=10)


def test_cap_counts_by_sydney_day_not_utc():
    r = rules()
    mine = [local(2026, 11, 3, 7), local(2026, 11, 3, 18)]  # one UTC day apart, one Sydney day
    assert mine[0].date() != mine[1].date()
    assert slots.sydney_day(mine[0], SYD) == slots.sydney_day(mine[1], SYD) == date(2026, 11, 3)
    assert "cap" in slots.check_slot(local(2026, 11, 3, 21, 30), r, mine=mine, platform=[])
    next_morning = local(2026, 11, 4, 7)
    assert next_morning.date() == mine[1].date()  # the same UTC day as the second post
    assert slots.check_slot(next_morning, r, mine=mine, platform=[]) == []


def test_smart_respects_daily_cap():
    r = rules()
    mine = [local(2026, 11, 3, 7), local(2026, 11, 3, 12)]
    third = slots.smart_slot(r, after=local(2026, 11, 3, 12), mine=mine, platform=[], rng=random.Random(2))
    assert slots.sydney_day(third, SYD) == date(2026, 11, 4)
    assert third == local(2026, 11, 4, 7)
    assert rules("instagram").cap == 1
    ig = slots.smart_slot(
        rules("instagram"),
        after=local(2026, 11, 3, 7),
        mine=[local(2026, 11, 3, 7)],
        platform=[],
        rng=random.Random(2),
    )
    assert slots.sydney_day(ig, SYD) == date(2026, 11, 4)


def test_smart_respects_account_spacing():
    for platform, hours in (("tiktok", 3), ("instagram", 6)):
        r = rules(platform, windows=ALL_DAY, daily_cap=5)
        mine = [local(2026, 11, 3, 10)]
        found = slots.smart_slot(
            r, after=local(2026, 11, 3, 10), mine=mine, platform=[], rng=random.Random(3)
        )
        assert found == local(2026, 11, 3, 10 + hours), platform
        # spacing counts posts before the slot as well as after it
        before = slots.smart_slot(
            r, after=local(2026, 11, 3, 8), mine=[local(2026, 11, 3, 9)], platform=[], rng=random.Random(3)
        )
        assert before == local(2026, 11, 3, 9 + hours), platform


def test_smart_respects_platform_gap():
    r = rules(windows=ALL_DAY)
    others = [local(2026, 11, 3, 7)]
    found = slots.smart_slot(r, after=local(2026, 11, 3, 7), mine=[], platform=others, rng=random.Random(4))
    assert found == local(2026, 11, 3, 7, 15)
    # the gap holds on both sides of the other account's post
    early = slots.smart_slot(
        r, after=local(2026, 11, 3, 6, 40), mine=[], platform=others, rng=random.Random(4)
    )
    assert early == local(2026, 11, 3, 6, 40)
    late = slots.smart_slot(
        r, after=local(2026, 11, 3, 6, 50), mine=[], platform=others, rng=random.Random(4)
    )
    assert late == local(2026, 11, 3, 7, 15)


def test_jitter_stays_valid():
    r = rules(jitter=10)
    draws = random.Random(99)
    rng = random.Random(5)
    moved = 0
    for _ in range(1000):
        after = local(2026, 11, 3, 0) + timedelta(minutes=draws.randrange(0, 60 * 24 * 3))
        platform = [after + timedelta(minutes=draws.randrange(0, 600)) for _ in range(draws.randrange(0, 4))]
        mine = [after - timedelta(hours=draws.randrange(1, 12))]
        found = slots.smart_slot(r, after=after, mine=mine, platform=platform, rng=rng)
        plain = slots.next_free(r, after=after, mine=mine, platform=platform)
        assert found is not None and plain is not None
        assert found >= after
        assert abs(found - plain) <= timedelta(minutes=10)
        assert slots.check_slot(found, r, mine=mine, platform=platform) == []
        assert found.tzinfo == UTC
        moved += found != plain
    assert moved > 300


def test_now_staggers_two_to_six_minutes():
    now = datetime(2026, 11, 3, 1, 2, 3, tzinfo=UTC)
    times = slots.now_slots(6, now=now, rng=random.Random(6))
    assert len(times) == 6 and times[0] == now
    gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
    assert all(timedelta(minutes=2) <= g <= timedelta(minutes=6) for g in gaps)
    assert len(set(gaps)) > 1
    assert slots.now_slots(0, now=now, rng=random.Random(6)) == []


def test_check_slot_codes_and_next_free():
    r = rules()
    seven = local(2026, 11, 3, 7)
    assert slots.check_slot(local(2026, 11, 3, 6), r, mine=[], platform=[]) == ["outside"]
    assert slots.check_slot(seven + timedelta(minutes=5), r, mine=[seven], platform=[]) == ["spacing"]
    assert slots.check_slot(seven + timedelta(minutes=5), r, mine=[], platform=[seven]) == ["gap"]
    full = [local(2026, 11, 3, 12), local(2026, 11, 3, 18)]
    assert slots.check_slot(local(2026, 11, 3, 21, 30), r, mine=full, platform=[]) == ["cap"]
    assert slots.check_slot(
        local(2026, 11, 3, 12, 30), r, mine=full, platform=[local(2026, 11, 3, 12, 20)]
    ) == [
        "cap",
        "spacing",
        "gap",
    ]
    assert slots.next_free(r, after=seven, mine=[seven], platform=[]) == local(2026, 11, 3, 12)
    # a time between steps waits for the next 5-minute step
    assert slots.next_free(r, after=seven + timedelta(seconds=30), mine=[], platform=[]) == seven + timedelta(
        minutes=5
    )


def test_search_gives_up_after_fourteen_days():
    r = rules(windows=[])
    start = local(2026, 11, 3, 7)
    assert slots.smart_slot(r, after=start, mine=[], platform=[], rng=random.Random(7)) is None
    assert slots.next_free(r, after=start, mine=[], platform=[]) is None
    r = rules("instagram", windows=[Window(start="07:00", end="07:05")])
    assert slots.next_free(r, after=start + timedelta(minutes=1), mine=[], platform=[]) == local(
        2026, 11, 4, 7
    )
    days = [start + timedelta(days=k) for k in range(20)]
    thirteen = [local(d.year, d.month, d.day, 7) for d in (x.astimezone(SYD) for x in days[:13])]
    fourteen = [local(d.year, d.month, d.day, 7) for d in (x.astimezone(SYD) for x in days[:14])]
    assert slots.next_free(r, after=start, mine=thirteen, platform=[]) == local(2026, 11, 16, 7)
    assert slots.next_free(r, after=start, mine=fourteen, platform=[]) is None
    assert slots.smart_slot(r, after=start, mine=fourteen, platform=[], rng=random.Random(7)) is None


def test_studio_slot_rounds_and_keeps_the_limits():
    now = local(2026, 11, 3, 7, 10)
    ahead = lambda **d: slots.studio_slot(now + timedelta(**d), now)  # noqa: E731
    assert ahead(minutes=19) is None  # it rounds to 20 ahead, but TikTok is given the slot itself first
    assert ahead(minutes=20) == local(2026, 11, 3, 7, 30)
    assert ahead(minutes=22) == local(2026, 11, 3, 7, 30)
    assert ahead(minutes=22, seconds=30) == local(2026, 11, 3, 7, 35)  # halfway rounds up
    assert ahead(days=10) == local(2026, 11, 13, 7, 10)
    assert ahead(days=10, minutes=5) is None
    assert ahead(minutes=-5) is None
    late = local(2026, 11, 30, 23, 58)
    assert slots.studio_slot(late, local(2026, 11, 25, 9)) == datetime(2026, 12, 1, 0, 0, tzinfo=SYD)
