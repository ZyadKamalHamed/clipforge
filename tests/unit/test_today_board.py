"""The Today page's board (SPEC 14.10): yesterday's best posts by the views they gained, the rules of the
campaigns still running, each account's posts today against its cap, the posts to put up by hand and the
submissions waiting on you, each with the links it needs."""

from datetime import date

import pytest

from clipforge.campaigns import store as campaigns
from clipforge.campaigns import tracker
from clipforge.campaigns.models import Metric, Required
from clipforge.post import store
from clipforge.today import board, day
from tests.briefing import add_brief
from tests.fakes import Clock, FakePublisher, fake_engine
from tests.posting import add_account, add_campaign, add_clip, at, custom, linked_clip, posted

HOUR = 3600.0
TODAY = date(2026, 11, 3)
SUBMIT = "https://whop.com/beast/submit"


@pytest.fixture
def clock():
    return Clock(at(8, 0))  # in the first posting window, so posts can go up now


@pytest.fixture
def engine(clock):
    publishers = {
        "tiktok:browser": FakePublisher(),
        "instagram:api": FakePublisher(platform="instagram", method="api"),
    }
    eng = fake_engine(now=clock, publishers=publishers)
    yield eng
    eng.close()


def counted(engine, post, *counts):
    """A count of ``views`` taken ``hours`` before now for each ``(hours, views)``."""
    now = engine.now().timestamp()
    for hours, views in counts:
        campaigns.add_metric(
            engine.db(), Metric(post_id=post.id, captured_at=now - hours * HOUR, source="hand", views=views)
        )


def test_best_is_by_views_gained(engine, clock):
    clock.set(at(8, day=1))
    made = [posted(engine, add_clip(engine, n), add_account(engine, "tiktok", f"h{n}")) for n in range(1, 6)]
    clock.set(at(8, day=4))
    first, second, third, stale, fourth = made
    counted(engine, first, (30, 100), (2, 1100))  # 1,000 on the count 28 hours before
    counted(engine, second, (1, 500))  # its only count, so all 500 were gained
    counted(engine, third, (45, 100), (25, 1600), (10, 2000), (1, 2600))  # 1,000 on the newest 20 hours older
    counted(engine, stale, (71, 9000))  # its newest count is three days old
    counted(engine, fourth, (2, 400))
    best = board.best(engine, engine.now())
    assert [(b["post_id"], b["views"], b["gained"]) for b in best] == [
        (third.id, 2600, 1000),  # a tie goes to the higher count
        (first.id, 1100, 1000),
        (second.id, 500, 500),
    ]
    assert best[0] == {
        "post_id": third.id,
        "hook": third.clip_title,
        "handle": "@h3",
        "platform": "tiktok",
        "views": 2600,
        "gained": 1000,
        "url": third.url,
    }


def test_guidelines_list_active_campaigns_only(engine):
    beast = add_campaign(
        engine,
        campaign_url="https://whop.com/beast",
        rate_per_1000=1.5,
        min_views=1000,
        max_payout=50.0,
        required=Required(hashtags=["#a", "#b"], mentions=["@x"], text=["Link in bio"]),
        banned=["gameplay", "music"],
        platforms=["tiktok"],
        ends_on="2026-11-30",
        budget_left=800.0,
        budget_entered_on="2026-10-27",
    )
    add_campaign(engine, name="Marked ended", status="ended")
    add_campaign(engine, name="Past its date", ends_on="2026-11-02")
    add_campaign(engine, name="Spent", budget_left=0)
    quiet = add_campaign(engine, name="Quiet", marketplace="Vyro", currency="AUD")
    shown = board.guidelines(engine, TODAY)
    assert [g["name"] for g in shown] == ["Quiet", "Beast clips"]
    assert shown[1] == {
        "id": beast.id,
        "name": "Beast clips",
        "marketplace": "Whop",
        "currency": "USD",
        "rate": 1.5,
        "minimum": 1000,
        "cap": 50.0,
        "hashtags": ["#a", "#b"],
        "mentions": ["@x"],
        "text": ["Link in bio"],
        "banned": ["gameplay", "music"],
        "platforms": ["tiktok"],
        "ends_on": "2026-11-30",
        "budget_left": 800.0,
        "budget_entered_on": "2026-10-27",
        "page_link": f"{beast.id}:page",
    }
    assert (shown[0]["id"], shown[0]["page_link"], shown[0]["rate"]) == (quiet.id, None, None)


def test_accounts_say_today_as_their_cards_do(engine):
    tiktok = add_account(engine, "tiktok", "t0", daily_cap=2)
    reels = add_account(engine, "instagram", "i0")
    posted(engine, add_clip(engine, 1), tiktok)
    shown = {a["id"]: a for a in board.accounts(engine)}
    assert shown == {
        tiktok.id: {
            "id": tiktok.id,
            "handle": "@t0",
            "platform": "tiktok",
            "today": {"posts": 1, "cap": 2, "ceiling": 10},
        },
        reels.id: {
            "id": reels.id,
            "handle": "@i0",
            "platform": "instagram",
            "today": {"posts": 0, "cap": 1, "ceiling": 50},
        },
    }


