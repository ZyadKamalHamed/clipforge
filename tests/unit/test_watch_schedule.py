import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from clipforge import repo
from clipforge.campaigns import store as campaigns
from clipforge.campaigns.models import Campaign
from clipforge.models import ProjectOptions
from clipforge.post import queue
from clipforge.watch import schedule, store
from clipforge.watch.models import ClipChannel, WatchedSource
from tests.fakes import Clock, FakeCommands, fake_engine

SYDNEY = ZoneInfo("Australia/Sydney")
URL = "https://www.youtube.com/watch?v=arj7oStGLkU"


def local(hour: int, minute: int = 0, *, day: int = 28, month: int = 10) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=SYDNEY)


@pytest.fixture
def clock():
    return Clock(local(5))


@pytest.fixture
def engine(clock):
    eng = fake_engine(now=clock)
    yield eng
    eng.close()


def channel(engine, *, sources: int = 1, **fields) -> ClipChannel:
    made = store.add_channel(
        engine.db(), ClipChannel(id=repo.new_id("cc"), name="Podcasts", created_at=0, updated_at=0, **fields)
    )
    for n in range(sources):
        yt = f"UC{n:022d}"
        store.add_source(
            engine.db(),
            WatchedSource(
                id=repo.new_id("ws"),
                clip_channel_id=made.id,
                yt_channel_id=yt,
                url=f"https://www.youtube.com/channel/{yt}/videos",
                start_published_at=0,
                created_at=0,
            ),
        )
    return made


def checked_on(engine, ch) -> str | None:
    return store.get_channel(engine.db(), ch.id).last_check_on


def finish(engine, check) -> None:
    store.update_check(engine.db(), check.id, finished_at=engine.now().timestamp())


def test_check_runs_at_its_time(engine, clock):
    ch = channel(engine)
    assert schedule.tick(engine) == []
    clock.set(local(5, 59))
    assert schedule.tick(engine) == []
    assert schedule.due_channels(engine, local(5, 59)) == []
    clock.set(local(6))
    assert [c.id for c in schedule.due_channels(engine, local(6))] == [ch.id]
    (made,) = schedule.tick(engine)
    assert (made.clip_channel_id, made.check_on, made.manual) == (ch.id, "2026-10-28", False)
    assert checked_on(engine, ch) == "2026-10-28"
    assert [j.kind for j in engine.runner.jobs_for(made.id)] == ["watch.check"]


def test_missed_check_runs_once_on_wake(engine, clock):
    ch = channel(engine)
    assert schedule.tick(engine) == []
    clock.set(local(11))  # the Mac slept through 06:00
    assert len(schedule.tick(engine)) == 1
    clock.set(local(11, 1))
    assert schedule.tick(engine) == []
    clock.set(local(23, 59))
    assert schedule.tick(engine) == []
    clock.set(local(6, day=29))
    (made,) = schedule.tick(engine)
    assert made.check_on == "2026-10-29"
    assert len(store.checks_for(engine.db(), ch.id)) == 2


def test_a_late_check_slept_through_past_midnight_runs_on_wake(engine, clock):
    """SPEC 10.9: a check whose time passed while the Mac slept runs once on waking, after midnight too,
    and counts for the day it was missed, so the next day's own check still comes at its time."""
    ch = channel(engine, check_time="23:30")
    clock.set(local(23, 30, day=27))
    assert len(schedule.tick(engine)) == 1
    clock.set(local(22))  # the Mac sleeps from just after this to 07:00 on the 29th
    assert schedule.tick(engine) == []
    clock.set(local(7, day=29))
    assert schedule.next_check_at(engine, store.get_channel(engine.db(), ch.id), local(7, day=29)) == local(
        7, day=29
    )
    assert len(schedule.tick(engine)) == 1
    assert checked_on(engine, ch) == "2026-10-28"
    assert schedule.tick(engine) == []
    clock.set(local(23, 30, day=29))
    assert len(schedule.tick(engine)) == 1
    assert checked_on(engine, ch) == "2026-10-29"


