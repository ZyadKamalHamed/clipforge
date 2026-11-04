"""SPEC 10.9, 14.9 and 16: the drafts API. Drafts come grouped by clipping channel, then by source video,
each with its clip, score, warnings and any slot that has to move; Discard and its Undo; and approving goes
through the Post dialog's plan and Confirm with the draft ids, which must be the drafts' own."""

import time

import pytest

from clipforge import repo
from clipforge.campaigns import store as campaigns
from clipforge.campaigns.models import Campaign, Required
from clipforge.campaigns.rules import hand_key
from clipforge.post import store as post_store
from clipforge.watch import drafts, store
from tests.fakes import Clock, FakePublisher, fake_engine
from tests.posting import add_account, add_clip, at
from tests.server import api_client
from tests.watching import add_channel, add_check, watched

GONE = {"error": "gone", "message": "This draft was already approved or removed.", "field": None}
CHANGED = "These drafts changed. Close this and try again."
POST = {
    "id", "batch_id", "clip_id", "clip_title", "account_id", "account_name", "platform", "method", "caption",
    "hashtags", "options", "scheduled_at", "status", "attempts", "url", "error", "error_code", "details",
    "note", "waiting", "actions", "retry_helps", "on_tiktok", "screenshot", "posted_at", "created_at",
    "updated_at",
}  # fmt: skip
DRAFT = POST | {"score", "reasons", "media", "hook_title", "warnings", "slot_moved", "campaign", "rules"}
VIDEO = {"id", "title", "channel", "thumb", "published_at", "rights_basis"}


@pytest.fixture
def clock():
    return Clock(at(7, 10))


@pytest.fixture
def engine(clock):
    publishers = {
        "tiktok:browser": FakePublisher(),
        "instagram:api": FakePublisher(platform="instagram", method="api"),
    }
    eng = fake_engine(now=clock, publishers=publishers)
    yield eng
    eng.close()


@pytest.fixture
def client(engine):
    return api_client(engine)  # no lifespan, so the runner never starts and jobs only queue


@pytest.fixture
def heard(engine):
    events = []
    engine.events.listen(lambda e: events.append((e.topic, e.data)))
    return events


def pair(engine):
    """A TikTok and an Instagram account, each with a draft of the better of two clips."""
    accounts = [
        add_account(engine, "tiktok", "t0", daily_cap=1),
        add_account(engine, "instagram", "i0", daily_cap=1),
    ]
    channel = add_channel(engine, accounts)
    pid, clips = watched(engine, channel, add_check(engine, channel), [90, 60])
    drafts.settle(engine, pid)
    made = sorted(drafts.live_drafts(engine), key=lambda p: p.platform != "tiktok")
    return made, clips, accounts


def ids(posts) -> list[str]:
    return [p.id for p in posts]


def test_drafts_grouped_by_channel_then_video(client, engine):
    t0 = add_account(engine, "tiktok", "t0", daily_cap=2)
    i0 = add_account(engine, "instagram", "i0", daily_cap=1)
    t1 = add_account(engine, "tiktok", "t1", daily_cap=1)
    podcasts = add_channel(engine, [t0, i0], "Podcasts")
    gaming = add_channel(engine, [t1], "Gaming", check_time="06:30")
    first = add_check(engine, podcasts)
    pa, (ca,) = watched(engine, podcasts, first, [90])
    pb, (cb,) = watched(engine, podcasts, first, [80])
    drafts.settle(engine, pa)
    pc, (cc,) = watched(engine, gaming, add_check(engine, gaming), [70])
    drafts.settle(engine, pc)
    for channel in (podcasts, gaming):
        store.update_channel(engine.db(), channel.id, last_check_on="2026-11-03")

    answer = client.get("/api/drafts")
    assert answer.status_code == 200
    shown = answer.json()
    assert shown["count"] == 4
    assert shown["next_check"] == {"at": at(6, day=4).timestamp(), "name": "Podcasts"}
    assert [g["channel"] for g in shown["groups"]] == [
        {"id": podcasts.id, "name": "Podcasts"},
        {"id": gaming.id, "name": "Gaming"},
    ]
    one, two = shown["groups"]
    assert [v["project"]["id"] for v in one["videos"]] == [pa, pb]  # the best clip's video first
    assert [v["project"]["id"] for v in two["videos"]] == [pc]
    video = one["videos"][0]
    assert set(video["project"]) == VIDEO and video["project"]["title"] == "A talk"
    assert {d["account_id"] for d in video["drafts"]} == {t0.id, i0.id}
    assert [(d["clip_id"], d["account_id"]) for d in one["videos"][1]["drafts"]] == [(cb.id, t0.id)]
    assert [(d["clip_id"], d["account_id"]) for d in two["videos"][0]["drafts"]] == [(cc.id, t1.id)]
    draft = video["drafts"][0]
    assert set(draft) == DRAFT
    assert (draft["status"], draft["score"], draft["slot_moved"]) == ("draft", 90.0, None)
    assert draft["media"]["video"].startswith("/media/") and draft["hook_title"]
    assert isinstance(draft["reasons"], list) and isinstance(draft["warnings"], list)


