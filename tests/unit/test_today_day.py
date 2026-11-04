"""Today's list (SPEC 14.10, 15): one row a day for each thing that needs you, kept in step with it, done by
the app when it goes and by your tick, in order of what it could earn and then the quicker first, with the
lowest-value items that aren't due set aside as optional to keep the day to 15 minutes, and the time each
kind of item takes learnt from your own timings."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from clipforge.post import store as post_store
from clipforge.today import day
from clipforge.today import store as today_store
from clipforge.today.models import Day, Need, Timing
from tests.fakes import Clock, fake_engine
from tests.posting import add_account, add_clip, at, custom

SYDNEY = ZoneInfo("Australia/Sydney")
TODAY = "2026-11-03"


@pytest.fixture
def clock():
    return Clock(at(9, 0))


@pytest.fixture
def engine(clock):
    eng = fake_engine(now=clock)
    yield eng
    eng.close()


@pytest.fixture
def found(monkeypatch):
    """What needs you, as the test sets it: a list the refresh reads each time."""
    current: list[Need] = []
    monkeypatch.setattr(day, "needs", lambda engine, today: list(current))
    return current


@pytest.fixture
def todo_events(engine):
    seen: list[dict] = []
    engine.events.listen(lambda e: seen.append(e.data) if e.topic == "todo" else None)
    return seen


def submit(ids, value=0.0, *, ref="c1", **fields) -> Need:
    return Need(
        "submit", ref, list(ids), f"Submit {len(ids)} live posts to Whop (Beast clips)", value=value, **fields
    )


def keys(items) -> list[tuple[str, str]]:
    return [(i.kind, i.ref) for i in items]


def with_rates(engine, rates: dict[str, float]) -> None:
    engine.update_settings(
        engine.settings.model_copy(
            update={"today": engine.settings.today.model_copy(update={"rates": rates})}
        )
    )


def test_first_refresh_makes_the_day(engine, found, todo_events):
    found.append(submit(["p1", "p2"], 2.0, guess=True, due=True, campaigns={"c1": 2}))
    found.append(Need("exported", "all", ["p3"], "Put up 1 exported post"))
    items = day.refresh(engine)

    assert keys(items) == [("submit", "c1"), ("exported", "all")]
    first = items[0]
    assert (first.day, first.ids, first.title, first.estimate_s) == (
        TODAY,
        ["p1", "p2"],
        "Submit 2 live posts to Whop (Beast clips)",
        30,  # 15 s a post by default
    )
    assert (first.value, first.currency, first.guess, first.due, first.campaigns) == (
        2.0,
        "USD",
        True,
        True,
        {"c1": 2},
    )
    assert (first.carried_from, first.done, first.optional, first.as_if_usd) == (None, False, False, False)
    assert items[1].estimate_s == 60
    assert today_store.get_day(engine.db(), TODAY) == Day(day=TODAY)
    assert todo_events == [{"day": TODAY}]

    again = day.refresh(engine)
    assert again == items  # nothing changed, so nothing is written or published
    assert todo_events == [{"day": TODAY}]
    found[1] = Need("exported", "all", ["p3", "p4"], "Put up 2 exported posts")
    (_, exported) = day.refresh(engine)
    assert (exported.id, exported.title, exported.ids, exported.estimate_s) == (
        items[1].id,
        "Put up 2 exported posts",
        ["p3", "p4"],
        120,
    )
    assert todo_events == [{"day": TODAY}, {"day": TODAY}]


def test_refresh_reads_what_needs_you(engine):
    account = add_account(engine)
    post = custom(engine, add_clip(engine, 1), account, at(19))
    post_store.update_post(engine.db(), post.id, status="exported")
    (item,) = day.refresh(engine)
    assert (item.kind, item.ref, item.ids, item.title) == (
        "exported",
        "all",
        [post.id],
        "Put up 1 exported post",
    )


def test_item_done_by_the_app_when_its_need_goes(engine, found, clock):
    found.append(submit(["p1", "p2"]))
    found.append(Need("exported", "all", ["p3"], "Put up 1 exported post"))
    (item, _) = day.refresh(engine)
    day.start(engine, item.id)
    clock.advance(seconds=40)
    del found[0]
    items = day.refresh(engine)

    assert keys(items) == [("exported", "all"), ("submit", "c1")]  # done after the rest
    done = items[1]
    assert (done.done, done.done_how, done.done_ids, done.done_at) == (
        True,
        "app",
        ["p1", "p2"],
        clock().timestamp(),
    )
    (timing,) = today_store.timings_of(engine.db(), "submit")
    assert (timing.seconds, timing.units, timing.started_at) == (40.0, 2, at(9, 0).timestamp())


def test_app_done_item_reopens_when_it_comes_back(engine, found):
    found.append(submit(["p1", "p2"]))
    (item,) = day.refresh(engine)
    found.clear()
    assert day.refresh(engine)[0].done_how == "app"
    found.append(submit(["p1"]))  # the same post needs you again
    (back,) = day.refresh(engine)
    assert (back.id, back.done, back.done_how, back.done_ids, back.ids, back.started_at) == (
        item.id,
        False,
        None,
        [],
        ["p1"],
        None,
    )
    assert len(today_store.items_of(engine.db(), TODAY)) == 1


def test_hand_tick_reopens_only_for_something_new(engine, found, clock):
    """Review Focus 2: the tick holds for what it covered; it opens again only for a post it didn't."""
    found.append(submit(["p1", "p2", "p3", "p4"]))
    (item,) = day.refresh(engine)
    ticked = day.tick(engine, item.id)
    assert (ticked.done, ticked.done_how, ticked.done_ids) == (True, "hand", ["p1", "p2", "p3", "p4"])
    clock.advance(minutes=1)
    assert day.tick(engine, item.id) == ticked  # a second tick changes nothing

    assert day.refresh(engine) == [ticked]  # the same four still need submitting in ClipForge
    found[0] = submit(["p1", "p2", "p3"])
    assert day.refresh(engine) == [ticked]
    found.clear()
    assert day.refresh(engine) == [ticked]  # ticked by hand, it stays so when its need goes

    found.append(submit(["p1", "p2", "p3", "p4", "p5"]))  # a fifth post went live
    (back,) = day.refresh(engine)
    assert (back.id, back.done, back.done_how, back.done_ids, back.ids, back.title) == (
        item.id,
        False,
        None,
        [],
        ["p1", "p2", "p3", "p4", "p5"],
        "Submit 5 live posts to Whop (Beast clips)",
    )


