"""The Queue across a restart (SPEC 10.4 and 17): posts, their times and their jobs live in the database, so
a new server on the same home carries on with them as they were, one job each and nothing posted twice. A
browser post the last run left uploading may or may not be on the site, so it goes to the person instead
of going again. The publishers here post nothing."""

from __future__ import annotations

import json
import time

from clipforge.post import store
from tests.fakes import FakePublisher, fake_engine
from tests.posting import add_account, add_clip, claim, confirm
from tests.server import LiveServer

STOPPED = (
    "ClipForge stopped while this post was uploading. Check TikTok Studio, then press Retry or Mark as"
    " posted."
)


def publishers():
    return {
        "tiktok:browser": FakePublisher(platform="tiktok", method="browser"),
        "instagram:api": FakePublisher(platform="instagram", method="api"),
    }


def queued(engine) -> dict[str, list[float]]:
    """Each post's queued jobs, by when they may start. The test clips' projects carry on when an engine
    opens, and their jobs aren't the Queue's."""
    rows = (
        engine.db()
        .execute("SELECT ref_id, not_before FROM jobs WHERE status = 'queued' AND kind LIKE 'post.%'")
        .fetchall()
    )
    found: dict[str, list[float]] = {}
    for row in rows:
        found.setdefault(row["ref_id"], []).append(row["not_before"])
    return found


def listed(live: LiveServer) -> dict:
    status, _, body = live.raw("GET", "/api/posts")
    assert status == 200
    return json.loads(body)


def until(check, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.02)


def test_queue_survives_a_server_restart():
    fakes = publishers()
    engine = fake_engine(publishers=fakes)
    tt, ig = add_account(engine, "tiktok", "tt_one"), add_account(engine, "instagram", "ig_one")
    # The post that goes now first, so the smart times are planned around it and never move it.
    _, (now,) = confirm(engine, [add_clip(engine, 4)], [tt], timing="now")
    _, later = confirm(engine, [add_clip(engine, rank) for rank in (1, 2, 3)], [tt, ig], timing="smart")
    with LiveServer(engine):
        until(lambda: store.get_post(engine.db(), now.id).status == "posted")
    before = {p.id: (p.status, p.scheduled_at) for p in store.list_posts(engine.db())}
    jobs_before = queued(engine)
    engine.close()

    engine = fake_engine(publishers=fakes)
    try:
        with LiveServer(engine) as live:
            body = listed(live)
            assert {p["id"]: (p["status"], p["scheduled_at"]) for p in body["posts"]} == before
            assert queued(engine) == jobs_before
            assert sorted(queued(engine)) == sorted(p.id for p in later)
            for p in later:
                assert queued(engine)[p.id] == [p.scheduled_at]  # one job each, at its time
            assert now.id not in queued(engine)
        assert len(fakes["tiktok:browser"].published) == 1
        assert fakes["instagram:api"].published == []
    finally:
        engine.close()


def test_restart_during_a_browser_upload_shows_needs_you():
    fakes = publishers()
    engine = fake_engine(publishers=fakes)
    tt = add_account(engine, "tiktok", "tt_one")
    _, (post,) = confirm(engine, [add_clip(engine)], [tt], timing="now")
    claim(engine, post.id)  # a worker took it, and the Mac went down mid upload
    store.update_post(engine.db(), post.id, status="uploading", attempts=1)
    engine.close()

    engine = fake_engine(publishers=fakes)
    try:
        with LiveServer(engine) as live:
            body = listed(live)
            (shown,) = body["posts"]
            assert (shown["status"], shown["error"]) == ("needs_you", STOPPED)
            assert body["needs_you"] == 1
            assert queued(engine) == {}
        assert fakes["tiktok:browser"].published == []
    finally:
        engine.close()
