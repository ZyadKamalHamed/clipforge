"""SPEC 10.11 and 16: the briefs API. The sources as Settings lists them, "Add a site" (a public list page
whose terms you ticked as checked), on and off, and Check now: at most once an hour, one read at a time,
never for a paste-only source. And the briefs: the richest first, filtered, hidden and back, and "I've
looked". And the accounts worth adding, hidden for 30 days."""

import shutil
import threading
from datetime import timedelta
from pathlib import Path

import pytest

from clipforge.briefs import run, scout, store
from tests.briefing import add_brief
from tests.fakes import Clock, fake_engine
from tests.posting import add_account, at
from tests.server import api_client

FIXTURE = Path(__file__).parent.parent / "fixtures" / "campaign_scout.csv"
SITE = {"name": "Clip Site", "list_url": "https://clips.example/campaigns", "terms_read": True}
SOURCE = {
    "id", "name", "marketplace", "list_url", "reader", "enabled", "terms_checked_on", "terms_note",
    "last_run_at", "last_result", "error", "created_at", "updated_at", "check_now_after", "reading", "paste",
}  # fmt: skip
BRIEF = {
    "id", "source_id", "external_id", "url", "marketplace", "brand", "title", "niche", "language",
    "currency", "rate_per_1000", "rates", "min_views", "max_payout", "caps", "budget_total", "budget_spent",
    "budget_left", "budget_read_at", "ends_on", "platforms", "footage", "text", "fields", "notes", "status",
    "campaign_id", "first_seen_at", "last_seen_at", "worth", "fits", "openable", "link_id", "reposting",
}  # fmt: skip


@pytest.fixture
def clock():
    return Clock(at(6, 42))


@pytest.fixture
def engine(clock):
    eng = fake_engine(now=clock)
    yield eng
    eng.close()


@pytest.fixture
def client(engine):
    return api_client(engine)  # no lifespan, so no timer reads


@pytest.fixture
def heard(engine):
    events = []
    engine.events.listen(lambda e: events.append((e.topic, e.data)))
    return events


@pytest.fixture
def scout_csv(no_real_scout):
    no_real_scout.parent.mkdir(parents=True)
    shutil.copy(FIXTURE, no_real_scout)
    return no_real_scout


def listed(client) -> dict[str, dict]:
    answer = client.get("/api/brief-sources")
    assert answer.status_code == 200
    return {s["id"]: s for s in answer.json()["sources"]}


def test_lists_the_sources(client):
    sources = listed(client)
    assert list(sources) == ["bs_scout", "bs_content_rewards", "bs_vyro"]
    for source in sources.values():
        assert set(source) == SOURCE
    assert (sources["bs_vyro"]["reader"], sources["bs_vyro"]["paste"]) == ("paste", True)
    assert (sources["bs_scout"]["paste"], sources["bs_scout"]["check_now_after"]) == (False, None)


def test_check_now_once_an_hour(client, engine, clock, scout_csv):
    answer = client.post("/api/brief-sources/bs_scout/check")
    assert answer.status_code == 202, answer.text
    assert set(answer.json()["source"]) == SOURCE
    assert run.idle(5)
    shown = listed(client)["bs_scout"]
    assert (shown["last_result"], shown["last_run_at"]) == ("ok", at(6, 42).timestamp())
    assert shown["check_now_after"] == at(7, 42).timestamp()
    clock.set(at(7, 41))
    again = client.post("/api/brief-sources/bs_scout/check")
    assert again.status_code == 429
    assert again.json() == {
        "error": "too_soon",
        "message": "Read this source again after 7:42 am.",
        "field": None,
    }
    clock.set(at(7, 42))
    assert client.post("/api/brief-sources/bs_scout/check").status_code == 202
    assert run.idle(5)


