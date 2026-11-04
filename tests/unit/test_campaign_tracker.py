"""Submissions and view counts (SPEC 10.10): each posted post of a campaign's clip gets one submission,
however it got posted; the submission moves only along the ways a marketplace can answer; and a post's
views are asked for by hand at 2 days, 7 days and the end date, unless insights read them."""

import math
from datetime import date

import pytest

from clipforge import repo
from clipforge.campaigns import store as campaigns
from clipforge.campaigns import tracker
from clipforge.errors import ClipForgeError
from clipforge.post import queue, store
from tests.fakes import Clock, FakePublisher, fake_engine
from tests.posting import add_account, add_campaign, add_clip, at, custom, linked_clip, post, posted, run
from tests.server import api_client

NOW = at(7, 10)  # Tue 3 Nov 2026


@pytest.fixture
def clock():
    return Clock(NOW)


@pytest.fixture
def engine(clock):
    publishers = {
        "tiktok:browser": FakePublisher(),
        "instagram:api": FakePublisher(platform="instagram", method="api"),
    }
    eng = fake_engine(now=clock, publishers=publishers)
    yield eng
    eng.close()


def drain(sub, topic: str) -> list[dict]:
    found = []
    while (event := sub.get(timeout=0)) is not None:
        if event.topic == topic:
            found.append(event.data)
    return found


def test_one_submission_per_post(engine):
    campaign = add_campaign(engine)
    sub = engine.events.subscribe()
    waiting = custom(engine, linked_clip(engine, campaign, 2), add_account(engine), at(19))
    queue.post_event(engine, waiting)  # scheduled: nothing to submit yet
    done = posted(engine, linked_clip(engine, campaign), add_account(engine))
    (made,) = campaigns.list_submissions(engine.db())
    assert (made.post_id, made.campaign_id, made.status) == (done.id, campaign.id, "to_submit")
    assert (made.submitted_at, made.decided_at, made.reason, made.paid_amount, made.paid_on) == (None,) * 5
    queue.post_event(engine, done)  # a retry's word on a post already posted
    assert tracker.on_post(engine, done) is None
    assert [s.id for s in campaigns.list_submissions(engine.db())] == [made.id]
    said = {"id": made.id, "post_id": done.id, "campaign_id": campaign.id, "status": "to_submit"}
    assert drain(sub, "submission") == [said]


def test_marked_posted_by_hand_gets_a_submission(engine):
    campaign = add_campaign(engine)
    planned = custom(engine, linked_clip(engine, campaign), add_account(engine), at(7, 10))
    store.update_post(engine.db(), planned.id, status="needs_you", error="Log in to TikTok again.")
    assert campaigns.list_submissions(engine.db()) == []
    done = api_client(engine).patch(f"/api/posts/{planned.id}", json={"status": "posted"})
    assert done.status_code == 200
    (made,) = campaigns.list_submissions(engine.db())
    assert (made.post_id, made.campaign_id, made.status) == (planned.id, campaign.id, "to_submit")


def test_an_exported_post_marked_posted_gets_a_submission(engine):
    """Export is the way out a post always has (SPEC 10.5): posted by you and marked as posted, it is a
    campaign's posted post like any other, with its submission and its views asked for."""
    campaign = add_campaign(engine)
    planned = custom(engine, linked_clip(engine, campaign), add_account(engine), at(7, 10))
    engine.posting.export(planned.id)
    assert campaigns.list_submissions(engine.db()) == []
    url = "https://www.tiktok.com/@mine/video/1"
    done = api_client(engine).patch(f"/api/posts/{planned.id}", json={"status": "posted", "url": url})
    assert done.status_code == 200
    assert (done.json()["post"]["status"], done.json()["post"]["url"]) == ("posted", url)
    (made,) = campaigns.list_submissions(engine.db())
    assert (made.post_id, made.campaign_id, made.status) == (planned.id, campaign.id, "to_submit")


def test_unlinked_draft_gets_no_submission(engine):
    campaign = add_campaign(engine)
    posted(engine, linked_clip(engine, campaign), add_account(engine), off_campaign=True)
    posted(engine, add_clip(engine, 2), add_account(engine))  # a project with no campaign
    assert campaigns.list_submissions(engine.db()) == []
    assert tracker.sweep(engine) == 0


