"""SPEC 10.10, 14.11, 14.12 and 16: the campaigns API. Campaigns made, read, changed and deleted with each
refusal a sentence for its field; pasted rules read without saving anything; submissions moved only the
ways a marketplace answers; view counts entered by hand within bounds; clipping channels and projects
linked by their own PATCH; a draft's hand rules ticked; and links opened only by the id of one ClipForge
stored, checked again before macOS ``open`` gets it."""

import time
from datetime import date
from urllib.parse import quote

import pytest

from clipforge import repo
from clipforge.briefs import store as briefs
from clipforge.campaigns import links, parse, store
from clipforge.campaigns.models import Footage, Submission
from clipforge.campaigns.rules import hand_key
from clipforge.config import Marketplace
from clipforge.post import store as post_store
from clipforge.today.needs import needs
from clipforge.trends import feed, shortlist
from clipforge.trends import store as trends
from tests.briefing import add_brief
from tests.fakes import Clock, FakeProvider, FakePublisher, fake_engine
from tests.posting import (
    add_account,
    add_campaign,
    add_clip,
    at,
    bare_post,
    campaign_post,
    linked_clip,
    posted,
)
from tests.server import api_client
from tests.unit.test_api_drafts import pair
from tests.unit.test_trends_store import ig_account, trending
from tests.watching import add_channel

RULES = "Earn $1.50 per 1,000 views on TikTok. Tag #MrBeast."
SUBMIT = "https://whop.com/beast/submit"
GONE = "That campaign isn't in ClipForge any more."


def answer(req):
    """Claude's reading of RULES: the name, the rate and the hashtag, every other field null."""
    given = {"name": "Beast clips", "rate_per_1000": 1.5, "hashtags": ["#MrBeast"]}
    return {name: None for name in parse.RulesAnswer.model_fields} | given


@pytest.fixture
def clock():
    return Clock(at(7, 10))


@pytest.fixture
def engine(clock):
    publishers = {
        "tiktok:browser": FakePublisher(),
        "instagram:api": FakePublisher(platform="instagram", method="api"),
    }
    eng = fake_engine(now=clock, publishers=publishers, provider=FakeProvider({"campaign": answer}))
    yield eng
    eng.close()


@pytest.fixture
def client(engine):
    return api_client(engine)


@pytest.fixture
def heard(engine):
    events = []
    engine.events.listen(lambda e: events.append((e.topic, e.data)))
    return events


def submission(engine, campaign, status: str = "posted") -> Submission:
    """A submission to ``campaign`` of a bare post with ``status``."""
    now = time.time()
    post_id = bare_post(engine.db(), status)
    made = Submission(
        id=repo.new_id("sb"), post_id=post_id, campaign_id=campaign.id, created_at=now, updated_at=now
    )
    return store.add_submission(engine.db(), made)


def opened(engine) -> list[list[str]]:
    return [c for c in engine.run_cmd.calls if c[:1] == ["open"]]


def test_campaign_crud(client, engine, heard):
    sent = {
        "name": "Beast clips",
        "marketplace": "Whop",
        "rate_per_1000": 1.5,
        "campaign_url": "https://whop.com/beast",
        "submit_url": SUBMIT,
    }
    r = client.post("/api/campaigns", json=sent)
    assert r.status_code == 201, r.text
    made = r.json()["campaign"]
    cid = made["id"]
    assert (made["name"], made["marketplace"], made["rate_per_1000"]) == ("Beast clips", "Whop", 1.5)
    assert made["links"] == {"page": f"{cid}:page", "submit": f"{cid}:submit"}
    assert made["ended"] is None
    assert made["channels"] == []
    assert made["money"]["counts"] == {
        "made": 0,
        "approved": 0,
        "live": 0,
        "submitted": 0,
        "accepted": 0,
        "paid": 0,
    }
    assert ("campaign", {"id": cid}) in heard

    listed = client.get("/api/campaigns").json()
    assert [c["id"] for c in listed["campaigns"]] == [cid]
    (line,) = listed["totals"]
    assert (line["marketplace"], line["currency"], line["withdraw_link"]) == ("Whop", "USD", None)

    one = client.get(f"/api/campaigns/{cid}").json()
    assert (one["campaign"]["id"], one["submissions"]) == (cid, [])

    r = client.patch(f"/api/campaigns/{cid}", json={"status": "ended"})
    assert r.status_code == 200, r.text
    assert r.json()["campaign"]["ended"] == "You marked Beast clips ended."

    r = client.patch(f"/api/campaigns/{cid}", json={"rate_per_1000": -1})
    assert r.status_code == 422
    assert r.json() == {
        "error": "campaign_field",
        "message": "The rate per 1,000 views must be a number of 0 or more.",
        "field": "rate_per_1000",
    }
    r = client.post("/api/campaigns", json={"name": "x", "marketplace": "Whop", "nme": "x"})
    assert (r.status_code, r.json()["field"]) == (422, "nme")
    assert r.json()["message"] == "A campaign has no field called nme."
    assert client.post("/api/campaigns", json=["not", "a", "form"]).status_code == 422
    assert client.patch("/api/campaigns/cp_missing", json={"status": "ended"}).status_code == 404
    assert client.get("/api/campaigns/cp_missing").status_code == 404

    assert client.delete(f"/api/campaigns/{cid}").status_code == 204
    assert client.get(f"/api/campaigns/{cid}").status_code == 404
    assert client.delete(f"/api/campaigns/{cid}").status_code == 404


