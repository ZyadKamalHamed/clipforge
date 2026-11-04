"""The morning (SPEC 14.10, 18): from your notify time the day's list is built once, after the morning's
clipping checks have made their drafts or two hours on, and the one daily notification says what waits.
Tomorrow's drafts cap follows how long today was planned to take: down when it went over 15 minutes, up by
one after three short days, never past what the clipping channels can make."""

import pytest

from clipforge.briefs import run
from clipforge.briefs import store as brief_store
from clipforge.today import day, morning
from clipforge.today import store as today_store
from clipforge.today.models import Day, Need
from clipforge.watch import store as watch_store
from tests.fakes import Clock, fake_engine
from tests.posting import add_account, at
from tests.watching import add_channel, add_check

TODAY = "2026-11-03"


@pytest.fixture
def clock():
    return Clock(at(7, 0))


def no_briefs(eng):
    """``eng`` with Campaign Scout turned off, so no brief source is due and the morning builds on time."""
    brief_store.update_source(eng.db(), "bs_scout", enabled=False)
    return eng


@pytest.fixture
def engine(clock):
    eng = no_briefs(fake_engine(now=clock))
    yield eng
    eng.close()


@pytest.fixture
def found(monkeypatch):
    """What needs you, as the test sets it: a list each refresh reads."""
    current: list[Need] = []
    monkeypatch.setattr(day, "needs", lambda engine, today: list(current))
    return current


def need(kind: str, n: int, ref: str = "r") -> Need:
    return Need(kind, ref, [f"{kind}{ref}{k}" for k in range(n)], f"{n} {kind} ({ref})")


def sent(engine) -> list[str]:
    """The bodies of the daily notifications the engine started."""
    return [arg for call in engine.run_cmd.calls for arg in call if arg.startswith("Your 15 minutes")]


def set_today(engine, **fields) -> None:
    engine.update_settings(
        engine.settings.model_copy(update={"today": engine.settings.today.model_copy(update=fields)})
    )


def test_nothing_before_notify_time(engine, clock, found):
    found.append(need("drafts", 2))
    clock.set(at(7, 29))
    assert morning.tick(engine) is False
    assert sent(engine) == []
    assert today_store.get_day(engine.db(), TODAY) is None
    set_today(engine, notify_at="06:15")
    assert morning.tick(engine) is True  # a start after the time builds at once
    assert sent(engine) == ["Your 15 minutes is ready: 2 drafts"]


def test_builds_once_and_notifies(clock, found):
    found.append(need("drafts", 6))
    first = no_briefs(fake_engine(now=clock))
    try:
        clock.set(at(7, 30))
        assert morning.tick(first) is True
        assert sent(first) == ["Your 15 minutes is ready: 6 drafts"]
        built = today_store.get_day(first.db(), TODAY)
        assert (built.notified_at, built.planned_s) == (at(7, 30).timestamp(), 120)
        clock.set(at(7, 31))
        assert morning.tick(first) is False
        assert len(sent(first)) == 1
    finally:
        first.close()
    second = fake_engine(now=clock)  # ClipForge opened again on the same database
    try:
        assert morning.tick(second) is False
        assert sent(second) == []
        clock.set(at(15, 0, day=4))
        assert morning.tick(second) is True  # the next day, opened in the afternoon
        assert sent(second) == ["Your 15 minutes is ready: 6 drafts"]
    finally:
        second.close()


def test_waits_for_the_mornings_drafts(engine, clock, found):
    ch = add_channel(engine, [add_account(engine, "tiktok", "t0")])
    check = add_check(engine, ch, finished=False)
    found.append(need("drafts", 3))
    clock.set(at(7, 30))
    assert morning.tick(engine) is False
    watch_store.update_check(engine.db(), check.id, finished_at=at(7, 40).timestamp())
    clock.set(at(8, 0))
    assert morning.tick(engine) is False  # finished, but its drafts aren't made yet
    clock.set(at(9, 29))
    assert morning.tick(engine) is False
    clock.set(at(9, 30))
    assert morning.tick(engine) is True  # two hours on, with what is ready
    assert sent(engine) == ["Your 15 minutes is ready: 3 drafts"]

    later = add_check(engine, ch)
    watch_store.update_check(engine.db(), later.id, check_on="2026-11-04", drafts=2, drafted_at=1.0)
    clock.set(at(7, 30, day=4))
    assert morning.tick(engine) is True  # its drafts are made, and yesterday's open check isn't today's


def test_morning_waits_for_the_briefs(engine, clock, found):
    brief_store.update_source(engine.db(), "bs_scout", enabled=True)
    found.append(need("drafts", 2))
    clock.set(at(7, 30))
    assert morning.tick(engine) is False  # Campaign Scout is still to be read
    clock.set(at(9, 29))
    assert morning.tick(engine) is False
    clock.set(at(9, 30))
    assert morning.tick(engine) is True  # two hours on, without it
    clock.set(at(7, 30, day=4))
    assert morning.tick(engine) is False
    assert run.tick(engine) == 1
    assert morning.tick(engine) is True  # read, so the list is built at once


