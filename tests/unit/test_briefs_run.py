"""The morning read (SPEC 10.11): once a day, after the clipping channels' checks or an hour past the read
time, each enabled source that isn't paste-only is read, one at a time; a source that stops keeps its
result and sentence for the day, and every read says so on the event stream."""

import shutil
import threading
from pathlib import Path

import httpx
import pytest

from clipforge.briefs import run, scout, store
from clipforge.briefs.models import BriefSource
from clipforge.watch import store as watch_store
from tests.fakes import Clock, FakeProvider, fake_engine
from tests.posting import add_account, at
from tests.watching import add_channel, add_check

FIXTURE = Path(__file__).parent.parent / "fixtures" / "campaign_scout.csv"
BLOCKED = "https://blocked.example/campaigns"


@pytest.fixture
def clock():
    return Clock(at(6, 1))


@pytest.fixture
def engine(clock):
    eng = fake_engine(now=clock, provider=FakeProvider({"brief_list": lambda req: {"items": []}}))
    yield eng
    eng.close()


@pytest.fixture
def scout_csv(no_real_scout):
    no_real_scout.parent.mkdir(parents=True)
    shutil.copy(FIXTURE, no_real_scout)
    return no_real_scout


@pytest.fixture
def heard(engine):
    events = []
    engine.events.listen(lambda e: events.append((e.topic, e.data)))
    return events


def running_check(engine):
    return add_check(engine, add_channel(engine, [add_account(engine, "tiktok", "t0")]), finished=False)


def blocked_site(engine) -> list[str]:
    """A general source on a site whose robots.txt turns every reader away: the paths it was asked for."""
    asked: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<a href='/c/1'>One</a>")

    engine.brief_transport = httpx.MockTransport(answer)
    store.add_source(
        engine.db(),
        BriefSource(
            id="bs_blocked",
            name="Blocked",
            marketplace="Blocked",
            list_url=BLOCKED,
            reader="general",
            created_at=1.0,
            updated_at=1.0,
        ),
    )
    return asked


def scout_source(engine):
    return store.get_source(engine.db(), "bs_scout")


def test_tick_reads_after_the_checks(engine, clock, scout_csv):
    check = running_check(engine)
    assert run.tick(engine) == 0  # 6:01, and a clipping check is still running
    assert scout_source(engine).last_run_at is None
    assert run.due(engine, scout_source(engine), clock()) is False  # it waits for the check
    watch_store.update_check(engine.db(), check.id, finished_at=at(6, 5).timestamp(), drafts=2)
    clock.set(at(6, 6))
    assert run.reading_done(engine, "2026-11-03") is False  # due now, and not yet read
    assert run.tick(engine) == 1  # Campaign Scout, once; the paste-only sources never
    read = scout_source(engine)
    assert (read.last_run_at, read.last_result, read.error) == (at(6, 6).timestamp(), "ok", None)
    assert store.briefs(engine.db(), source_id="bs_scout")
    assert run.reading_done(engine, "2026-11-03") is True
    clock.set(at(6, 7))
    assert run.tick(engine) == 0  # read for today


def test_tick_reads_an_hour_after_anyway(engine, clock, scout_csv):
    running_check(engine)
    clock.set(at(6, 59))
    assert run.tick(engine) == 0
    clock.set(at(7, 0))
    assert run.tick(engine) == 1  # an hour past the read time, whatever the checks are doing
    assert scout_source(engine).last_result == "ok"


def test_nothing_before_the_read_time(engine, clock, scout_csv):
    clock.set(at(5, 59))
    assert run.tick(engine) == 0
    assert run.reading_done(engine, "2026-11-03") is True  # nothing is due yet
    clock.set(at(6, 0))
    assert run.due(engine, scout_source(engine), clock()) is True


def test_paste_sources_never_read(engine, clock, scout_csv):
    clock.set(at(12, 0))
    vyro = store.get_source(engine.db(), "bs_vyro")
    assert run.due(engine, vyro, clock()) is False
    with pytest.raises(run.PasteOnly):
        run.read_source(engine, "bs_vyro")
    store.update_source(engine.db(), "bs_scout", enabled=False)
    assert run.tick(engine) == 0  # nor one turned off
    for source in store.sources(engine.db()):
        assert source.last_run_at is None


def test_a_stop_holds_for_the_day(engine, clock, scout_csv):
    asked = blocked_site(engine)
    assert run.tick(engine) == 2
    blocked = store.get_source(engine.db(), "bs_blocked")
    assert blocked.last_result == "blocked"
    assert blocked.error and blocked.error.endswith(".")
    assert asked == ["/robots.txt"]  # turned away before its list
    clock.set(at(13, 0))
    assert run.tick(engine) == 0  # no retries in between
    assert asked == ["/robots.txt"]
    clock.set(at(6, 1, day=4))
    assert run.tick(engine) == 2  # it tries again the next morning


def test_scout_problems_say_changed(engine, clock, no_real_scout):
    read = run.read_source(engine, "bs_scout")
    assert (read.result, read.error, read.new) == ("changed", "missing", 0)
    source = scout_source(engine)
    assert (source.last_result, source.error, source.last_run_at) == (
        "changed",
        "missing",
        clock().timestamp(),
    )


def test_read_publishes_brief(engine, heard, scout_csv):
    read = run.read_source(engine, "bs_scout")
    assert read.result == "ok" and read.error is None
    assert read.new == len(store.briefs(engine.db(), source_id="bs_scout")) > 0
    assert ("brief", {"source_id": "bs_scout", "result": "ok"}) in heard
    again = run.read_source(engine, "bs_scout")
    assert (again.new, again.changed, again.gone) == (0, 0, 0)  # the same file says nothing new


def test_one_read_at_a_time(engine, clock, scout_csv, monkeypatch):
    started, release = threading.Event(), threading.Event()
    real = scout.read_scout

    def slow(path):
        started.set()
        assert release.wait(5)
        return real(path)

    monkeypatch.setattr(scout, "read_scout", slow)
    first = threading.Thread(target=run.read_source, args=(engine, "bs_scout"))
    first.start()
    try:
        assert started.wait(5)
        assert run.reading() == "bs_scout"
        with pytest.raises(run.Busy):
            run.read_source(engine, "bs_scout")
        assert run.tick(engine) == 0  # the next tick carries on
    finally:
        release.set()
        first.join(5)
    assert run.reading() is None
    assert scout_source(engine).last_result == "ok"


def test_check_now_waits_for_the_next_morning_after_a_block(engine, clock):
    asked = blocked_site(engine)
    assert run.read_source(engine, "bs_blocked").result == "blocked"
    source = store.get_source(engine.db(), "bs_blocked")
    assert run.check_now_after(engine, source) == at(6, 0, day=4)  # the next morning's read
    clock.set(at(13, 0))
    with pytest.raises(run.TooSoon) as soon:
        run.check_now(engine, "bs_blocked")
    assert soon.value.message == "This site turned ClipForge away, so it waits for the next morning's read."
    assert asked == ["/robots.txt"]  # no request in between
    clock.set(at(6, 0, day=4))
    assert run.check_now_after(engine, store.get_source(engine.db(), "bs_blocked")) is None