def test_parse_route_saves_nothing(client, engine):
    r = client.post("/api/campaigns/parse", json={"text": RULES})
    assert r.status_code == 200, r.text
    got = r.json()
    assert (got["fields"]["name"], got["fields"]["rate_per_1000"]) == ("Beast clips", 1.5)
    assert got["fields"]["required"]["hashtags"] == ["MrBeast"]
    assert {"name", "rate_per_1000"} <= set(got["filled"])
    assert got["sentence"] is None
    assert store.list_campaigns(engine.db()) == []

    r = client.post("/api/campaigns/parse", json={"text": "   "})
    assert r.status_code == 422
    assert r.json() == {"error": "rules_empty", "message": parse.EMPTY, "field": "text"}
    r = client.post("/api/campaigns/parse", json={"text": "x" * (parse.MAX_RULES_CHARS + 1)})
    assert r.status_code == 422
    assert r.json() == {"error": "rules_too_long", "message": parse.TOO_LONG, "field": "text"}
    assert store.list_campaigns(engine.db()) == []


def test_delete_refused_is_409_with_the_sentence(client, engine):
    campaign = add_campaign(engine)
    submission(engine, campaign)
    r = client.delete(f"/api/campaigns/{campaign.id}")
    assert r.status_code == 409
    assert r.json() == {
        "error": "has_submissions",
        "message": "This campaign has 1 submission, so mark it ended instead.",
        "field": None,
    }
    assert store.get_campaign(engine.db(), campaign.id) is not None


def test_submission_patch_moves_and_refuses(client, engine, heard):
    campaign = add_campaign(engine, rate_per_1000=2.0)
    sub = submission(engine, campaign)

    def move(**body):
        return client.patch(f"/api/submissions/{sub.id}", json=body)

    r = move(status="submitted")
    assert r.status_code == 200, r.text
    moved = r.json()["submission"]
    assert (moved["status"], moved["submitted_at"] is not None) == ("submitted", True)
    assert moved["moves"] == ["to_submit", "approved", "rejected"]
    assert moved["post"]["platform"] == "tiktok"
    assert (
        "submission",
        {"id": sub.id, "post_id": sub.post_id, "campaign_id": campaign.id, "status": "submitted"},
    ) in heard

    r = move(status="paid", paid_amount=3)
    assert r.status_code == 409
    assert r.json() == {
        "error": "wrong_status",
        "message": "A submission can't go from Submitted to Paid.",
        "field": None,
    }
    assert move(status="approved").status_code == 200
    r = move(status="paid")
    assert (r.status_code, r.json()["field"], r.json()["message"]) == (
        422,
        "paid_amount",
        "Enter the amount paid.",
    )
    r = move(status="paid", paid_amount=12.5)
    assert r.json()["submission"]["paid_amount"] == 12.5
    assert move(status="approved").status_code == 200
    r = move(status="rejected", reason="x" * 301)
    assert (r.status_code, r.json()["field"]) == (422, "reason")
    r = move(status="rejected", reason="Watermark showing.")
    assert r.json()["submission"]["reason"] == "Watermark showing."
    assert move(status="bogus").status_code == 422
    assert client.patch("/api/submissions/sb_missing", json={"status": "submitted"}).status_code == 404

    listed = client.get("/api/submissions", params={"campaign_id": campaign.id}).json()["submissions"]
    assert [s["id"] for s in listed] == [sub.id]
    assert client.get("/api/submissions", params={"status": "paid"}).json()["submissions"] == []
    assert (
        client.get("/api/submissions", params={"status": "rejected"}).json()["submissions"][0]["id"] == sub.id
    )
    assert client.get("/api/submissions", params={"status": "bogus"}).status_code == 422


