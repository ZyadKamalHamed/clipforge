"""Instagram insights for campaign posts (SPEC 10.10), against the look-alike in tests/fakes_graph.py: once a
day per post through the account's own Keychain token, while the campaign runs and for 30 days at most,
one call a second; a missing permission sends the post to hand entry, and a limit stops the round."""

import logging
from datetime import date, timedelta

import httpx
import pytest

from clipforge.campaigns import insights, tracker
from clipforge.campaigns import store as campaigns
from clipforge.post import keychain, store
from clipforge.server import app as server_app
from tests.fakes import Clock, FakePublisher, fake_engine
from tests.fakes_graph import IG_TOKEN, PAGE_TOKEN, FakeGraph, error
from tests.posting import SYD, add_account, add_campaign, at, linked_clip, post, posted

NOW = at(7, 10)  # Tue 3 Nov 2026


@pytest.fixture
def clock():
    return Clock(NOW)


@pytest.fixture
def graph(clock):
    return FakeGraph(now=clock)


@pytest.fixture
def engine(clock, graph):
    publishers = {
        "tiktok:browser": FakePublisher(),
        "instagram:api": FakePublisher(platform="instagram", method="api"),
    }
    eng = fake_engine(now=clock, publishers=publishers)
    eng.graph_transport = graph.transport()
    yield eng
    eng.close()


def reel(engine, campaign, rank: int = 1, *, mode: str = "facebook", token: str | None = PAGE_TOKEN, **state):
    """A clip of ``campaign`` posted now to a linked Instagram account, its token in the Keychain."""
    account = add_account(engine, "instagram")
    ref = keychain.ref_for(account.id)
    if token is not None:
        keychain.put_token(ref, token)
    account = store.update_account(engine.db(), account.id, ig_mode=mode, token_ref=ref, ig_user_id="ig1")
    return posted(engine, linked_clip(engine, campaign, rank), account, **state)


def read(engine) -> int:
    return insights.read_due(engine, pause=lambda s: None)


def media_ids(graph) -> list[str]:
    return [r.url.path.split("/")[2] for r in graph.of("insights")]


def test_insights_read_once_a_day(engine, clock, graph, caplog):
    caplog.set_level(logging.DEBUG)
    beast = add_campaign(engine, rate_per_1000=2.0)
    first = reel(engine, beast)
    second = reel(engine, beast, 2, mode="instagram", token=IG_TOKEN)
    newer = {
        "data": [
            {"name": "views", "period": "lifetime", "total_value": {"value": 4321}},
            {"name": "likes", "values": [{"value": 12}]},
            {"name": "saved", "values": [{"value": "lots"}]},  # not a count: left out
            {"name": "reach", "values": [{"value": 99}]},  # not asked for: left out
        ]
    }
    graph.script["insights"].append(httpx.Response(200, json=newer))
    waits: list[float] = []
    assert insights.read_due(engine, pause=waits.append) == 2
    assert waits == [1.0]  # one call a second
    calls = graph.of("insights")
    version = engine.settings.posting.graph_version
    assert [(c.url.host, c.url.path) for c in calls] == [
        ("graph.facebook.com", f"/{version}/{first.external_id}/insights"),
        ("graph.instagram.com", f"/{version}/{second.external_id}/insights"),
    ]
    assert [c.url.params["metric"] for c in calls] == ["views,likes,comments,shares,saved"] * 2
    assert [c.headers["Authorization"] for c in calls] == [f"OAuth {PAGE_TOKEN}", f"Bearer {IG_TOKEN}"]
    assert all("access_token" not in str(c.url) for c in calls)
    (a,) = campaigns.metrics_of(engine.db(), first.id)
    assert (a.source, a.views, a.likes, a.comments, a.shares, a.saves, a.captured_at) == (
        "insights",
        4321,
        12,
        None,
        None,
        None,
        NOW.timestamp(),
    )
    (b,) = campaigns.metrics_of(engine.db(), second.id)
    assert (b.views, b.likes, b.comments, b.shares, b.saves) == (1200, 80, 5, 3, 9)

    clock.advance(hours=19, minutes=59)
    assert read(engine) == 0 and len(graph.of("insights")) == 2  # read in the last 20 hours
    clock.advance(minutes=1)
    assert read(engine) == 2 and len(graph.of("insights")) == 4
    assert [m.views for m in campaigns.metrics_of(engine.db(), first.id)] == [4321, 1200]
    assert PAGE_TOKEN not in caplog.text and IG_TOKEN not in caplog.text


