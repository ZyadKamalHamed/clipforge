"""SPEC 10.10 from start to finish: a clipping channel linked to a paid campaign drafts at its check time,
each draft rendered and captioned by the campaign's rules; approval waits for the rule you check by hand;
the posts go up at their slots and each gets a submission; Instagram's insights read one post's views, the
other's are due by hand; and once paid, the totals show it. A draft whose render breaks a rule added later
is blocked naming it until it is rendered again. Fakes for YouTube and its feed, speech, the LLM, the
render, Graph and the sites; a real database and real job workers."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from clipforge import repo
from clipforge.campaigns import insights
from clipforge.campaigns import store as campaigns
from clipforge.campaigns.rules import campaign_of, enforce, hand_key
from clipforge.jobs import pipeline
from clipforge.post import keychain
from clipforge.post import store as post_store
from clipforge.render.edl import EDL
from clipforge.render.styles import style_for
from clipforge.watch import check, schedule
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
from tests.fakes_graph import PAGE_TOKEN, FakeGraph
from tests.integration.test_watch_run import EP2, FEED, LIVE, MORNING, SHORT, podcasts
from tests.posting import add_account, add_campaign, add_render, run
from tests.server import api_client

FIRE = "\U0001f525"
RULES = {
    "rate_per_1000": 2.0,
    "max_s": 45.0,
    "banned": ["gameplay", "emoji"],
    "required": {"hashtags": ["Beast"], "mentions": ["@MrBeast"]},
    "hand_rules": ["No reaction content"],
}


def rendered(engine, ctx):
    """The render's stand-in: the style the real render takes (the clip's campaign's banned elements off,
    as clip.render works it out), an edit list holding each element that style leaves on, and a version
    that passes QA, made the clip's current one."""
    with engine.lock:
        clip = repo.get_clip(engine.db(), ctx.job.ref_id)
        campaign = campaign_of(engine, engine.project(clip.project_id))
        style = enforce(style_for(ctx.settings, clip.edits or {}), campaign)
        edl = EDL(
            clip_id=clip.id,
            fps=30.0,
            src_width=1920,
            src_height=1080,
            duration=clip.end_s - clip.start_s,
            segments=[],
            style=style,
            emoji=[{"text": FIRE, "start_s": 1.0}] if style.emoji else [],
            gameplay={"item_id": "g1", "start_s": 0.0} if style.gameplay_mode != "off" else None,
        )
        version = 1 + len(repo.list_renders(engine.db(), clip.id))
        render = add_render(engine, clip, version, edl=edl)
        engine.update_clip(clip.id, status="rendered")
    return {"render_id": render.id, "version": render.version, "status": "ready"}


@pytest.fixture
def clock():
    return Clock(MORNING)


@pytest.fixture
def graph(clock):
    return FakeGraph(now=clock)


@pytest.fixture
def tiktok():
    return FakePublisher()


@pytest.fixture
def instagram():
    return FakePublisher(platform="instagram", method="api")


@pytest.fixture
def engine(monkeypatch, clock, graph, tiktok, instagram):
    monkeypatch.setattr(check, "FEED_GAP_S", 0.0)
    monkeypatch.setitem(pipeline.CLIP_FUNCTIONS, "clip.source", lambda engine, ctx: None)
    monkeypatch.setitem(pipeline.CLIP_FUNCTIONS, "clip.analyze", lambda engine, ctx: None)
    monkeypatch.setitem(pipeline.CLIP_FUNCTIONS, "clip.render", rendered)
    youtube = FakeYouTube(seconds=600.0)
    youtube.videos = {video_id: {} for video_id in (SHORT, LIVE, EP2)}
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
    eng.graph_transport = graph.transport()
    yield eng
    eng.close()


def accounts(engine):
    """A TikTok account and an Instagram account posting through the API, its token in the Keychain."""
    reels = add_account(engine, "instagram", "i0")
    ref = keychain.ref_for(reels.id)
    keychain.put_token(ref, PAGE_TOKEN)
    reels = post_store.update_account(
        engine.db(), reels.id, ig_mode="facebook", token_ref=ref, ig_user_id="ig1"
    )
    return [add_account(engine, "tiktok", "t0"), reels]


def drafted(engine, client, campaign_fields: dict) -> tuple[str, list[dict]]:
    """A clipping channel linked through the API to a new campaign, checked at 06:00 with nobody pressing
    anything: the campaign's id and the drafts Today shows."""
    campaign = add_campaign(engine, **campaign_fields)
    channel = podcasts(engine, accounts(engine))
    linked = client.patch(f"/api/clip-channels/{channel.id}", json={"campaign_id": campaign.id})
    assert linked.status_code == 200, linked.text
    assert linked.json()["channel"]["rights_basis"] == "clipping_programme"
    (made,) = schedule.tick(engine)
    assert engine.runner.run_until_idle(timeout_s=30, refs=[made.id])
    projects = [p.id for p in repo.list_projects(engine.db())]
    assert engine.wait(timeout_s=120, projects=projects)
    today = client.get("/api/drafts").json()
    drafts = [d for g in today["groups"] for v in g["videos"] for d in v["drafts"]]
    assert drafts, "the check made no drafts"
    return campaign.id, drafts