def test_views_route_bounds(client, engine):
    campaign = add_campaign(engine, rate_per_1000=2.0)
    sub = submission(engine, campaign)

    def views(value, post_id=sub.post_id):
        return client.post(f"/api/posts/{post_id}/views", json={"views": value})

    for good in (0, 10**10):
        r = views(good)
        assert r.status_code == 200, r.text
    assert r.json()["submission"]["views"] == 10**10
    assert r.json()["submission"]["views_source"] == "hand"
    for bad in (10**10 + 1, -1, 1.5, True, "12", None):
        r = views(bad)
        assert r.status_code == 422, bad
        assert (r.json()["field"], r.json()["message"]) == ("views", "Enter the views as a whole number.")
    loose = bare_post(engine.db(), "posted")
    assert views(5, loose).json() == {
        "submission": None
    }  # a post of no campaign keeps its count all the same
    scheduled = bare_post(engine.db(), "scheduled")
    r = views(5, scheduled)
    assert (r.status_code, r.json()["message"]) == (409, "Only a posted post has views.")
    assert views(5, "po_missing").status_code == 404
    assert [m.views for m in store.metrics_of(engine.db(), sub.post_id)] == [0, 10**10]


def test_channel_and_project_patch_link(client, engine):
    campaign = add_campaign(engine)
    channel = add_channel(engine, [add_account(engine)])

    r = client.patch(f"/api/clip-channels/{channel.id}", json={"campaign_id": campaign.id})
    assert r.status_code == 200, r.text
    card = r.json()["channel"]
    assert card["campaign"] == {"id": campaign.id, "name": "Beast clips", "ended": None}
    assert card["rights_basis"] == "clipping_programme"
    r = client.patch(f"/api/clip-channels/{channel.id}", json={"campaign_id": "cp_missing"})
    assert r.json() == {"error": "invalid", "message": GONE, "field": "campaign_id"}
    r = client.patch(f"/api/clip-channels/{channel.id}", json={"campaign_id": None})
    assert (r.json()["channel"]["campaign"], r.json()["channel"]["rights_basis"]) == (None, "not_cleared")
    assert client.get(f"/api/campaigns/{campaign.id}").json()["campaign"]["channels"] == []

    project_id = add_clip(engine).project_id
    r = client.patch(f"/api/projects/{project_id}", json={"campaign_id": campaign.id})
    assert r.status_code == 200, r.text
    assert (r.json()["campaign_id"], r.json()["rights_basis"]) == (campaign.id, "clipping_programme")
    r = client.patch(f"/api/projects/{project_id}", json={"campaign_id": None})
    assert (r.json()["campaign_id"], r.json()["rights_basis"]) == (None, "not_cleared")
    r = client.patch(f"/api/projects/{project_id}", json={"campaign_id": "cp_missing"})
    assert (r.status_code, r.json()["field"], r.json()["message"]) == (422, "campaign_id", GONE)
    strict = add_campaign(engine, name="Only one video", footage=Footage(videos=["dQw4w9WgXcQ"]))
    r = client.patch(f"/api/projects/{project_id}", json={"campaign_id": strict.id})
    assert r.status_code == 409
    assert r.json() == {
        "error": "not_allowed",
        "message": "The campaign Only one video doesn't allow footage from this channel.",
        "field": None,
    }
    assert engine.project(project_id).campaign_id is None