def test_never_twice_a_day_across_restarts(clock):
    clock.set(local(11))
    first = fake_engine(now=clock)
    try:
        channel(first)
        assert len(schedule.tick(first)) == 1
    finally:
        first.close()
    clock.set(local(11, 5))
    second = fake_engine(now=clock)
    try:
        assert schedule.tick(second) == []
    finally:
        second.close()


def test_changed_time_after_todays_check_waits_for_tomorrow(engine, clock):
    ch = channel(engine)
    clock.set(local(6))
    assert len(schedule.tick(engine)) == 1
    store.update_channel(engine.db(), ch.id, check_time="09:00")
    clock.set(local(9, 30))
    assert schedule.tick(engine) == []
    assert schedule.next_check_at(engine, store.get_channel(engine.db(), ch.id), local(9, 30)) == local(
        9, day=29
    )
    clock.set(local(8, 59, day=29))
    assert schedule.tick(engine) == []
    clock.set(local(9, day=29))
    assert len(schedule.tick(engine)) == 1


def test_an_earlier_time_before_todays_check_runs_now(engine, clock):
    ch = channel(engine, check_time="09:00")
    clock.set(local(7))
    assert schedule.tick(engine) == []
    store.update_channel(engine.db(), ch.id, check_time="06:30")
    assert len(schedule.tick(engine)) == 1


def test_skipped_local_time_runs_at_the_next_valid_moment(engine, clock):
    # Sydney's clocks went from 02:00 to 03:00 on Sunday 4 October 2026, so 02:30 never came
    ch = channel(engine, check_time="02:30")
    store.update_channel(engine.db(), ch.id, last_check_on="2026-10-03")
    before = datetime(2026, 10, 3, 15, 59, tzinfo=UTC)  # 01:59 standard time
    clock.set(before)
    assert schedule.tick(engine) == []
    assert schedule.next_check_at(engine, store.get_channel(engine.db(), ch.id), before) == datetime(
        2026, 10, 4, 3, 0, tzinfo=SYDNEY
    )
    clock.set(datetime(2026, 10, 3, 16, 0, tzinfo=UTC))  # 03:00 summer time, the first moment after
    (made,) = schedule.tick(engine)
    assert made.check_on == "2026-10-04"


def test_repeated_local_time_runs_at_the_first(engine, clock):
    # Sydney's clocks went back from 03:00 to 02:00 on Sunday 5 April 2026, so 02:30 came twice
    channel(engine, check_time="02:30")
    clock.set(datetime(2026, 4, 4, 15, 30, tzinfo=UTC))  # the first 02:30, summer time
    (made,) = schedule.tick(engine)
    assert made.check_on == "2026-04-05"
    clock.set(datetime(2026, 4, 4, 16, 30, tzinfo=UTC))  # the second
    assert schedule.tick(engine) == []


def test_paused_channel_never_checks(engine, clock):
    paused = channel(engine, paused=True)
    empty = channel(engine, sources=0)
    clock.set(local(6))
    assert schedule.tick(engine) == []
    assert schedule.next_check_at(engine, paused, local(6)) is None
    assert schedule.next_check_at(engine, empty, local(6)) is None
    assert checked_on(engine, paused) is None


def test_budget_zero_stops_linked_channels(engine, clock):
    now = time.time()
    spent = campaigns.add_campaign(
        engine.db(),
        Campaign(
            id=repo.new_id("cp"),
            name="Beast clips",
            marketplace="Whop",
            budget_left=0,
            created_at=now,
            updated_at=now,
        ),
    )
    linked = channel(engine, campaign_id=spent.id)
    plain = channel(engine)
    clock.set(local(6))
    assert [c.clip_channel_id for c in schedule.tick(engine)] == [plain.id]
    assert checked_on(engine, linked) is None
    assert schedule.next_check_at(engine, linked, local(6)) is None
    with pytest.raises(schedule.CampaignEnded) as err:
        schedule.check_now(engine, linked.id)
    assert (err.value.code, err.value.message) == ("campaign_ended", "Beast clips has no budget left.")
    campaigns.update_campaign(engine.db(), spent.id, budget_left=120.0)  # topped up: it checks again
    (made,) = schedule.tick(engine)
    assert made.clip_channel_id == linked.id