def test_order_is_by_value_then_quicker(engine, found, clock):
    found.extend(
        [
            Need("payout", "Whop:USD", ["Whop:USD"], "Payout ready on Whop", value=6.0),  # 30 s
            Need("views", "tiktok", ["v1"], "Enter TikTok views for 1 post"),
            submit(["a", "b"], 6.0),  # 30 s, as the payout: the table's order puts it first
            Need("drafts", "ch", ["d1"], "Approve 1 draft (Gaming)", value=6.0),  # 20 s: quicker
            Need("views", "instagram", ["v2"], "Enter Instagram views for 1 post"),
            Need("needs_you", "all", ["x"], "1 post needs you", value=10.0, currency="AUD"),
        ]
    )
    items = day.refresh(engine)
    assert keys(items) == [
        ("needs_you", "all"),  # A$10, as if US$ without a rate
        ("drafts", "ch"),
        ("submit", "c1"),
        ("payout", "Whop:USD"),
        ("views", "instagram"),
        ("views", "tiktok"),
    ]
    assert items[0].as_if_usd is True and items[1].as_if_usd is False

    with_rates(engine, {"AUD": 0.5})  # A$10 is US$5 now
    items = day.refresh(engine)
    assert keys(items)[:4] == [
        ("drafts", "ch"),
        ("submit", "c1"),
        ("payout", "Whop:USD"),
        ("needs_you", "all"),
    ]
    assert items[3].as_if_usd is False and items[3].value == 10.0  # shown in A$ still

    day.tick(engine, items[0].id)
    clock.advance(minutes=1)
    day.tick(engine, items[1].id)
    assert keys(day.refresh(engine))[-2:] == [("submit", "c1"), ("drafts", "ch")]  # done, newest first