def test_open_link_takes_only_stored_ids(client, engine):
    campaign = add_campaign(engine, submit_url=SUBMIT)

    def open_(link_id):
        return client.post(f"/api/links/{quote(link_id, safe=':')}/open")

    assert open_("nope").status_code == 404
    assert open_(f"{campaign.id}:page").status_code == 404  # it has no campaign page saved
    assert open_(f"{campaign.id}:login").status_code == 404
    assert open_("cp_missing:submit").status_code == 404
    assert client.post(f"/api/links/{quote(SUBMIT, safe='')}/open").status_code == 404
    assert open_("withdraw:Whop").status_code == 404  # no Withdraw link saved yet
    assert opened(engine) == []

    assert open_(f"{campaign.id}:submit").status_code == 204
    assert opened(engine) == [["open", SUBMIT]]

    payouts = "https://whop.com/payouts"
    engine.settings = engine.settings.model_copy(
        update={"marketplaces": {"Whop": Marketplace(withdraw_url=payouts)}}
    )
    assert open_("withdraw:whop").status_code == 204  # named alike whatever the case
    assert opened(engine)[-1] == ["open", payouts]


def test_open_refuses_a_bad_stored_link(client, engine):
    campaign = add_campaign(engine, campaign_url="https://whop.com/beast")
    engine.db().execute("UPDATE campaigns SET campaign_url = 'file:///etc' WHERE id = ?", (campaign.id,))
    r = client.post(f"/api/links/{campaign.id}:page/open")
    assert r.status_code == 409
    assert r.json() == {
        "error": "bad_link",
        "message": "That link isn't a web address ClipForge can open, so fix it on the campaign first.",
        "field": None,
    }
    assert opened(engine) == []


def test_sound_and_list_links_open_only_their_hosts(client, engine):
    conn = engine.db()

    def open_(link_id):
        return client.post(f"/api/links/{quote(link_id, safe=':')}/open")

    mine = shortlist.add(engine, "tiktok.com/music/Night-Drive-7234567890123456789?lang=en")
    assert open_(f"sound:{mine.id}").status_code == 204
    assert opened(engine)[-1] == ["open", "https://www.tiktok.com/music/Night-Drive-7234567890123456789"]
    ig = ig_account(conn)
    linked, bare = trending(ig.id, "a1", 0), trending(ig.id, "a2", 1)
    linked.url = "https://www.instagram.com/reels/audio/a1/"
    trends.replace_trending(conn, ig.id, "music", [linked, bare], time.time())
    assert open_(f"sound:{linked.id}").status_code == 204
    assert opened(engine)[-1] == ["open", "https://www.instagram.com/reels/audio/a1/"]
    count = len(opened(engine))
    assert open_(f"sound:{bare.id}").status_code == 404  # Instagram gave it no link
    assert open_("sound:snd_missing").status_code == 404
    # a stored link that isn't on its platform's own site, or isn't a web address, never opens
    for platform_row, bad in (
        (mine, "https://evil.example/music/x"),
        (linked, "https://www.tiktok.com/x"),
        (mine, "file:///etc/passwd"),
    ):
        conn.execute("UPDATE sounds SET url = ? WHERE id = ?", (bad, platform_row.id))
        r = open_(f"sound:{platform_row.id}")
        assert r.status_code == 409 and r.json()["message"] == links.BAD_SOUND, bad
    assert links.BAD_SOUND == "That sound's link isn't on TikTok or Instagram, so it shows as text only."
    # the weekly lists open by name, and only the four ClipForge knows
    for name, url in feed.LISTS.items():
        assert open_(f"list:{name}").status_code == 204
        assert opened(engine)[-1] == ["open", url]
    assert open_("list:tokchart").status_code == 404
    assert open_("list:").status_code == 404
    assert len(opened(engine)) == count + len(feed.LISTS)


def test_plan_names_each_posts_campaign_and_ticks_its_rules(client, engine):
    """The Post dialog's plan says each post's campaign by name and its rules in clean text, and a tick
    sent in the post's edit passes the rule."""
    rule = "No watermark\u202e"
    campaign = add_campaign(engine, name="Beast\u202e clips", hand_rules=[rule])
    clip, account = linked_clip(engine, campaign), add_account(engine)
    asked = {"clip_ids": [clip.id], "account_ids": [account.id]}
    (p,) = client.post("/api/posts/plan", json=asked).json()["posts"]
    assert p["campaign"] == {"id": campaign.id, "name": "Beast clips"}
    hand = p["rules"][-1]
    assert (hand["key"], hand["label"], hand["ok"]) == (hand_key(rule), "No watermark", False)
    edits = {f"{clip.id}:{account.id}": {"rule_ticks": [hand_key(rule)]}}
    planned = client.post("/api/posts/plan", json=asked | {"edits": edits}).json()
    assert planned["posts"][0]["rules"][-1]["ok"] is True and not planned["blocked"]