def test_no_drafts_says_when_the_next_check_runs(client, engine):
    assert client.get("/api/drafts").json() == {"groups": [], "count": 0, "next_check": None}
    channel = add_channel(engine, [add_account(engine)], "Podcasts")
    store.update_channel(engine.db(), channel.id, last_check_on="2026-11-03")
    shown = client.get("/api/drafts").json()
    assert shown["next_check"] == {"at": at(6, day=4).timestamp(), "name": "Podcasts"}


def test_passed_slot_shows_where_it_moves(client, engine, clock):
    made, _, _ = pair(engine)
    clock.advance(days=1)
    shown = client.get("/api/drafts").json()
    cards = [d for v in shown["groups"][0]["videos"] for d in v["drafts"]]
    assert {d["id"] for d in cards} == set(ids(made))
    for card in cards:
        assert card["slot_moved"]["at"] >= clock().timestamp()  # from now on, never the passed one
        assert "has passed, so it moves to" in card["slot_moved"]["sentence"]
        assert all(w["code"] != "slot_moved" for w in card["warnings"])


def test_discard_and_restore(client, engine, heard):
    made, _, _ = pair(engine)
    gone = client.post("/api/drafts/discard", json={"post_ids": [made[0].id]})
    assert gone.status_code == 200
    (post,) = gone.json()["posts"]
    assert set(post) == POST and (post["id"], post["status"]) == (made[0].id, "discarded")
    assert client.get("/api/drafts").json()["count"] == 1
    back = client.post("/api/drafts/restore", json={"post_ids": [made[0].id]})
    assert [p["status"] for p in back.json()["posts"]] == ["draft"]
    assert client.get("/api/drafts").json()["count"] == 2
    assert [d for t, d in heard if t == "drafts"][-2:] == [{"count": 1}, {"count": 2}]

    assert client.post("/api/drafts/discard", json={"post_ids": [made[0].id, "p_gone"]}).json() == GONE
    assert post_store.get_post(engine.db(), made[0].id).status == "draft"  # all of them or none
    assert client.post("/api/drafts/discard", json={"post_ids": []}).status_code == 422
    assert client.post("/api/drafts/restore", json={"post_ids": ["p"] * 101}).status_code == 422


def test_queue_leaves_out_drafts(client, engine):
    made, _, _ = pair(engine)
    assert client.get("/api/posts").json()["posts"] == []
    drafts.discard(engine, [made[0].id])
    assert client.get("/api/posts").json()["posts"] == []
    listed = client.get("/api/posts", params={"status": "draft"}).json()["posts"]
    assert [p["id"] for p in listed] == [made[1].id]


def test_plan_and_confirm_with_draft_ids(client, engine, heard):
    made, _, _ = pair(engine)
    asked = {"draft_ids": ids(made)}
    planned = client.post("/api/posts/plan", json=asked)
    assert planned.status_code == 200, planned.text
    plan = planned.json()
    assert {p["key"] for p in plan["posts"]} == {f"{d.clip_id}:{d.account_id}" for d in made}
    confirmed = client.post("/api/posts/confirm", json={"plan_id": plan["plan_id"], "request": asked})
    assert confirmed.status_code == 201, confirmed.text
    posts = confirmed.json()["posts"]
    assert sorted(p["id"] for p in posts) == sorted(ids(made))
    assert {p["status"] for p in posts} == {"scheduled"}
    assert client.get("/api/drafts").json()["count"] == 0
    assert ("drafts", {"count": 0}) in heard
    assert len(client.get("/api/posts").json()["posts"]) == 2  # in the Queue now