def test_check_now_one_at_a_time(client, engine, scout_csv, monkeypatch):
    started, release = threading.Event(), threading.Event()
    real = scout.read_scout

    def slow(path):
        started.set()
        assert release.wait(5)
        return real(path)

    monkeypatch.setattr(scout, "read_scout", slow)
    added = client.post("/api/brief-sources", json=SITE).json()["source"]
    try:
        assert client.post("/api/brief-sources/bs_scout/check").status_code == 202
        assert started.wait(5)
        assert listed(client)["bs_scout"]["reading"] is True
        busy = client.post(f"/api/brief-sources/{added['id']}/check")
        assert busy.status_code == 409
        assert busy.json()["message"] == "A read is already running."
    finally:
        release.set()
        assert run.idle(5)
    assert listed(client)["bs_scout"]["reading"] is False


def test_check_now_refuses_a_paste_source(client):
    answer = client.post("/api/brief-sources/bs_vyro/check")
    assert answer.status_code == 422
    assert answer.json()["error"] == "paste_only"
    assert answer.json()["message"].endswith(".")
    assert client.post("/api/brief-sources/bs_nothing/check").status_code == 404


def test_add_a_site_needs_the_terms_tick(client, engine):
    unticked = client.post("/api/brief-sources", json={k: v for k, v in SITE.items() if k != "terms_read"})
    assert unticked.status_code == 422
    assert unticked.json()["field"] == "terms_read"
    assert unticked.json()["message"].endswith(".")
    assert (
        client.post("/api/brief-sources", json=SITE | {"terms_read": False}).json()["field"] == "terms_read"
    )
    for link in ("ftp://clips.example/list", "clips.example/list", "https://", "http://localhost:8723/x",
                 "http://192.168.1.1/admin", "https://me:pw@clips.example/list"):  # fmt: skip
        refused = client.post("/api/brief-sources", json=SITE | {"list_url": link})
        assert refused.status_code == 422, link
        assert refused.json()["field"] == "list_url", link
    taken = client.post("/api/brief-sources", json=SITE | {"list_url": "https://www.vyro.com/new"})
    assert taken.status_code == 422
    assert taken.json()["error"] == "paste_only"  # Vyro's terms forbid reading it, so it is paste only
    nameless = client.post("/api/brief-sources", json=SITE | {"name": "   "})
    assert (nameless.status_code, nameless.json()["field"]) == (422, "name")

    added = client.post("/api/brief-sources", json=SITE | {"name": "  Clip Site "})
    assert added.status_code == 201, added.text
    source = added.json()["source"]
    assert (source["name"], source["marketplace"], source["reader"], source["enabled"]) == (
        "Clip Site",
        "Clip Site",
        "general",
        True,
    )
    assert (source["terms_checked_on"], source["terms_note"]) == (
        "2026-11-03",
        "You checked this site's terms.",
    )
    assert source["list_url"] == SITE["list_url"]
    assert list(listed(client))[-1] == source["id"]
    again = client.post("/api/brief-sources", json=SITE | {"list_url": "https://CLIPS.example/other"})
    assert again.json()["message"] == "Clip Site already reads that site."


def test_turn_a_source_on_and_off(client, engine):
    off = client.patch("/api/brief-sources/bs_scout", json={"enabled": False})
    assert off.status_code == 200
    assert off.json()["source"]["enabled"] is False
    assert store.get_source(engine.db(), "bs_scout").enabled is False
    assert client.patch("/api/brief-sources/bs_scout", json={"enabled": True}).json()["source"]["enabled"]
    assert client.patch("/api/brief-sources/bs_scout", json={"reader": "general"}).status_code == 422
    assert client.patch("/api/brief-sources/bs_nothing", json={"enabled": True}).status_code == 404


def shown(client, query: str = "") -> list[dict]:
    answer = client.get(f"/api/briefs{query}")
    assert answer.status_code == 200, answer.text
    return answer.json()["briefs"]