def test_next_check_is_today_then_tomorrow(engine, clock):
    ch = channel(engine)
    assert schedule.next_check_at(engine, ch, local(5)) == local(6)
    assert schedule.next_check_at(engine, ch, local(7)) == local(7)  # missed: the next tick
    clock.set(local(7))
    schedule.tick(engine)
    assert schedule.next_check_at(engine, store.get_channel(engine.db(), ch.id), local(7)) == local(6, day=29)


def test_check_now_once_per_ten_minutes(engine, clock):
    ch = channel(engine)
    first = schedule.check_now(engine, ch.id)
    assert first.manual
    assert checked_on(engine, ch) is None  # the day's own check still runs at 06:00
    finish(engine, first)
    clock.advance(minutes=4)
    with pytest.raises(schedule.TooSoon) as err:
        schedule.check_now(engine, ch.id)
    assert err.value.message == "Checked 4 minutes ago. Check again after 5:10 am."
    clock.advance(minutes=5, seconds=59)
    with pytest.raises(schedule.TooSoon):
        schedule.check_now(engine, ch.id)
    clock.advance(seconds=1)
    second = schedule.check_now(engine, ch.id)
    assert second.id != first.id
    assert len(store.checks_for(engine.db(), ch.id)) == 2


def test_check_now_counts_the_days_check(engine, clock):
    ch = channel(engine)
    clock.set(local(6))
    (daily,) = schedule.tick(engine)
    finish(engine, daily)
    clock.set(local(6, 1))
    with pytest.raises(schedule.TooSoon) as err:
        schedule.check_now(engine, ch.id)
    assert err.value.message == "Checked 1 minute ago. Check again after 6:10 am."


def test_check_now_after_a_rate_limit_waits_for_tomorrow(engine, clock):
    ch = channel(engine)
    clock.set(local(6))
    (daily,) = schedule.tick(engine)
    with engine.lock:
        store.update_check(engine.db(), daily.id, finished_at=engine.now().timestamp(), error="rate_limited")
    clock.set(local(9))
    with pytest.raises(schedule.TooSoon) as err:
        schedule.check_now(engine, ch.id)
    assert err.value.message == schedule.SLOWED
    assert len(store.checks_for(engine.db(), ch.id)) == 1
    assert schedule.check_now_after(engine, store.get_channel(engine.db(), ch.id), local(9)) == local(
        6, day=29
    )
    clock.set(local(6, day=29))
    assert schedule.check_now(engine, ch.id).id != daily.id


def test_check_now_while_one_runs_gives_that_one(engine, clock):
    ch = channel(engine)
    first = schedule.check_now(engine, ch.id)
    clock.advance(minutes=30)
    assert schedule.check_now(engine, ch.id).id == first.id
    assert len(store.checks_for(engine.db(), ch.id)) == 1


def test_check_now_unknown_channel(engine):
    with pytest.raises(LookupError):
        schedule.check_now(engine, "ccnothere")


def test_busy_holds_the_mac_awake(clock):
    cmds = FakeCommands()
    engine = fake_engine(now=clock, run_cmd=cmds)
    try:
        assert not schedule.busy(engine)
        (hand,), _ = engine.add_sources(URL, ProjectOptions())
        engine.start(hand.id)
        assert not schedule.busy(engine)  # a project started by hand is watched over by you
        queue.tick(engine)
        assert cmds.calls == []

        ch = channel(engine)
        check = schedule.check_now(engine, ch.id)
        assert schedule.busy(engine)
        queue.tick(engine)
        assert cmds.calls == [["caffeinate", "-i", "-t", "900"]]

        engine.db().execute("UPDATE jobs SET status = 'done' WHERE ref_id = ?", (check.id,))
        engine.db().commit()
        assert not schedule.busy(engine)
        (watched,), _ = engine.add_sources(URL, ProjectOptions(), new=True)
        engine.update_project(watched.id, clip_channel_id=ch.id)
        engine.start(watched.id)
        assert schedule.busy(engine)

        later = time.time() + 3600  # waiting out a usage limit holds nothing awake (the runner's clock)
        engine.db().execute("UPDATE jobs SET not_before = ? WHERE ref_id = ?", (later, watched.id))
        engine.db().commit()
        assert not schedule.busy(engine)
        queue.tick(engine)
        assert cmds.alive() == []
    finally:
        engine.close()
