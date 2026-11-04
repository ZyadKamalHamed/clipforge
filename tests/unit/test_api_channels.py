"""SPEC 10.9, 14.9 and 16: the clipping channels API. A clipping channel is made, listed, changed and
removed as its card says, with every refusal a sentence; a source is added by its link; Check now says when
it can run again; and the history says what each check found, what it skipped and why."""

import time

import pytest

from clipforge import repo
from clipforge.campaigns import store as campaigns
from clipforge.campaigns.models import Campaign
from clipforge.post import store as post_store
from clipforge.server.views import check_line
from clipforge.watch import drafts, store
from clipforge.watch.models import ChannelListing, WatchCheck, WatchItem
from tests.fakes import Clock, FakePublisher, FakeYouTube, fake_engine
from tests.posting import add_account, at
from tests.server import api_client
from tests.watching import add_channel, add_check, watched

UC = "UCabcdefghijklmnopqrstuv"
LISTING = ChannelListing(
    channel_id=UC,
    title="A Podcast",
    url=f"https://www.youtube.com/channel/{UC}/videos",
    entries=[("bbbbbbbbbb2", "Episode 2"), ("aaaaaaaaaa1", "Episode 1")],
)
NOT_FOUND = {"error": "not_found", "message": "That isn't here.", "field": None}
CHANNEL = {
    "id", "name", "paused", "account_ids", "options", "rights_basis", "check_time", "daily_video_limit",
    "clips_per_day", "on_limit", "sources", "last_check", "last_line", "next_check_at", "check_now_after",
    "drafts", "campaign",
}  # fmt: skip
SOURCE = {"id", "title", "url", "newest_title", "newest_at", "error", "letter"}
CHECK = {
    "id",
    "manual",
    "started_at",
    "finished_at",
    "found",
    "skipped",
    "clipped",
    "drafts",
    "error",
    "line",
}
ITEM = {"id", "video_id", "title", "url", "published_at", "status", "code", "reason", "project_id"}


@pytest.fixture
def clock():
    return Clock(at(7, 10))


@pytest.fixture
def youtube():
    yt = FakeYouTube(channel_id=UC, channel="A Podcast", channel_url=f"https://www.youtube.com/channel/{UC}")
    yt.listing = LISTING
    return yt


@pytest.fixture
def engine(clock, youtube):
    publishers = {
        "tiktok:browser": FakePublisher(),
        "instagram:api": FakePublisher(platform="instagram", method="api"),
    }
    eng = fake_engine(now=clock, publishers=publishers, youtube=youtube)
    yield eng
    eng.close()


@pytest.fixture
def client(engine):
    return api_client(engine)  # no lifespan, so the runner never starts and a check only queues


@pytest.fixture
def heard(engine):
    events = []
    engine.events.listen(lambda e: events.append((e.topic, e.data)))
    return events


def made(client, **body) -> dict:
    answer = client.post("/api/clip-channels", json={"name": "Podcasts", **body})
    assert answer.status_code == 201, answer.text
    return answer.json()["channel"]


def test_create_list_patch_delete_channel(client, engine, heard):
    tiktok, insta = add_account(engine, "tiktok", "t0"), add_account(engine, "instagram", "i0")
    channel = made(
        client,
        name="  Podcasts ",
        account_ids=[tiktok.id, insta.id, tiktok.id],
        options={"project": {"length": "short"}, "hashtags": ["podcast", "#Clips", "#clips"]},
        check_time="07:30",
        daily_video_limit=2,
    )
    assert set(channel) == CHANNEL
    assert channel["name"] == "Podcasts"
    assert channel["account_ids"] == [tiktok.id, insta.id]
    assert channel["options"]["project"]["length"] == "short"
    assert channel["options"]["hashtags"] == ["podcast", "Clips"]
    assert (channel["check_time"], channel["daily_video_limit"], channel["clips_per_day"]) == (
        "07:30",
        2,
        None,
    )
    assert (channel["on_limit"], channel["rights_basis"], channel["paused"]) == ("wait", "not_cleared", False)
    assert channel["sources"] == [] and channel["last_check"] is None and channel["last_line"] is None
    assert channel["next_check_at"] is None  # nothing to watch yet
    assert (channel["check_now_after"], channel["drafts"]) == (None, 0)
    cid = channel["id"]

    listed = client.get("/api/clip-channels").json()["channels"]
    assert [c["id"] for c in listed] == [cid]

    changed = client.patch(
        f"/api/clip-channels/{cid}", json={"paused": True, "clips_per_day": 4, "name": "Talks"}
    )
    assert changed.status_code == 200
    shown = changed.json()["channel"]
    assert (shown["paused"], shown["clips_per_day"], shown["name"], shown["check_time"]) == (
        True,
        4,
        "Talks",
        "07:30",
    )
    merged = client.patch(
        f"/api/clip-channels/{cid}", json={"clips_per_day": None, "options": {"hashtags": []}}
    )
    shown = merged.json()["channel"]
    assert shown["clips_per_day"] is None  # back to what the accounts can post
    assert shown["options"]["hashtags"] == [] and shown["options"]["project"]["length"] == "short"

    assert client.delete(f"/api/clip-channels/{cid}").status_code == 204
    assert client.get("/api/clip-channels").json() == {"channels": []}
    assert client.delete(f"/api/clip-channels/{cid}").json() == NOT_FOUND
    assert client.patch(f"/api/clip-channels/{cid}", json={"paused": False}).status_code == 404
    assert [d for t, d in heard if t == "clip_channel"] == [{"id": cid}] * 4