def test_sweep_makes_missing_ones(engine):
    campaign = add_campaign(engine)
    first = posted(engine, linked_clip(engine, campaign), add_account(engine))
    second = posted(engine, linked_clip(engine, campaign, 2), add_account(engine, "instagram"))
    custom(engine, linked_clip(engine, campaign, 3), add_account(engine), at(19))  # not posted yet
    posted(engine, linked_clip(engine, campaign, 4), add_account(engine), off_campaign=True)
    engine.db().execute("DELETE FROM submissions")  # made before submissions were kept
    assert tracker.sweep(engine) == 2
    assert {s.post_id for s in campaigns.list_submissions(engine.db())} == {first.id, second.id}
    assert tracker.sweep(engine) == 0


def test_a_post_serves_the_campaign_it_was_planned_for(engine):
    """A submission is for the campaign a post was planned under: none for a post that went before its
    project was linked, and the first campaign still when the project is linked to another before it goes."""
    beast, other = add_campaign(engine), add_campaign(engine, name="Other clips")
    plain = add_clip(engine)
    early = posted(engine, plain, add_account(engine))
    repo.update_project(engine.db(), plain.project_id, campaign_id=beast.id)  # linked after it went
    clip = linked_clip(engine, beast, 2)
    waiting = custom(engine, clip, add_account(engine, "instagram"), engine.now())
    repo.update_project(engine.db(), clip.project_id, campaign_id=other.id)  # linked elsewhere since
    _, done = run(engine, waiting.id)
    assert done.status == "posted", done.error
    assert tracker.sweep(engine) == 0
    (made,) = campaigns.list_submissions(engine.db())
    assert (made.post_id, made.campaign_id) == (done.id, beast.id)
    assert campaigns.submission_of_post(engine.db(), early.id) is None


def test_a_failed_submission_never_stops_the_post(engine, monkeypatch, caplog):
    campaign = add_campaign(engine)

    def broken(engine, post):
        raise RuntimeError("the table is locked")

    monkeypatch.setattr(tracker, "on_post", broken)
    done = posted(engine, linked_clip(engine, campaign), add_account(engine))
    assert post(engine, done.id).status == "posted"
    assert "submission" in caplog.text
    monkeypatch.undo()
    assert tracker.sweep(engine) == 1  # the campaign page's sweep makes it later


def test_submission_moves(engine, clock):
    campaign = add_campaign(engine)
    done = posted(engine, linked_clip(engine, campaign), add_account(engine))
    (made,) = campaigns.list_submissions(engine.db())
    conn = engine.db()
    statuses = {"to_submit", "submitted", "approved", "rejected", "paid"}
    assert set(tracker.MOVES) == statuses and set().union(*tracker.MOVES.values()) <= statuses
    pairs = 0
    for old, news in tracker.MOVES.items():
        for new in news:
            campaigns.update_submission(
                conn, made.id, status=old, paid_amount=None, paid_on=None, reason=None
            )
            moved = tracker.move_submission(
                engine, made.id, new, reason="Too short", paid_amount=12.4 if new == "paid" else None
            )
            assert moved.status == new
            pairs += 1
    assert pairs == 10

    def reset():
        empty = dict(submitted_at=None, decided_at=None, reason=None, paid_amount=None, paid_on=None)
        return campaigns.update_submission(conn, made.id, status="to_submit", **empty)

    reset()
    t0 = clock().timestamp()
    sent = tracker.move_submission(engine, made.id, "submitted")
    assert (sent.submitted_at, sent.decided_at) == (t0, None)
    clock.advance(hours=30)
    turned = tracker.move_submission(engine, made.id, "rejected", reason="  Too short  ")
    assert (turned.reason, turned.decided_at, turned.submitted_at) == ("Too short", clock().timestamp(), t0)
    clock.advance(hours=2)
    won = tracker.move_submission(engine, made.id, "approved", reason="ignored")
    assert (won.reason, won.decided_at) == (None, clock().timestamp())  # an appeal that worked
    decided = won.decided_at
    clock.advance(days=3)
    paid = tracker.move_submission(engine, made.id, "paid", paid_amount=12.4)
    assert (paid.paid_amount, paid.paid_on, paid.decided_at) == (12.4, "2026-11-07", decided)
    back = tracker.move_submission(engine, made.id, "approved")
    assert (back.paid_amount, back.paid_on, back.decided_at) == (None, None, decided)  # paid by mistake
    undone = tracker.move_submission(engine, made.id, "submitted")
    assert (undone.decided_at, undone.submitted_at) == (None, t0)
    again = tracker.move_submission(engine, made.id, "to_submit")
    assert (again.submitted_at, again.status) == (None, "to_submit")

    with pytest.raises(ClipForgeError) as refused:
        tracker.move_submission(engine, made.id, "paid", paid_amount=5.0)
    assert (refused.value.code, refused.value.message) == (
        "wrong_status",
        "A submission can't go from To submit to Paid.",
    )
    tracker.move_submission(engine, made.id, "submitted")
    tracker.move_submission(engine, made.id, "approved")
    for amount in (None, -1.0, math.nan, math.inf):
        with pytest.raises(ClipForgeError) as no_amount:
            tracker.move_submission(engine, made.id, "paid", paid_amount=amount)
        assert (no_amount.value.code, no_amount.value.message) == ("paid_amount", "Enter the amount paid.")
    assert campaigns.get_submission(conn, made.id).status == "approved"
    with pytest.raises(ClipForgeError) as long:
        tracker.move_submission(engine, made.id, "rejected", reason="x" * 301)
    assert long.value.code == "reason"
    assert tracker.move_submission(engine, made.id, "paid", paid_amount=0.0).paid_amount == 0.0
    with pytest.raises(ClipForgeError) as gone:
        tracker.move_submission(engine, "sb-nothing", "submitted")
    assert gone.value.code == "not_found"
    assert done.id == made.post_id