def test_insights_stop_after_30_days_or_the_end(engine, clock, graph):
    reel(engine, add_campaign(engine))
    clock.advance(days=29, hours=23)
    assert read(engine) == 1
    clock.advance(hours=21)
    assert read(engine) == 0  # 30 days old
    clock.advance(hours=4)  # 7:10 am, inside the posting windows for the posts below

    today = clock().astimezone(SYD).date()
    yesterday = (today - timedelta(days=1)).isoformat()
    reel(engine, add_campaign(engine, status="ended"), 2)
    reel(engine, add_campaign(engine, ends_on=yesterday), 3)
    reel(engine, add_campaign(engine, budget_left=0.0), 4)
    reel(engine, add_campaign(engine), 5, off_campaign=True)  # no submission
    reel(engine, add_campaign(engine), 6, token=None)  # nothing in the Keychain
    for n, status in ((7, "rejected"), (8, "paid")):
        done = reel(engine, add_campaign(engine), n)
        sub = campaigns.submission_of_post(engine.db(), done.id)
        campaigns.update_submission(engine.db(), sub.id, status=status)
    running = reel(engine, add_campaign(engine, ends_on=today.isoformat()), 9)  # its last day
    posted(engine, linked_clip(engine, add_campaign(engine), 10), add_account(engine))  # TikTok
    for n, fields in ((11, {"external_id": None}), (12, {"external_id": "../me"})):
        store.update_post(engine.db(), reel(engine, add_campaign(engine), n).id, **fields)
    kept = reel(engine, add_campaign(engine), 13)
    store.update_post(engine.db(), kept.id, external_id=None, state={"media_id": "m77"})
    graph.requests.clear()
    assert read(engine) == 2
    assert media_ids(graph) == [running.external_id, "m77"]


def test_insights_permission_missing_falls_back_to_hand(engine, clock, graph):
    beast = add_campaign(engine)
    first, second = reel(engine, beast), reel(engine, beast, 2)
    graph.script["insights"] += [
        error(10, message="Application does not have permission for this action"),
        error(200, message="Permissions error"),
    ]
    assert read(engine) == 0
    for done in (first, second):
        now = post(engine, done.id)
        assert now.state["insights"] == "no_permission"
        assert tracker.views_due(engine, now, beast, date(2026, 11, 5)) == "2 days"  # by hand instead
        assert store.get_account(engine.db(), now.account_id).status == "connected"  # posting is untouched
    clock.advance(days=1)
    assert read(engine) == 0 and len(graph.of("insights")) == 2  # never asked again


def test_insights_errors_skip_or_stop(engine, clock, graph, caplog):
    beast = add_campaign(engine)
    a, b, c = reel(engine, beast), reel(engine, beast, 2), reel(engine, beast, 3)
    graph.script["insights"].append(error(4, message="Application request limit reached"))
    assert read(engine) == 0 and len(graph.of("insights")) == 1  # the limit stops the round
    graph.script["insights"] += [
        error(190, message="Error validating access token"),
        error(100, message="Unsupported get request"),
    ]
    assert read(engine) == 1  # a and b are skipped this round, c is read
    assert media_ids(graph)[1:] == [a.external_id, b.external_id, c.external_id]
    assert all("insights" not in post(engine, p.id).state for p in (a, b))
    assert store.get_account(engine.db(), a.account_id).status == "connected"
    graph.script["insights"].append(httpx.ConnectError("no network"))
    assert read(engine) == 0 and len(graph.of("insights")) == 5  # no answer stops the round too
    assert "Unsupported get request" in caplog.text and PAGE_TOKEN not in caplog.text


def test_posting_timer_reads_insights_hourly(monkeypatch, caplog):
    ticks, reads = [], []
    monkeypatch.setattr(server_app.queue, "tick", ticks.append)
    monkeypatch.setattr(server_app.insights, "read_due", reads.append)
    engine = object()
    last = server_app._post_round(engine, None, 100.0)
    assert (last, len(ticks), len(reads)) == (100.0, 1, 1)
    last = server_app._post_round(engine, last, 100.0 + 3599)
    assert (last, len(ticks), len(reads)) == (100.0, 2, 1)
    last = server_app._post_round(engine, last, 100.0 + 3600)
    assert (last, len(ticks), len(reads)) == (3700.0, 3, 2)

    def broken(engine):
        raise RuntimeError("no network")

    monkeypatch.setattr(server_app.insights, "read_due", broken)
    monkeypatch.setattr(server_app.queue, "tick", broken)
    assert server_app._post_round(engine, last, 7300.0) == 7300.0  # a failed read waits the hour too
    assert "posting check failed" in caplog.text and "insights" in caplog.text
