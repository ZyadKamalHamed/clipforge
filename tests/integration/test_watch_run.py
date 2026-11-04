"""SPEC 10.9 from start to finish: at its check time a clipping channel reads its source's feed, clips the new
uploads, renders their best clips and leaves them on Today as drafts, with no clicks and nothing posted.
Approving them through the Post dialog's plan and Confirm schedules them, and each posts once at its slot.
Fakes for YouTube and its feed, speech, the LLM, the render and the sites; a real database and real job
workers."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from clipforge import repo
from clipforge.jobs import pipeline, share
from clipforge.post import store as post_store
from clipforge.watch import check, schedule, store
from clipforge.watch.models import ClipChannel, WatchedSource
from tests.fakes import (
    Clock,
    FakeProvider,
    FakePublisher,
    FakeSpeech,
    FakeYouTube,
    brain_handlers,
    fake_engine,
    lines,
)
from tests.posting import add_account, add_render, jobs, run
from tests.server import api_client

FEED = (Path(__file__).parent.parent / "fixtures" / "feeds" / "channel.xml").read_bytes()
UC = "UCabcdefghijklmnopqrstuv"
START = datetime(2026, 10, 25, 12, tzinfo=UTC)  # after Episode 1, before Episodes 2 and 3 and the Short
MORNING = datetime(2026, 10, 27, 19, tzinfo=UTC)  # 06:00 on Wednesday 28 October in Sydney, the check time
SHORT, LIVE, EP2 = "ddddddddd-4", "ccccccccc3_", "bbbbbbbbbb2"


def rendered(engine, ctx):
    """The render's stand-in: a version that passes QA, made the clip's current one, as clip.render does."""
    with engine.lock:
        render = add_render(engine, repo.get_clip(engine.db(), ctx.job.ref_id))
        engine.update_clip(ctx.job.ref_id, status="rendered")
    return {"render_id": render.id, "version": render.version, "status": "ready"}


@pytest.fixture
def clock():
    return Clock(MORNING)


@pytest.fixture
def tiktok():
    return FakePublisher()


@pytest.fixture
def instagram():
    return FakePublisher(platform="instagram", method="api")


@pytest.fixture
def engine(monkeypatch, clock, tiktok, instagram):
    monkeypatch.setattr(check, "FEED_GAP_S", 0.0)
    monkeypatch.setitem(pipeline.CLIP_FUNCTIONS, "clip.source", lambda engine, ctx: None)
    monkeypatch.setitem(pipeline.CLIP_FUNCTIONS, "clip.analyze", lambda engine, ctx: None)
    monkeypatch.setitem(pipeline.CLIP_FUNCTIONS, "clip.render", rendered)
    youtube = FakeYouTube(seconds=600.0)  # ten minutes: inside the skip lengths
    youtube.videos = {video_id: {} for video_id in (SHORT, LIVE, EP2)}  # each its own info
    eng = fake_engine(
        youtube=youtube,
        speech=FakeSpeech(lines(36)),
        provider=FakeProvider(brain_handlers()),
        now=clock,
        publishers={"tiktok:browser": tiktok, "instagram:api": instagram},
    )
    eng.feed_transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=FEED, headers={"content-type": "text/xml"})
    )
    yield eng
    eng.close()


def podcasts(engine, accounts, name: str = "Podcasts") -> ClipChannel:
    """A clipping channel filling ``accounts`` from one YouTube channel, watched since START."""
    conn = engine.db()
    channel = store.add_channel(
        conn,
        ClipChannel(
            id=repo.new_id("cc"),
            name=name,
            account_ids=[a.id for a in accounts],
            rights_basis="permission",
            created_at=START.timestamp(),
            updated_at=START.timestamp(),
        ),
    )
    store.add_source(
        conn,
        WatchedSource(
            id=repo.new_id("ws"),
            clip_channel_id=channel.id,
            yt_channel_id=UC,
            title="A Podcast",
            url=f"https://www.youtube.com/channel/{UC}/videos",
            start_published_at=START.timestamp(),
            created_at=START.timestamp(),
        ),
    )
    return channel


def post_jobs(engine) -> int:
    return engine.db().execute("SELECT COUNT(*) FROM jobs WHERE kind LIKE 'post.%'").fetchone()[0]


def test_watched_upload_becomes_drafts_with_no_clicks(engine, clock, tiktok, instagram):
    accounts = [add_account(engine, "tiktok", "t0"), add_account(engine, "instagram", "i0")]
    channel = podcasts(engine, accounts)
    (made,) = schedule.tick(engine)  # 06:00, its check time: nobody pressed anything
    assert engine.runner.run_until_idle(timeout_s=30, refs=[made.id])
    items = {i.video_id: i for i in store.items_for(engine.db(), channel.id)}
    assert {v: i.status for v, i in items.items()} == {SHORT: "skipped", LIVE: "clipping", EP2: "clipping"}
    projects = [items[LIVE].project_id, items[EP2].project_id]
    assert engine.wait(timeout_s=120, projects=projects)

    done = store.get_check(engine.db(), made.id)
    assert (done.found, done.skipped, done.clipped) == (3, 1, 2)
    assert done.drafted_at is not None and done.drafts >= 2
    assert {i.status for i in store.items_for(engine.db(), channel.id)} == {"skipped", "drafted"}
    made_drafts = post_store.list_posts(engine.db(), status=["draft"])
    assert len(made_drafts) == done.drafts
    assert {p.account_id for p in made_drafts} == {a.id for a in accounts}
    assert {repo.get_clip(engine.db(), p.clip_id).project_id for p in made_drafts} <= set(projects)
    assert (tiktok.published, instagram.published, post_jobs(engine)) == ([], [], 0)  # nothing went up

    client = api_client(engine)
    today = client.get("/api/drafts").json()
    assert today["count"] == done.drafts
    assert [g["channel"]["name"] for g in today["groups"]] == ["Podcasts"]
    shown = [d["id"] for g in today["groups"] for v in g["videos"] for d in v["drafts"]]
    assert sorted(shown) == sorted(p.id for p in made_drafts)

    asked = {"draft_ids": shown}
    planned = client.post("/api/posts/plan", json=asked)
    assert planned.status_code == 200, planned.text
    assert not planned.json()["blocked"], planned.json()["warnings"]
    confirmed = client.post(
        "/api/posts/confirm", json={"plan_id": planned.json()["plan_id"], "request": asked}
    )
    assert confirmed.status_code == 201, confirmed.text
    posts = sorted(confirmed.json()["posts"], key=lambda p: p["scheduled_at"])
    assert {p["status"] for p in posts} == {"scheduled"} and len(posts) == done.drafts
    assert client.get("/api/drafts").json()["count"] == 0
    assert (tiktok.published, instagram.published) == ([], [])

    for p in posts:
        slot = datetime.fromtimestamp(p["scheduled_at"], UTC)
        clock.set(slot - timedelta(minutes=1))
        assert run(engine, p["id"])[1].status == "scheduled"  # a minute early, it waits
        clock.set(slot)
        assert run(engine, p["id"])[1].status == "posted"
        assert jobs(engine, p["id"]) == []  # and nothing is left to post it again
    went_up = [x.id for x in tiktok.published] + [x.id for x in instagram.published]
    assert sorted(went_up) == sorted(p["id"] for p in posts)
    assert {x.account_id for x in tiktok.published} == {accounts[0].id}
    assert {x.account_id for x in instagram.published} == {accounts[1].id}


def test_drafts_come_with_auto_render_off(engine):
    engine.settings = engine.settings.model_copy(update={"auto_render": "none"})
    channel = podcasts(engine, [add_account(engine, "tiktok", "t0"), add_account(engine, "instagram", "i0")])
    (made,) = schedule.tick(engine)
    assert engine.runner.run_until_idle(timeout_s=30, refs=[made.id])
    projects = [i.project_id for i in store.items_for(engine.db(), channel.id) if i.project_id]
    assert engine.wait(timeout_s=120, projects=projects)
    done = store.get_check(engine.db(), made.id)
    assert done.drafted_at is not None and done.drafts >= 2


def test_two_clipping_channels_fetch_and_hear_each_video_once(engine, monkeypatch):
    monkeypatch.setattr(share, "WAIT_S", 0.1)
    first = podcasts(engine, [add_account(engine, "tiktok", "t0")])
    second = podcasts(engine, [add_account(engine, "tiktok", "t1")], "Highlights")
    made = schedule.tick(engine)  # both check at 06:00, so neither project has a file when the other starts
    assert len(made) == 2
    assert engine.runner.run_until_idle(timeout_s=30, refs=[m.id for m in made])
    by_video: dict[str, list[str]] = {}
    for channel in (first, second):
        for item in store.items_for(engine.db(), channel.id):
            if item.project_id:
                by_video.setdefault(item.video_id, []).append(item.project_id)
    assert sorted(by_video) == sorted([LIVE, EP2]) and all(len(p) == 2 for p in by_video.values())
    projects = [p for pair in by_video.values() for p in pair]
    assert engine.wait(timeout_s=120, projects=projects)
    assert {engine.project(p).status for p in projects} == {"ready"}
    assert len(engine.youtube.called("download_audio")) == 2  # one a video, not one a project
    full_passes = [c for c in engine.asr_engines["parakeet"].called("transcribe") if c[1] is None]
    assert len(full_passes) == 2
    for one, other in by_video.values():
        a, b = engine.paths.project(one), engine.paths.project(other)
        for name in ("audio16k", "transcript_json", "signals_json"):
            assert getattr(a, name).samefile(getattr(b, name))  # hard links: one file, two projects