def test_quiet_when_empty_or_off(engine, clock, found):
    clock.set(at(7, 30))
    assert morning.tick(engine) is True
    assert sent(engine) == []
    assert today_store.get_day(engine.db(), TODAY).notified_at == at(7, 30).timestamp()
    found.append(need("drafts", 2))
    set_today(engine, daily_notification=False)
    clock.set(at(7, 30, day=4))
    assert morning.tick(engine) is True
    assert sent(engine) == []


def test_body_names_the_counts(engine, found):
    found.extend(
        [
            need("payout", 2, "Whop:USD"),  # one payout, however many approvals it holds
            need("submit", 3, "c1"),
            need("drafts", 6, "ch1"),
            need("submit", 1, "c2"),
        ]
    )
    assert morning.body(day.refresh(engine)) == "Your 15 minutes is ready: 6 drafts, 4 to submit, 1 payout"
    found[:] = [
        need("exported", 1, "all"),
        need("login", 1, "a1"),
        need("needs_you", 1, "all"),
        need("payout", 1, "Whop:USD"),
        need("decide", 1, "whop"),
        need("views", 1, "tiktok"),
        need("submit", 1, "c1"),
        need("drafts", 1, "ch1"),
        need("ends", 1, "c1"),
    ]
    assert morning.body(day.refresh(engine)) == (
        "Your 15 minutes is ready: 1 draft, 1 to submit, 1 view, 1 decision, 1 payout, 1 post needs you,"
        " 1 login, 2 more"
    )


def test_cap_lowers_after_a_long_day(engine, clock, found):
    found.extend([need("drafts", 12, "ch1"), need("views", 86, "tiktok")])  # 240 s and 860 s
    built = morning.build(engine, TODAY)
    assert built.planned_s == 1100
    assert today_store.get_day(engine.db(), "2026-11-04").drafts_cap == 2  # 200 s over is 10 drafts
    found[:] = [need("drafts", 2, "ch1"), need("views", 200, "tiktok")]
    clock.set(at(7, 30, day=4))
    morning.build(engine, "2026-11-04")
    assert today_store.get_day(engine.db(), "2026-11-05").drafts_cap == 1  # never under one


def test_cap_rises_after_three_short_days(engine, clock, found):
    ch = add_channel(
        engine, [add_account(engine, "tiktok", "t0", daily_cap=2), add_account(engine, "instagram", "i0")]
    )
    add_channel(engine, [add_account(engine, "tiktok", "rest", daily_cap=5)], name="Resting", paused=True)
    assert morning.drafts_limit(engine) == 3  # the paused channel makes none
    found.append(need("drafts", 1, ch.id))
    for n in (1, 2, 3):
        clock.set(at(7, 30, day=n))
        morning.tick(engine)
    assert morning.drafts_cap(engine, TODAY) is None  # with no cap set it stays none
    today_store.put_day(engine.db(), Day(day="2026-11-04", drafts_cap=1))
    caps = []
    for n in range(4, 14):
        clock.set(at(7, 30, day=n))
        assert morning.tick(engine) is True
        caps.append(today_store.get_day(engine.db(), f"2026-11-{n + 1:02d}").drafts_cap)
    assert caps == [1, 1, 2, 2, 2, 3, 3, 3, 3, 3]  # up one after each three short days, up to the channels


def test_cap_holds_when_the_channels_make_fewer(engine, clock, found):
    ch = add_channel(engine, [add_account(engine, "tiktok", "t0", daily_cap=5)], paused=True)
    assert morning.drafts_limit(engine) == 0  # a paused channel makes none
    today_store.put_day(engine.db(), Day(day="2026-11-04", drafts_cap=4))
    found.append(need("drafts", 1, ch.id))
    caps = []
    for n in (4, 5, 6):
        clock.set(at(7, 30, day=n))
        assert morning.tick(engine) is True
        caps.append(today_store.get_day(engine.db(), f"2026-11-{n + 1:02d}").drafts_cap)
    assert caps == [4, 4, 4]  # three short days raise it only as far as the channels make, never lower


def test_the_paths_steps_never_cut_the_drafts(engine, found):
    found.extend(
        [
            need("drafts", 2, "ch1"),
            Need("path", "instagram", ["instagram"], "Make an Instagram account", estimate_s=900),
        ]
    )
    built = morning.build(engine, TODAY)
    assert built.planned_s == 40  # the two drafts; the path waits for a longer day instead
    after = today_store.get_day(engine.db(), "2026-11-04")
    assert after is None or after.drafts_cap is None