def test_due_is_never_optional(engine, found):
    found.extend(
        [
            Need("login", "a1", ["a1"], "Log in again: @x on TikTok", due=True),  # 60 s, worth nothing
            Need(
                "needs_you", "all", [f"n{i}" for i in range(5)], "5 posts need you", value=3.0, due=True
            ),  # 300
            Need(
                "drafts", "ch", [f"d{i}" for i in range(10)], "Approve 10 drafts (Gaming)", value=4.0
            ),  # 200
            Need("exported", "all", [f"e{i}" for i in range(5)], "Put up 5 exported posts", value=1.0),  # 300
            Need("insights", "a2", ["a2"], "Give Instagram the insights permission: @y", value=0.5),  # 60
            Need("payout", "Whop:USD", ["Whop:USD"], "Payout ready on Whop", value=2.0),  # 30
            submit([f"s{i}" for i in range(10)], 0.2),  # 150
            Need(
                "views", "tiktok", [f"v{i}" for i in range(10)], "Enter TikTok views for 10 posts", value=0.1
            ),  # 100
        ]
    )
    items = day.refresh(engine)
    assert sum(i.estimate_s for i in items) == 1200
    assert {(i.kind, i.ref) for i in items if i.optional} == {
        ("views", "tiktok"),
        ("submit", "c1"),
        ("insights", "a2"),
    }
    assert day.left_s(items) == 890


def test_new_day_starts_at_sydney_midnight_with_carry_over(engine, found, clock):
    """Review Focus 1: the list belongs to Sydney's day, a new one starts once with yesterday's unfinished
    items carried over, yesterday's rows stay as they were and summer time ending makes no second day."""
    found.extend([submit(["p1"]), Need("exported", "all", ["p3"], "Put up 1 exported post")])
    clock.set(at(23, 59, day=3))
    (left, ticked) = day.refresh(engine)
    day.tick(engine, ticked.id)
    yesterday = today_store.items_of(engine.db(), TODAY)

    clock.set(at(0, 1, day=4))
    items = day.refresh(engine)
    assert {i.day for i in items} == {"2026-11-04"}
    assert {(i.kind, i.carried_from) for i in items} == {("submit", TODAY), ("exported", None)}
    assert today_store.items_of(engine.db(), TODAY) == yesterday
    assert day.today_of(engine).isoformat() == "2026-11-04"

    clock.set(datetime(2027, 4, 4, 2, 30, tzinfo=SYDNEY))  # 2:30 am summer time, the night it ends
    first = day.refresh(engine)
    clock.set(datetime(2027, 4, 4, 2, 30, fold=1, tzinfo=SYDNEY))  # the same clock time an hour later
    assert day.refresh(engine) == first
    assert {i.day for i in first} == {"2027-04-04"}
    assert {(i.kind, i.carried_from) for i in first} == {("submit", "2026-11-04"), ("exported", "2026-11-04")}
    days = today_store.last_days(engine.db(), "2027-04-05", 10)
    assert [d.day for d in days] == ["2027-04-04", "2026-11-04", TODAY]


def timed(engine, clock, need: Need, seconds: float) -> None:
    """``need`` on the list, started, and ticked ``seconds`` later."""
    (item,) = [i for i in day.refresh(engine) if (i.kind, i.ref) == (need.kind, need.ref)]
    day.start(engine, item.id)
    clock.advance(seconds=seconds)
    day.tick(engine, item.id)


def test_timing_is_per_unit_and_calibrates(engine, found, clock):
    six = [f"p{i}" for i in range(6)]
    for n, seconds in enumerate((90, 120, 150)):
        found[:] = [submit(six, ref=f"c{n}")]
        timed(engine, clock, found[0], seconds)
    assert day.unit_seconds(engine, "submit") == 20.0  # per post: 15, 20 and 25 s
    found[:] = [submit(["a", "b", "c", "d"], ref="c9")]
    assert [i.estimate_s for i in day.refresh(engine) if i.ref == "c9"] == [80]
    assert day.unit_seconds(engine, "drafts") == day.DEFAULTS["drafts"]