@pytest.mark.parametrize(
    "body, field, sentence",
    [
        ({"check_time": "25:00"}, "check_time", "Give the check time as hours and minutes, like 06:00."),
        ({"daily_video_limit": 11}, "daily_video_limit", "Clip from 1 to 10 new videos a day."),
        ({"name": "   "}, "name", "Give the clipping channel a name."),
        ({"name": "x" * 61}, "name", "Keep the name to 60 characters."),
        (
            {"clips_per_day": 0},
            "clips_per_day",
            "Draft from 1 to 50 clips a day, or leave it to what the accounts can post.",
        ),
        ({"account_ids": ["a_gone"]}, "account_ids", "One of those accounts isn't in ClipForge any more."),
        ({"options": {"hashtags": ["#a"] * 11}}, "options.hashtags", "Give at most 10 hashtags."),
    ],
)
def test_bad_channel_body_is_a_sentence(client, body, field, sentence):
    answer = client.post("/api/clip-channels", json={"name": "Podcasts"} | body)
    assert answer.status_code == 422
    assert answer.json() == {"error": "invalid", "message": sentence, "field": field}
    assert client.get("/api/clip-channels").json() == {"channels": []}


def test_new_channel_can_start_paused(client, engine):
    """Accounts worth adding's Set it up makes the channel paused until you check it (SPEC 10.11)."""
    channel = made(client, paused=True)
    assert channel["paused"] is True
    assert store.get_channel(engine.db(), channel["id"]).paused is True
    assert made(client, name="Gaming")["paused"] is False


def test_bad_options_and_fields_name_the_field(client):
    answer = client.post("/api/clip-channels", json={"name": "P", "options": {"project": {"nope": 1}}})
    assert (answer.status_code, answer.json()["field"]) == (422, "options.project.nope")
    answer = client.post("/api/clip-channels", json={"name": "P", "colour": "red"})
    assert (answer.status_code, answer.json()["field"]) == (422, "colour")
    cid = made(client)["id"]
    answer = client.patch(f"/api/clip-channels/{cid}", json={"check_time": "6am"})
    assert answer.json()["message"] == "Give the check time as hours and minutes, like 06:00."


def test_add_source_by_link(client, engine, youtube, heard):
    cid = made(client)["id"]
    answer = client.post(
        f"/api/clip-channels/{cid}/sources", json={"link": "https://www.youtube.com/@apodcast"}
    )
    assert answer.status_code == 201, answer.text
    source = answer.json()["source"]
    assert set(source) == SOURCE
    assert source["title"] == "A Podcast" and source["letter"] == "A"
    assert source["url"] == f"https://www.youtube.com/channel/{UC}/videos"
    assert (source["newest_title"], source["error"]) == ("Episode 2", None)
    channel = client.get("/api/clip-channels").json()["channels"][0]
    assert [s["id"] for s in channel["sources"]] == [source["id"]]
    assert channel["next_check_at"] == at(7, 10).timestamp()  # 6:00 has passed, so the check is due now
    assert ("clip_channel", {"id": cid}) in heard

    again = client.post(f"/api/clip-channels/{cid}/sources", json={"link": "@apodcast"})
    assert again.status_code == 409
    assert again.json()["message"] == "This clipping channel already watches A Podcast."
    bad = client.post(f"/api/clip-channels/{cid}/sources", json={"link": "the podcast"})
    assert (bad.status_code, bad.json()["error"], bad.json()["field"]) == (422, "not_a_channel", "link")
    many = client.post(f"/api/clip-channels/{cid}/sources", json={"link": "@apodcast", "backfill": 6})
    assert (many.status_code, many.json()["field"]) == (422, "backfill")
    assert client.post("/api/clip-channels/cc_gone/sources", json={"link": "@apodcast"}).json() == NOT_FOUND

    other = made(client, name="Other")["id"]
    assert client.delete(f"/api/clip-channels/{other}/sources/{source['id']}").status_code == 404
    assert client.delete(f"/api/clip-channels/{cid}/sources/{source['id']}").status_code == 204
    assert client.get("/api/clip-channels").json()["channels"][0]["sources"] == []