def test_draft_edit_ticks_a_rule(client, engine):
    made, _, _ = pair(engine)
    (project_id,) = {repo.get_clip(engine.db(), d.clip_id).project_id for d in made}
    campaign = add_campaign(engine, hand_rules=["No watermark"])
    repo.update_project(engine.db(), project_id, campaign_id=campaign.id, rights_basis="clipping_programme")
    draft = made[1]

    def rule():
        (group,) = client.get("/api/drafts").json()["groups"]
        (card,) = [d for v in group["videos"] for d in v["drafts"] if d["id"] == draft.id]
        return card, next((r for r in card["rules"] if r["key"] == hand_key("No watermark")), None)

    card, hand = rule()
    assert hand["ok"] is False
    assert "rule_unchecked" in {w["code"] for w in card["warnings"]}

    r = client.patch(f"/api/posts/{draft.id}", json={"rule_ticks": [hand_key("No watermark")] * 2})
    assert r.status_code == 200, r.text
    assert post_store.get_post(engine.db(), draft.id).state["rule_ticks"] == [hand_key("No watermark")]
    card, hand = rule()
    assert hand["ok"] is True
    assert "rule_unchecked" not in {w["code"] for w in card["warnings"]}

    client.patch(f"/api/posts/{draft.id}", json={"rule_ticks": []})
    assert "rule_ticks" not in post_store.get_post(engine.db(), draft.id).state
    client.patch(f"/api/posts/{draft.id}", json={"off_campaign": True})
    card, hand = rule()
    assert (card["campaign"], hand) == (None, None)  # an ordinary draft now
    client.patch(f"/api/posts/{draft.id}", json={"off_campaign": False})
    assert rule()[0]["campaign"]["id"] == campaign.id

    r = client.patch(f"/api/posts/{draft.id}", json={"rule_ticks": ["x" * 21]})
    assert r.status_code == 422
    other = bare_post(engine.db(), "scheduled")
    r = client.patch(f"/api/posts/{other}", json={"off_campaign": True})
    assert r.status_code == 409


def joins(engine) -> list[tuple[str, list[str], str, float, bool, dict[str, int]]]:
    return [
        (n.ref, n.ids, n.title, n.value, n.guess, n.campaigns)
        for n in needs(engine, date(2026, 11, 3))
        if n.kind == "join"
    ]


def test_save_from_a_brief_marks_it_used(client, engine, heard):
    brief = add_brief(engine, niche="Gaming", rate_per_1000=1.5)
    sent = {"name": "Beast clips", "marketplace": "Whop", "rate_per_1000": 1.5, "brief_id": brief.id}
    r = client.post("/api/campaigns", json=sent)
    assert r.status_code == 201, r.text
    cid = r.json()["campaign"]["id"]
    assert store.get_campaign(engine.db(), cid).brief_id == brief.id
    used = briefs.get_brief(engine.db(), brief.id)
    assert (used.status, used.campaign_id) == ("used", cid)
    assert ("brief", {"ids": [brief.id], "status": "used"}) in heard
    again = client.post("/api/campaigns", json=sent)
    assert again.status_code == 422
    assert again.json() == {
        "error": "campaign_field",
        "message": "This brief is a campaign now, so it stays with that campaign.",
        "field": "brief_id",
    }
    missing = client.post("/api/campaigns", json=sent | {"brief_id": "br_nope"})
    assert (missing.status_code, missing.json()["field"]) == (422, "brief_id")
    gone = add_brief(engine, niche="Gaming", status="gone")
    assert client.post("/api/campaigns", json=sent | {"brief_id": gone.id}).status_code == 422
    assert len(store.list_campaigns(engine.db())) == 1  # nothing saved for a refusal
    assert client.post("/api/campaigns", json=sent | {"brief_id": 7}).status_code == 422