def test_briefs_route_sorted_and_filtered(client, engine):
    settings = engine.settings
    engine.settings = settings.model_copy(
        update={"today": settings.today.model_copy(update={"rates": {"AUD": 0.6}})}
    )
    gamer = add_account(engine, "tiktok", "gamer", niche_tags=["gaming"], daily_cap=2)
    add_brief(engine, title="Low", rate_per_1000=0.5, niche="Sports")
    add_brief(engine, title="Unknown", niche="Gaming")
    add_brief(engine, title="High", rate_per_1000=2.0, marketplace="Vyro", url="https://vyro.com/c/1")
    add_brief(engine, title="Aussie", rate_per_1000=2.5, currency="AUD")  # 2.50 x 0.6 is US$1.50
    add_brief(engine, title="Gaming", rate_per_1000=0.5, niche="Gaming")  # 0.50 twice a day for the gamer
    add_brief(engine, title="Hidden", rate_per_1000=9.0, status="hidden")
    add_brief(engine, title="Gone", rate_per_1000=9.0, status="gone")
    add_brief(
        engine,
        title="Bidi\u202e title\x07",
        brand="Brand\u202e",
        text="Line one\x07\nLine two",
        notes={"verdict": "Good\u202e"},
        rate_per_1000=0.1,
        url="https://clippingalpha.com/c/2",
    )

    briefs = shown(client)
    assert [b["title"] for b in briefs] == ["High", "Aussie", "Gaming", "Low", "Bidi title ", "Unknown"]
    for brief in briefs:
        assert set(brief) == BRIEF
    high, aussie, gaming = briefs[0], briefs[1], briefs[2]
    assert high["worth"] == {"amount": 2.0, "currency": "USD", "guess": True, "account_id": None}
    assert aussie["worth"]["currency"] == "AUD"
    assert gaming["worth"] == {"amount": 1.0, "currency": "USD", "guess": True, "account_id": gamer.id}
    assert gaming["fits"] == [{"id": gamer.id, "platform": "tiktok", "handle": "gamer", "label": ""}]
    assert (high["openable"], high["link_id"]) == (True, f"brief:{high['id']}")
    odd = briefs[4]
    assert (odd["brand"], odd["text"], odd["notes"]) == ("Brand", "Line one \nLine two", {"verdict": "Good"})
    assert (odd["openable"], odd["link_id"], odd["url"]) == (False, None, "https://clippingalpha.com/c/2")
    assert briefs[-1]["worth"]["amount"] is None  # no rate, so it goes last

    assert [b["title"] for b in shown(client, "?niche=Gaming")] == ["Gaming", "Unknown"]
    assert [b["title"] for b in shown(client, "?marketplace=Vyro")] == ["High"]
    assert [b["title"] for b in shown(client, "?status=hidden&status=gone")] == ["Hidden", "Gone"]
    gone = shown(client, "?status=gone")[0]
    assert gone["worth"]["amount"] == 0.0
    assert client.get("/api/briefs?status=nope").status_code == 422


def test_reposting_briefs_come_first(client, engine):
    """Reposting campaigns head the list, richest first among themselves, then the rest as before."""
    add_brief(engine, title="High", rate_per_1000=2.0)
    add_brief(engine, title="No raw", text="Edit the assets, no raw reposts.", rate_per_1000=1.0)
    add_brief(engine, title="Unknown", text="Repost from the edit repo.")
    add_brief(engine, title="Edit Reposts", rate_per_1000=0.5)
    add_brief(engine, title="Richer Reposts", rate_per_1000=0.8)
    briefs = shown(client)
    assert [b["title"] for b in briefs] == ["Richer Reposts", "Edit Reposts", "Unknown", "High", "No raw"]
    assert [b["reposting"] for b in briefs] == [True, True, True, False, False]


