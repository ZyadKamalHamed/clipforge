import threading

import pytest

from clipforge.server import app as server_app
from clipforge.watch import schedule
from tests.fakes import fake_engine
from tests.server import api_client


@pytest.fixture
def engine():
    eng = fake_engine()
    yield eng
    eng.close()


def test_watching_timer_ticks(engine, monkeypatch):
    ticks: list[object] = []
    twice = threading.Event()

    def tick(eng):
        ticks.append(eng)
        if len(ticks) >= 2:
            twice.set()
        return []

    monkeypatch.setattr(server_app, "WATCH_TICK_S", 0.01)
    monkeypatch.setattr(schedule, "tick", tick)
    with api_client(engine):
        assert twice.wait(5)
    assert ticks[0] is engine


def test_a_failing_tick_keeps_the_timer_going(engine, monkeypatch):
    calls: list[int] = []
    again = threading.Event()

    def tick(eng):
        calls.append(1)
        if len(calls) >= 2:
            again.set()
        raise RuntimeError("boom")

    monkeypatch.setattr(server_app, "WATCH_TICK_S", 0.01)
    monkeypatch.setattr(schedule, "tick", tick)
    with api_client(engine):
        assert again.wait(5)


def test_today_timer_runs_the_morning(engine, monkeypatch):
    from clipforge.today import morning

    ticks: list[object] = []
    twice = threading.Event()

    def tick(eng):
        ticks.append(eng)
        if len(ticks) >= 2:
            twice.set()
        return False

    monkeypatch.setattr(server_app, "TODAY_TICK_S", 0.01)
    monkeypatch.setattr(morning, "tick", tick)
    with api_client(engine):
        assert twice.wait(5)
    assert ticks[0] is engine


def test_brief_timer_reads(engine, monkeypatch):
    from clipforge.briefs import run

    ticks: list[object] = []
    twice = threading.Event()

    def tick(eng):
        ticks.append(eng)
        if len(ticks) >= 2:
            twice.set()
        return 0

    monkeypatch.setattr(server_app, "BRIEF_TICK_S", 0.01)
    monkeypatch.setattr(run, "tick", tick)
    with api_client(engine):
        assert twice.wait(5)
    assert ticks[0] is engine


def test_trending_timer_reads(engine, monkeypatch):
    from clipforge.trends import feed, instagram

    assert server_app.TREND_TICK_S == 300.0
    ticks: list[object] = []
    pings: list[object] = []
    twice = threading.Event()

    def tick(eng):
        ticks.append(eng)
        raise RuntimeError("Instagram's read failed")  # the weekly ping still has its look

    def ping(eng):
        pings.append(eng)
        if len(pings) >= 2:
            twice.set()
        return False

    monkeypatch.setattr(server_app, "TREND_TICK_S", 0.01)
    monkeypatch.setattr(instagram, "tick", tick)
    monkeypatch.setattr(feed, "tick", ping)
    with api_client(engine):
        assert twice.wait(5)
    assert ticks[0] is engine and pings[0] is engine


def test_today_catches_up_soon_after_a_change(engine, monkeypatch):
    from clipforge.briefs import run
    from clipforge.today import day

    caught = threading.Event()
    monkeypatch.setattr(run, "tick", lambda eng: 0)  # no morning read, which is a change of its own
    monkeypatch.setattr(server_app, "CATCH_UP_S", 0.01)
    monkeypatch.setattr(day, "catch_up", lambda eng: caught.set() or True)
    with api_client(engine):
        assert not caught.wait(0.2)  # nothing changed, so nothing to catch up
        engine.events.publish("post", {"id": "p1", "status": "posted"})
        assert caught.wait(5)