def test_check_now_too_soon_is_429(client, engine, clock):
    channel = add_channel(engine, [add_account(engine)])
    add_check(engine, channel)  # finished at 7:10
    clock.advance(minutes=3)
    answer = client.post(f"/api/clip-channels/{channel.id}/check")
    assert answer.status_code == 429
    assert answer.json() == {
        "error": "too_soon",
        "message": "Checked 3 minutes ago. Check again after 7:20 am.",
        "field": None,
    }
    shown = client.get("/api/clip-channels").json()["channels"][0]
    assert shown["check_now_after"] == at(7, 20).timestamp()

    clock.advance(minutes=8)
    started = client.post(f"/api/clip-channels/{channel.id}/check")
    assert started.status_code == 202
    check = started.json()["check"]
    assert set(check) == CHECK
    assert (check["manual"], check["finished_at"], check["line"]) == (True, None, "7:21 am: checking now")
    again = client.post(f"/api/clip-channels/{channel.id}/check")
    assert (again.status_code, again.json()["check"]["id"]) == (202, check["id"])  # the one running
    shown = client.get("/api/clip-channels").json()["channels"][0]
    assert shown["last_check"]["id"] == check["id"] and shown["last_line"] == "7:21 am: checking now"
    assert client.post("/api/clip-channels/cc_gone/check").json() == NOT_FOUND


def test_ended_campaign_shows_on_the_card_and_stops_check_now(client, engine):
    now = time.time()
    saved = campaigns.add_campaign(
        engine.db(),
        Campaign(
            id=repo.new_id("cp"), name="Beast clips", marketplace="Whop", created_at=now, updated_at=now
        ),
    )
    channel = add_channel(engine, [add_account(engine)], campaign_id=saved.id)
    plain = add_channel(engine, [], "Gaming")

    def cards():
        return {c["id"]: c for c in client.get("/api/clip-channels").json()["channels"]}

    shown = cards()
    assert shown[channel.id]["campaign"] == {"id": saved.id, "name": "Beast clips", "ended": None}
    assert shown[plain.id]["campaign"] is None
    campaigns.update_campaign(engine.db(), saved.id, status="ended")
    shown = cards()[channel.id]
    assert shown["campaign"]["ended"] == "You marked Beast clips ended."
    assert shown["next_check_at"] is None
    answer = client.post(f"/api/clip-channels/{channel.id}/check")
    assert answer.status_code == 409
    assert answer.json() == {
        "error": "campaign_ended",
        "message": "You marked Beast clips ended.",
        "field": None,
    }


def item(engine, channel, check_id, status, reason=None, title="A video", **fields) -> WatchItem:
    (source,) = store.list_sources(engine.db(), channel.id)
    now = engine.now().timestamp()
    return store.add_item(
        engine.db(),
        WatchItem(
            id=repo.new_id("wi"),
            source_id=source.id,
            clip_channel_id=channel.id,
            check_id=check_id,
            video_id=repo.new_id("v"),
            title=title,
            status=status,
            reason=reason,
            first_seen_at=now,
            updated_at=now,
            **fields,
        ),
    )


def test_history_lists_items_with_reasons(client, engine):
    channel = add_channel(engine, [add_account(engine)])
    check = add_check(engine, channel)
    pid, _ = watched(engine, channel, check, [80])
    item(engine, channel, check.id, "skipped", "short", title="A Short one")
    item(engine, channel, check.id, "skipped", "too_short", title="A trailer")
    item(engine, channel, None, "waiting", "live")  # no check of its own yet
    store.update_check(engine.db(), check.id, found=3, skipped=2, clipped=1, drafts=0)

    answer = client.get(f"/api/clip-channels/{channel.id}/history")
    assert answer.status_code == 200
    (shown,) = answer.json()["checks"]
    assert set(shown) == CHECK | {"items"}
    assert shown["line"] == "7:10 am: 3 new videos, 2 skipped, no drafts"
    assert all(set(i) == ITEM for i in shown["items"])
    assert [(i["status"], i["code"], i["reason"]) for i in shown["items"]] == [
        ("clipping", None, None),
        ("skipped", "short", "A Short."),
        ("skipped", "too_short", "Under 4 minutes."),
    ]
    assert shown["items"][0]["project_id"] == pid
    video = shown["items"][1]["video_id"]
    assert shown["items"][1]["url"] == f"https://www.youtube.com/watch?v={video}"
    assert client.get("/api/clip-channels/cc_gone/history").json() == NOT_FOUND