def test_views_due_marks(engine, clock):
    campaign = add_campaign(engine, rate_per_1000=2.0, ends_on="2026-11-20")
    done = posted(engine, linked_clip(engine, campaign), add_account(engine))  # TikTok, 3 Nov

    def due(day: int, on=None):
        return tracker.views_due(engine, post(engine, done.id), on or campaign, date(2026, 11, day))

    assert due(3) is None and due(4) is None
    assert due(5) == "2 days"
    clock.set(at(21, day=5))
    tracker.add_views(engine, done.id, 1000)
    assert due(5) is None and due(9) is None
    assert due(10) == "7 days" and due(12) == "7 days"
    clock.set(at(9, day=12))
    tracker.add_views(engine, done.id, 5000)
    assert due(19) is None
    assert due(20) == "end date" and due(25) == "end date"
    clock.set(at(23, day=20))
    tracker.add_views(engine, done.id, 6000)
    assert due(25) is None

    short = campaign.model_copy(update={"ends_on": "2026-11-06"})
    assert due(5, short) is None and due(6, short) is None  # a count since each mark is in
    clock.set(at(8, day=4))
    early = posted(engine, linked_clip(engine, campaign, 2), add_account(engine))  # 4 Nov
    assert tracker.views_due(engine, early, short, date(2026, 11, 5)) is None
    assert tracker.views_due(engine, early, short, date(2026, 11, 6)) == "end date"
    assert tracker.views_due(engine, early, short, date(2026, 11, 11)) == "end date"  # no 7-day count after

    reel = posted(engine, linked_clip(engine, campaign, 3), add_account(engine, "instagram"))
    assert tracker.views_due(engine, reel, campaign, date(2026, 11, 6)) is None  # insights read it
    store.update_post(engine.db(), reel.id, state={"insights": "no_permission"})
    assert tracker.views_due(engine, post(engine, reel.id), campaign, date(2026, 11, 6)) == "2 days"

    sub = campaigns.submission_of_post(engine.db(), early.id)
    for status in ("rejected", "paid"):
        campaigns.update_submission(engine.db(), sub.id, status=status)
        assert tracker.views_due(engine, early, short, date(2026, 11, 6)) is None


def test_add_views_checks(engine, clock):
    campaign = add_campaign(engine)
    done = posted(engine, linked_clip(engine, campaign), add_account(engine))
    sub = engine.events.subscribe()
    count = tracker.add_views(engine, done.id, 1234)
    assert (count.post_id, count.views, count.source, count.captured_at) == (
        done.id,
        1234,
        "hand",
        clock().timestamp(),
    )
    assert [d["post_id"] for d in drain(sub, "submission")] == [done.id]
    for bad in (-1, True, 10**10 + 1):
        with pytest.raises(ClipForgeError) as refused:
            tracker.add_views(engine, done.id, bad)
        assert refused.value.code == "views"
    waiting = custom(engine, linked_clip(engine, campaign, 2), add_account(engine), at(19))
    with pytest.raises(ClipForgeError) as early:
        tracker.add_views(engine, waiting.id, 10)
    assert (early.value.code, early.value.message) == ("wrong_status", "Only a posted post has views.")
    with pytest.raises(ClipForgeError) as gone:
        tracker.add_views(engine, "po-nothing", 10)
    assert gone.value.code == "not_found"
    assert [m.views for m in campaigns.metrics_of(engine.db(), done.id)] == [1234]