def test_post_list_holds_exported_posts(engine):
    account = add_account(engine, "tiktok", "t0")
    out = custom(engine, add_clip(engine, 1), account, at(19))
    store.update_post(engine.db(), out.id, status="exported", caption="Watch this", hashtags=["a"])
    custom(engine, add_clip(engine, 3), add_account(engine, "tiktok", "t2"), at(19))  # ClipForge posts it
    shown = {p["post_id"]: p for p in board.post_list(engine)}
    assert set(shown) == {out.id}
    assert shown[out.id] == {
        "post_id": out.id,
        "status": "exported",
        "handle": "@t0",
        "platform": "tiktok",
        "hook": out.clip_title,
        "caption": "Watch this",
        "hashtags": ["a"],
        "full_caption": "Watch this\n\n#a",
        "scheduled_at": at(19).timestamp(),
        "has_file": False,
    }
    folder = engine.paths.exports / "t0" / "2026-11-03" / out.id
    folder.mkdir(parents=True)
    (folder / "clip.mp4").write_bytes(b"video")
    assert {p["post_id"]: p["has_file"] for p in board.post_list(engine)}[out.id] is True


def test_post_list_holds_posts_tiktok_studio_holds(engine):
    """A post TikTok Studio holds for its slot is one you see through: it shows with its time, soonest
    first, beside the exported ones."""
    account = add_account(engine, "tiktok", "t0")
    held = custom(engine, add_clip(engine, 1), account, at(20))
    store.update_post(engine.db(), held.id, status="uploading")
    store.update_post(engine.db(), held.id, status="scheduled_on_tiktok", state={"on_tiktok": True})
    out = custom(engine, add_clip(engine, 2), add_account(engine, "tiktok", "t1"), at(19))
    store.update_post(engine.db(), out.id, status="exported")
    shown = board.post_list(engine)
    assert [(p["post_id"], p["status"]) for p in shown] == [
        (out.id, "exported"),
        (held.id, "scheduled_on_tiktok"),
    ]
    assert shown[1]["scheduled_at"] == at(20).timestamp()
    assert shown[1]["handle"] == "@t0"


def test_submit_list_holds_links_and_due_views(engine, clock):
    beast = add_campaign(engine, rate_per_1000=2.0, submit_url=SUBMIT)

    def sub(post):
        return campaigns.submission_of_post(engine.db(), post.id)

    clock.set(at(8, day=1))
    old = posted(engine, linked_clip(engine, beast, 1), add_account(engine, "tiktok", "a"))
    sent = posted(engine, linked_clip(engine, beast, 2), add_account(engine, "tiktok", "b"))
    tracker.move_submission(engine, sub(sent).id, "submitted")
    clock.set(at(8, 30, day=2))
    young = posted(engine, linked_clip(engine, beast, 3), add_account(engine, "tiktok", "c"))
    tracker.move_submission(engine, sub(young).id, "submitted")  # not a day ago yet
    fresh = posted(engine, linked_clip(engine, beast, 4), add_account(engine, "tiktok", "d"))
    clock.set(at(8, day=3))
    shown = {s["post_id"]: s for s in board.submit_list(engine, TODAY)}
    assert set(shown) == {old.id, sent.id, fresh.id}
    expected = {
        "id": sub(old).id,
        "post_id": old.id,
        "campaign_id": beast.id,
        "status": "to_submit",
        "views_due": "2 days",
        "handle": "@a",
        "platform": "tiktok",
        "hook": old.clip_title,
        "url": old.url,
        "campaign_name": "Beast clips",
        "marketplace": "Whop",
        "currency": "USD",
        "submit_link": f"{beast.id}:submit",
        "todo": ["submit", "views"],
    }
    assert {k: shown[old.id][k] for k in expected} == expected
    assert shown[old.id]["post"]["url"] == old.url  # the row a campaign's page shows, as it is
    assert (shown[sent.id]["status"], shown[sent.id]["todo"]) == ("submitted", ["decide", "views"])
    assert (shown[fresh.id]["status"], shown[fresh.id]["todo"]) == ("to_submit", ["submit"])


def test_join_line_on_the_triangle(engine):
    brief = add_brief(engine, niche="Gaming", rate_per_1000=1.5, status="used")
    campaign = add_campaign(engine, brief_id=brief.id)
    items = day.refresh(engine)
    assert {"kind": "join", "title": "Join on Whop", "count": 1} in board.campaign_todo(
        engine, items, campaign, TODAY
    )