def a_check(engine, channel, **fields) -> WatchCheck:
    start = engine.now().timestamp()
    check = WatchCheck(
        id=repo.new_id("wc"), clip_channel_id=channel.id, check_on="2026-11-03", started_at=start
    )
    return store.add_check(engine.db(), check.model_copy(update=fields))


def test_check_line_says_what_the_check_did(engine, clock):
    channel = add_channel(engine, [add_account(engine)])
    clock.set(at(6))
    done = a_check(engine, channel, finished_at=at(6, 1).timestamp(), found=2, skipped=1, clipped=1, drafts=6)
    shorts = [item(engine, channel, done.id, "skipped", "short")]
    assert check_line(engine, done, shorts) == "6:00 am: 2 new videos, 1 skipped as a Short, 6 drafts"
    two = done.model_copy(update={"skipped": 2})
    shorts.append(item(engine, channel, done.id, "skipped", "short"))
    assert check_line(engine, two, shorts) == "6:00 am: 2 new videos, 2 skipped as Shorts, 6 drafts"
    one = done.model_copy(update={"found": 1, "skipped": 0, "drafts": 1})
    assert check_line(engine, one, []) == "6:00 am: 1 new video, 1 draft"
    none = done.model_copy(update={"found": 0, "skipped": 0, "clipped": 0, "drafts": 0})
    assert check_line(engine, none, []) == "6:00 am: no new videos, no drafts"
    clipping = done.model_copy(update={"skipped": 0, "clipped": 2, "drafts": None})
    assert check_line(engine, clipping, []) == "6:00 am: 2 new videos, 2 being clipped"
    stopped = none.model_copy(update={"error": "rate_limited"})
    assert check_line(engine, stopped, []) == "6:00 am: no new videos, no drafts, stopped early"
    clock.set(at(9, day=4))
    assert check_line(engine, one, []) == "Tue 3 Nov, 6:00 am: 1 new video, 1 draft"


def test_rate_limited_check_says_so(client, engine):
    channel = add_channel(engine, [add_account(engine)])
    a_check(engine, channel, finished_at=engine.now().timestamp(), error="rate_limited", drafts=0)
    a_check(engine, channel, finished_at=engine.now().timestamp(), error="failed", drafts=0)
    checks = client.get(f"/api/clip-channels/{channel.id}/history").json()["checks"]
    assert [c["error"] for c in checks] == [
        "ClipForge stopped this check before it finished. The next check tries again.",
        "YouTube asked this Mac to slow down, so this clipping channel waits until tomorrow's check.",
    ]


def test_deleting_a_channel_discards_its_drafts(client, engine, heard):
    accounts = [add_account(engine, "tiktok", "t0", daily_cap=1)]
    channel = add_channel(engine, accounts)
    pid, _ = watched(engine, channel, add_check(engine, channel), [90])
    drafts.settle(engine, pid)
    (draft,) = drafts.live_drafts(engine)
    assert client.get("/api/clip-channels").json()["channels"][0]["drafts"] == 1
    assert client.delete(f"/api/clip-channels/{channel.id}").status_code == 204
    assert post_store.get_post(engine.db(), draft.id).status == "discarded"
    assert repo.get_project(engine.db(), pid) is not None  # its projects stay, as ordinary ones
    assert ("drafts", {"count": 0}) in heard


def test_titles_in_answers_are_clean(client, engine):
    channel = add_channel(engine, [add_account(engine)])
    (source,) = store.list_sources(engine.db(), channel.id)
    store.update_source(engine.db(), source.id, title="A\u0007Pod\u202ecast", newest_title="New\u0000one")
    check = add_check(engine, channel)
    item(engine, channel, check.id, "skipped", "short", title="Bad\u202etitle\u0007")
    text = client.get("/api/clip-channels").text + client.get(f"/api/clip-channels/{channel.id}/history").text
    for mark in ("\u0007", "\u202e", "\u0000", "\\u0007", "\\u202e", "\\u0000"):
        assert mark not in text
    named = client.post("/api/clip-channels", json={"name": "Pod\u0007casts"}).json()["channel"]
    assert named["name"] == "Pod casts"