def test_draft_ids_must_match_clips_and_accounts(client, engine):
    made, _, accounts = pair(engine)
    other = add_clip(engine)
    draft = made[0]
    wrong = {"draft_ids": [draft.id], "clip_ids": [other.id], "account_ids": [draft.account_id]}
    answer = client.post("/api/posts/plan", json=wrong)
    assert answer.status_code == 422
    assert answer.json() == {"error": "invalid", "message": CHANGED, "field": "draft_ids"}
    elsewhere = {"draft_ids": [draft.id], "clip_ids": [draft.clip_id], "account_ids": [accounts[1].id]}
    assert client.post("/api/posts/plan", json=elsewhere).json()["message"] == CHANGED
    confirm = {"plan_id": "plan_x", "request": wrong}
    assert client.post("/api/posts/confirm", json=confirm).json()["message"] == CHANGED
    own = {"draft_ids": [draft.id], "clip_ids": [draft.clip_id], "account_ids": [draft.account_id]}
    assert client.post("/api/posts/plan", json=own).status_code == 200
    assert client.post("/api/posts/plan", json={"clip_ids": [], "account_ids": []}).status_code == 422
    assert client.post("/api/posts/plan", json={"draft_ids": ["p"] * 101}).status_code == 422
    assert client.post("/api/posts/plan", json={"draft_ids": ["p_gone"]}).json() == GONE


def test_queue_buttons_on_a_draft_say_why_not(client, engine):
    made, _, _ = pair(engine)
    draft, other = made
    drafts.discard(engine, [other.id])
    for post, words in ((draft, "a draft waiting for review"), (other, "a discarded draft")):
        answer = client.patch(f"/api/posts/{post.id}", json={"status": "cancelled"})
        assert answer.status_code == 409
        assert answer.json()["message"] == f"This post is {words}, so it can't be cancelled."
        assert client.post(f"/api/posts/{post.id}/retry").status_code == 409


def test_titles_in_answers_are_clean(client, engine):
    made, _, _ = pair(engine)
    (project_id,) = {repo.get_clip(engine.db(), d.clip_id).project_id for d in made}
    engine.db().execute(
        "UPDATE projects SET title = ?, channel = ? WHERE id = ?",
        ("A\u0007talk\u202e", "Chan\u0000nel", project_id),
    )
    text = client.get("/api/drafts").text
    for mark in ("\u0007", "\u202e", "\u0000", "\\u0007", "\\u202e", "\\u0000"):
        assert mark not in text


def test_linked_draft_carries_its_campaign_and_rules(client, engine):
    made, _, _ = pair(engine)
    (project_id,) = {repo.get_clip(engine.db(), d.clip_id).project_id for d in made}
    now = time.time()
    saved = campaigns.add_campaign(
        engine.db(),
        Campaign(
            id=repo.new_id("cp"),
            name="Beast clips",
            marketplace="Whop",
            created_at=now,
            updated_at=now,
            required=Required(hashtags=["beast"]),
            hand_rules=["No watermark"],
        ),
    )
    repo.update_project(engine.db(), project_id, campaign_id=saved.id, rights_basis="clipping_programme")

    def cards():
        (group,) = client.get("/api/drafts").json()["groups"]
        return {d["platform"]: d for v in group["videos"] for d in v["drafts"]}

    by = cards()
    assert by["tiktok"]["campaign"] == {"id": saved.id, "name": "Beast clips"}
    assert [r["key"] for r in by["tiktok"]["rules"]] == [
        "hashtag:beast",
        "disclosure",
        hand_key("No watermark"),
    ]
    assert [r["key"] for r in by["instagram"]["rules"]] == ["hashtag:beast", hand_key("No watermark")]
    hand = by["instagram"]["rules"][-1]
    assert hand == {
        "key": hand_key("No watermark"),
        "label": "No watermark",
        "kind": "hand",
        "ok": False,
        "sentence": "Check this yourself, then tick it.",
    }
    assert "rule_unchecked" in {w["code"] for w in by["instagram"]["warnings"]}

    post_store.update_post(engine.db(), made[1].id, state={"off_campaign": True})
    by = cards()
    assert (by["instagram"]["campaign"], by["instagram"]["rules"]) == (None, [])  # an ordinary draft now
    assert by["tiktok"]["campaign"] is not None