def test_hide_and_unhide(client, engine, heard):
    brief = add_brief(engine, title="Clip me", rate_per_1000=1.0)
    hidden = client.patch(f"/api/briefs/{brief.id}", json={"status": "hidden"})
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()["brief"]["status"] == "hidden"
    assert shown(client) == []
    assert ("brief", {"ids": [brief.id], "status": "hidden"}) in heard
    back = client.patch(f"/api/briefs/{brief.id}", json={"status": "seen"})
    assert back.json()["brief"]["status"] == "seen"
    assert [b["id"] for b in shown(client)] == [brief.id]

    assert client.patch(f"/api/briefs/{brief.id}", json={"status": "used"}).status_code == 422
    assert client.patch(f"/api/briefs/{brief.id}", json={"status": "seen", "title": "x"}).status_code == 422
    assert client.patch("/api/briefs/br_nothing", json={"status": "seen"}).status_code == 404
    for status in ("used", "gone"):
        fixed = add_brief(engine, status=status)
        refused = client.patch(f"/api/briefs/{fixed.id}", json={"status": "hidden"})
        assert refused.status_code == 409
        assert refused.json()["error"] == status
        assert refused.json()["message"].endswith(".")
        assert store.get_brief(engine.db(), fixed.id).status == status


def test_ive_looked_marks_seen(client, engine, heard):
    first, second = add_brief(engine, title="One"), add_brief(engine, title="Two")
    hidden = add_brief(engine, title="Three", status="hidden")
    looked = client.post(
        "/api/briefs/seen", json={"ids": [first.id, second.id, hidden.id, first.id, "br_nothing"]}
    )
    assert looked.status_code == 200, looked.text
    assert looked.json() == {"seen": [first.id, second.id]}
    assert [store.get_brief(engine.db(), b.id).status for b in (first, second, hidden)] == [
        "seen",
        "seen",
        "hidden",
    ]
    assert ("brief", {"ids": [first.id, second.id], "status": "seen"}) in heard
    again = client.post("/api/briefs/seen", json={"ids": [first.id]})
    assert again.json() == {"seen": []}
    assert client.post("/api/briefs/seen", json={"ids": "all"}).status_code == 422


def test_hide_route(engine, clock, client, heard):
    add_brief(engine, niche="Gaming", rate_per_1000=1.0)
    shown = client.get("/api/advice")
    assert shown.status_code == 200, shown.text
    assert [s["niche"] for s in shown.json()["advice"]["suggestions"]] == ["Gaming"]
    assert set(shown.json()["advice"]["suggestions"][0]) == {
        "niche", "language", "label", "count", "budget_left", "per_day", "as_if", "guess", "best", "make",
        "names", "bio", "picture", "channel", "plan",
    }  # fmt: skip
    hidden = client.post("/api/advice/hide", json={"kind": "suggestion", "niche": "Gaming", "language": "en"})
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()["advice"]["suggestions"] == []
    assert ("brief", {"advice": "hidden"}) in heard
    clock.set(at(6, 42) + timedelta(days=29))
    assert client.get("/api/advice").json()["advice"]["suggestions"] == []
    clock.set(at(6, 42) + timedelta(days=30))
    assert [s["niche"] for s in client.get("/api/advice").json()["advice"]["suggestions"]] == ["Gaming"]
    for body in (
        {"kind": "forever", "niche": "Gaming", "language": "en"},
        {"kind": "niche", "niche": "", "language": "en"},
        {"kind": "niche", "niche": "Gaming", "language": "English"},
        {"kind": "niche", "niche": "Gaming", "language": "en", "until": "2099-01-01"},
    ):
        assert client.post("/api/advice/hide", json=body).status_code == 422, body


def test_add_a_site_refuses_a_paste_only_marketplace(client):
    for link in (
        "https://whop.com/discover",
        "https://www.whop.com/discover",
        "https://app.vyro.com/campaigns",
        "https://vyro.com/other",
        "https://app.contentrewards.com/discover",
    ):
        refused = client.post("/api/brief-sources", json=SITE | {"list_url": link})
        assert refused.status_code == 422, link
        assert refused.json() == {
            "error": "paste_only",
            "message": "ClipForge doesn't read this marketplace's pages, so paste its briefs instead.",
            "field": "list_url",
        }, link
    assert (
        client.post("/api/brief-sources", json=SITE | {"list_url": "https://notwhop.com/c"}).status_code
        == 201
    )