def test_long_pauses_never_calibrate(engine, found, clock):
    """Review Focus 4: a timer left over lunch, a tick with no start, two clicks on Start."""
    found.append(submit(["p1"], ref="lunch"))
    timed(engine, clock, found[0], 31 * 60)
    assert today_store.timings_of(engine.db(), "submit") == []

    found[:] = [submit(["p1"], ref="no-start")]
    (item,) = [i for i in day.refresh(engine) if i.ref == "no-start"]
    day.tick(engine, item.id)
    assert today_store.timings_of(engine.db(), "submit") == []

    found[:] = [submit(["p1", "p2"], ref="twice")]
    (item,) = [i for i in day.refresh(engine) if i.ref == "twice"]
    began = day.start(engine, item.id)
    clock.advance(seconds=10)
    found[:] = [submit(["p1", "p2", "p3"], ref="twice")]
    day.refresh(engine)
    again = day.start(engine, item.id)
    assert (again.started_at, again.units_at_start) == (began.started_at, 2)
    clock.advance(seconds=10)
    day.tick(engine, item.id)
    found[:] = [submit(["p1"], ref="quick")]
    timed(engine, clock, found[0], 0)
    assert [(t.seconds, t.units) for t in today_store.timings_of(engine.db(), "submit")] == [
        (0.0, 1),
        (20.0, 2),
    ]
    assert day.unit_seconds(engine, "submit") == day.DEFAULTS["submit"]  # two timings: too few to go on

    found[:] = [submit(["p1"], ref="quick again")]
    timed(engine, clock, found[0], 0)
    assert day.unit_seconds(engine, "submit") == 1.0  # never under a second, whatever the median


def test_left_and_used(engine, found, clock):
    found.extend(
        [
            submit(["p1", "p2"], 5.0),  # 30 s
            Need("exported", "all", ["p3"], "Put up 1 exported post", value=1.0),  # 60 s
            Need("payout", "Whop:USD", ["Whop:USD"], "Payout ready on Whop", value=9.0),  # 30 s
        ]
    )
    items = day.refresh(engine)
    assert day.left_s(items) == 120
    today_store.update_item(engine.db(), items[2].id, optional=True)  # the exported post, 60 s
    assert day.left_s(today_store.items_of(engine.db(), TODAY)) == 60

    timed(engine, clock, found[0], 40.5)
    timed(engine, clock, found[1], 20.25)
    items = day.refresh(engine)
    assert day.left_s(items) == 30
    assert day.used_s(engine, TODAY) == 61
    today_store.add_timing(
        engine.db(),
        Timing(
            id="t-next",
            kind="drafts",
            started_at=0,
            finished_at=at(0, 30, day=4).timestamp(),
            seconds=99,
            units=1,
        ),
    )
    assert day.used_s(engine, TODAY) == 61
    assert day.used_s(engine, "2026-11-04") == 99


def test_an_item_done_on_another_page_is_timed_to_when_it_was_done(engine, found, clock):
    """Review M9: the list catches up when Today is next read, but the item's time is when the change it
    waited on came, not that read."""
    found.append(submit(["p1", "p2"]))
    (item,) = day.refresh(engine)
    day.start(engine, item.id)
    clock.advance(seconds=30)
    found.clear()
    engine.events.publish("submission", {"campaign_id": "c1"})  # the two submitted on the campaign's page
    clock.advance(seconds=570)
    (done,) = day.refresh(engine)
    assert (done.done_how, done.done_at) == ("app", at(9, 0).timestamp() + 30)
    (timing,) = today_store.timings_of(engine.db(), "submit")
    assert timing.seconds == 30.0


def test_only_a_change_the_item_waits_on_stops_its_timer(engine, found, clock):
    found.append(submit(["p1"]))
    (item,) = day.refresh(engine)
    engine.events.publish("submission", {"campaign_id": "c1"})  # before the start: not its finish
    day.start(engine, item.id)
    clock.advance(seconds=5)
    engine.events.publish("account", {"id": "a1"})  # a submission doesn't wait on an account
    clock.advance(seconds=35)
    found.clear()
    engine.events.publish("submission", {"campaign_id": "c1"})
    clock.advance(seconds=600)
    day.refresh(engine)
    (timing,) = today_store.timings_of(engine.db(), "submit")
    assert timing.seconds == 40.0


def test_catch_up_closes_an_item_at_once_only_while_a_timer_runs(engine, found, clock, todo_events):
    found.append(submit(["p1"]))
    (item,) = day.refresh(engine)
    engine.events.publish("submission", {"campaign_id": "c1"})
    assert day.catch_up(engine) is False  # no timer running: the change is let go, nothing read
    assert not engine.changes.pending()
    day.start(engine, item.id)
    assert day.catch_up(engine) is False  # nothing changed since
    clock.advance(seconds=25)
    found.clear()
    engine.events.publish("submission", {"campaign_id": "c1"})
    seen = len(todo_events)
    assert day.catch_up(engine) is True
    (done,) = today_store.items_of(engine.db(), item.day)
    assert done.done and done.done_how == "app" and len(todo_events) == seen + 1