def plan(client, ids: list[str]) -> dict:
    planned = client.post("/api/posts/plan", json={"draft_ids": ids})
    assert planned.status_code == 200, planned.text
    return planned.json()


def blocking(planned: dict) -> set[str]:
    return {w["sentence"] for w in planned["warnings"] if w["blocking"]}


def test_a_linked_channel_from_check_to_paid(engine, clock, graph, tiktok, instagram):
    client = api_client(engine)
    campaign_id, drafts = drafted(engine, client, RULES)
    conn = engine.db()
    for d in drafts:
        assert d["campaign"] == {"id": campaign_id, "name": "Beast clips"}
        post = post_store.get_post(conn, d["id"])
        render = repo.get_render(conn, repo.get_clip(conn, post.clip_id).current_render_id)
        edl = EDL.model_validate_json(Path(render.edl_path).read_bytes())
        assert (edl.gameplay, edl.emoji) == (None, [])  # the banned elements are off in the render
        assert render.duration_s <= 45.0
        assert "Beast" in post.hashtags and "@MrBeast" in post.caption
        rules = {r["key"]: r for r in d["rules"]}
        hand = hand_key("No reaction content")
        assert not rules[hand]["ok"] and rules[hand]["kind"] == "hand"
        assert all(r["ok"] for k, r in rules.items() if k != hand), d["rules"]

    ids = [d["id"] for d in drafts]
    held = plan(client, ids)
    assert held["blocked"]
    assert blocking(held) == {"Beast clips: No reaction content. Check this yourself, then tick it."}
    for d in drafts:
        ticked = client.patch(f"/api/posts/{d['id']}", json={"rule_ticks": [hand_key("No reaction content")]})
        assert ticked.status_code == 200, ticked.text
    cleared = plan(client, ids)
    assert not cleared["blocked"], cleared["warnings"]
    confirmed = client.post(
        "/api/posts/confirm", json={"plan_id": cleared["plan_id"], "request": {"draft_ids": ids}}
    )
    assert confirmed.status_code == 201, confirmed.text
    posts = sorted(confirmed.json()["posts"], key=lambda p: p["scheduled_at"])
    assert {p["status"] for p in posts} == {"scheduled"}

    for p in posts:
        clock.set(datetime.fromtimestamp(p["scheduled_at"], UTC))
        assert run(engine, p["id"])[1].status == "posted"
    subs = {s.post_id: s for s in campaigns.list_submissions(conn, campaign_id)}
    assert set(subs) == {p["id"] for p in posts}
    assert {s.status for s in subs.values()} == {"to_submit"}

    reels = [p for p in posts if post_store.get_post(conn, p["id"]).platform == "instagram"]
    clips = [p for p in posts if post_store.get_post(conn, p["id"]).platform == "tiktok"]
    assert reels and clips
    clock.set(datetime.fromtimestamp(posts[-1]["scheduled_at"], UTC) + timedelta(hours=1))
    assert insights.read_due(engine, pause=lambda s: None) == len(reels)
    two_days = datetime.fromtimestamp(posts[-1]["scheduled_at"], UTC) + timedelta(days=2, hours=1)
    clock.set(two_days)
    shown = {s["post_id"]: s for s in client.get(f"/api/campaigns/{campaign_id}").json()["submissions"]}
    for p in reels:
        assert (shown[p["id"]]["views"], shown[p["id"]]["views_source"]) == (1200, "insights")
        assert shown[p["id"]]["views_due"] is None
    for p in clips:
        assert shown[p["id"]]["views_due"] == "2 days"

    paid = reels[0]["id"]
    for status, more in (("submitted", {}), ("approved", {}), ("paid", {"paid_amount": 2.4})):
        moved = client.patch(f"/api/submissions/{subs[paid].id}", json={"status": status, **more})
        assert moved.status_code == 200, moved.text
    sheet = client.get("/api/campaigns").json()
    (line,) = sheet["totals"]
    assert (line["marketplace"], line["currency"], line["paid"]) == ("Whop", "USD", 2.4)
    (row,) = sheet["campaigns"]
    assert row["money"]["paid"] == 2.4
    assert {x.id for x in tiktok.published} == {p["id"] for p in clips}
    assert {x.id for x in instagram.published} == {p["id"] for p in reels}


def test_a_draft_breaking_a_rule_is_blocked_until_rerendered(engine, clock):
    client = api_client(engine)
    campaign_id, drafts = drafted(engine, client, {"rate_per_1000": 2.0})
    ids = [d["id"] for d in drafts]
    assert not plan(client, ids)["blocked"]
    changed = client.patch(f"/api/campaigns/{campaign_id}", json={"banned": ["emoji"]})
    assert changed.status_code == 200, changed.text

    held = plan(client, ids)  # the rules as they are now, not as they were at drafting
    assert held["blocked"]
    assert blocking(held) == {
        "Beast clips: No emoji. The render still has emoji. Render it again to take it out."
    }
    conn = engine.db()
    clips = {post_store.get_post(conn, i).clip_id for i in ids}
    for clip_id in clips:
        again = client.post(f"/api/clips/{clip_id}/render", json={})
        assert again.status_code == 202, again.text
    assert engine.runner.run_until_idle(timeout_s=60)
    assert all(len(repo.list_renders(conn, c)) == 2 for c in clips)
    fixed = plan(client, ids)
    assert not fixed["blocked"], fixed["warnings"]