def test_join_until_joined(client, engine, clock, heard):
    brief = add_brief(engine, niche="Gaming", rate_per_1000=1.5)
    sent = {"name": "Beast clips", "marketplace": "Whop", "rate_per_1000": 1.5, "brief_id": brief.id}
    cid = client.post("/api/campaigns", json=sent).json()["campaign"]["id"]
    assert joins(engine) == [(cid, [cid], "Join Beast clips on Whop", 1.5, True, {cid: 1})]
    r = client.patch(f"/api/campaigns/{cid}", json={"joined": True})
    assert r.status_code == 200, r.text
    assert r.json()["campaign"]["joined_at"] == clock().timestamp()
    assert ("campaign", {"id": cid}) in heard
    assert joins(engine) == []
    assert client.patch(f"/api/campaigns/{cid}", json={"joined": "yes"}).status_code == 422
    plain = client.post("/api/campaigns", json=sent | {"brief_id": None, "name": "Plain"})
    assert plain.status_code == 201, plain.text
    assert joins(engine) == []  # a campaign you made yourself has nothing to join
    assert client.patch("/api/campaigns/cp_nope", json={"joined": True}).status_code == 404


def test_campaign_rows_carry_their_state(client, engine):
    add_campaign(engine, name="Soon", ends_on="2026-11-04")  # the engine's posting day is 3 Nov
    add_campaign(engine, name="Later", ends_on="2026-11-30")
    add_campaign(engine, name="Over", status="ended")
    rows = {c["name"]: c["state"] for c in client.get("/api/campaigns").json()["campaigns"]}
    assert rows == {"Soon": "ending", "Later": "active", "Over": "ended"}


def test_campaign_rows_carry_their_us_rate(client, engine):
    """What one of each campaign's currency is worth in US$ by your rates, so the sheet sorts its amounts
    across currencies; a currency with no rate counts as if it were US$, as everywhere else."""
    settings = engine.settings
    engine.settings = settings.model_copy(
        update={"today": settings.today.model_copy(update={"rates": {"AUD": 0.66}})}
    )
    add_campaign(engine, name="Aussie", currency="AUD")
    add_campaign(engine, name="Euro", currency="EUR")
    add_campaign(engine, name="Dollar")
    rows = {c["name"]: c["usd_rate"] for c in client.get("/api/campaigns").json()["campaigns"]}
    assert rows == {"Aussie": 0.66, "Euro": 1.0, "Dollar": 1.0}


MONEY_POST = {
    "post_id", "posted_on", "account_id", "handle", "platform", "niche", "campaign_id", "campaign",
    "marketplace", "hook", "url", "views_2d", "views_7d", "views", "status", "reason", "currency",
    "estimated", "pending", "paid", "rate", "as_if",
}  # fmt: skip


def rows_of(engine, table: str) -> list[tuple]:
    return [tuple(r) for r in engine.db().execute(f"SELECT * FROM {table} ORDER BY rowid")]


def test_money_route_reads_only(client, engine):
    beast = add_campaign(engine, rate_per_1000=2.0)
    t1 = add_account(engine, "tiktok", "t1")
    sent = campaign_post(engine, beast, t1, days_ago=8, counts=((7, 5000),), status="approved")
    missed = posted(engine, linked_clip(engine, beast), t1)
    engine.db().execute("DELETE FROM submissions WHERE post_id = ?", (missed.id,))  # one the sweep makes
    before = {table: rows_of(engine, table) for table in ("posts", "submissions", "post_metrics")}

    r = client.get("/api/money")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"today", "posts", "accounts", "as_if"}
    assert (body["today"], body["as_if"]) == ("2026-11-03", [])
    assert {p["post_id"] for p in body["posts"]} == {sent.id, missed.id}
    assert all(set(p) == MONEY_POST for p in body["posts"])
    assert [(a["id"], a["handle"]) for a in body["accounts"]] == [(t1.id, "@t1")]

    after = {table: rows_of(engine, table) for table in ("posts", "submissions", "post_metrics")}
    assert after["posts"] == before["posts"]
    assert after["post_metrics"] == before["post_metrics"]
    new = [row for row in after["submissions"] if row not in before["submissions"]]
    assert len(after["submissions"]) == len(before["submissions"]) + 1 and len(new) == 1
