"""Use this campaign (SPEC 10.11, 16): a brief fills the new campaign form as pasted rules do, its own
figures over what Claude read and every field it set marked to check, and its page opens in your default
browser when it is on a marketplace ClipForge reads. Nothing is saved until you press Save."""

import pytest

from clipforge.briefs import store as briefs
from clipforge.briefs.models import TEXT_MAX, Footage
from clipforge.briefs.use import use
from clipforge.campaigns import parse
from clipforge.campaigns import store as campaigns
from tests.briefing import add_brief
from tests.fakes import Clock, FakeProvider, fake_engine
from tests.posting import at
from tests.server import api_client

RULES = "Earn $9 per 1,000 views. Tag #MrBeast. Clip only MrBeast's channel."
CHANNEL = "https://www.youtube.com/@MrBeast"
VIDEO = "https://www.youtube.com/watch?v=arj7oStGLkU"


def answer(req):
    """Claude's reading of RULES: a name, a rate and a hashtag, every other field null."""
    given = {"name": "Claude's name", "rate_per_1000": 9.0, "hashtags": ["#MrBeast"]}
    return {name: None for name in parse.RulesAnswer.model_fields} | given


@pytest.fixture
def provider():
    return FakeProvider({"campaign": answer})


@pytest.fixture
def engine(provider):
    eng = fake_engine(now=Clock(at(9, 0)), provider=provider)
    yield eng
    eng.close()


def opened(engine) -> list[list[str]]:
    return [c for c in engine.run_cmd.calls if c[:1] == ["open"]]


def scout_brief(engine, **fields):
    """A Campaign Scout brief on Content Rewards with every figure it can have."""
    return add_brief(
        engine,
        **{
            "brand": "MrBeast",
            "title": "Clip the new video",
            "marketplace": "Content Rewards",
            "rate_per_1000": 1.5,
            "min_views": 10_000,
            "max_payout": 150.0,
            "budget_left": 812.0,
            "budget_read_at": at(6, 5, day=2).timestamp(),
            "ends_on": "2026-11-30",
            "platforms": ["tiktok"],
            "footage": Footage(links=[CHANNEL, VIDEO]),
            "text": RULES,
        }
        | fields,
    )


def test_use_fills_from_the_brief(engine, provider):
    brief = scout_brief(engine)
    got = use(engine, brief.id)
    assert (got.brief_id, got.opened, got.sentence) == (brief.id, True, None)
    f = got.fields
    assert (f["name"], f["marketplace"], f["campaign_url"]) == (
        "MrBeast: Clip the new video",
        "Content Rewards",
        brief.url,
    )
    assert (f["currency"], f["rate_per_1000"], f["min_views"], f["max_payout"]) == ("USD", 1.5, 10_000, 150.0)
    assert (f["budget_left"], f["ends_on"], f["platforms"]) == (812.0, "2026-11-30", ["tiktok"])
    assert f["footage_links"] == [f"{CHANNEL}/videos"]
    assert f["footage"]["videos"] == ["arj7oStGLkU"]
    assert f["required"]["hashtags"] == ["MrBeast"]  # Claude's reading stays where the brief says nothing
    assert f["rules_text"] == RULES
    filled = {"name", "marketplace", "campaign_url", "currency", "rate_per_1000", "min_views", "max_payout"}
    filled |= {"budget_left", "ends_on", "platforms", "footage_links", "footage.videos", "required.hashtags"}
    assert set(got.filled) == filled
    assert len(provider.requests) == 1
    assert opened(engine) == [["open", brief.url]]
    assert campaigns.list_campaigns(engine.db()) == []  # nothing saved
    assert briefs.get_brief(engine.db(), brief.id).status == "new"


def test_use_takes_the_lower_rate(engine):
    brief = scout_brief(
        engine,
        brand=None,
        rate_per_1000=None,
        rates={"tiktok": 0.5, "instagram": 1.5},
        max_payout=None,
        caps={"tiktok": 200.0, "instagram": 100.0},
        platforms=["tiktok", "instagram"],
    )
    f = use(engine, brief.id).fields
    assert f["name"] == "Clip the new video"  # no brand, the title alone
    assert (f["rate_per_1000"], f["max_payout"]) == (0.5, 100.0)
    assert "Rates: TikTok US$0.50, Instagram US$1.50 per 1,000 views" in f["notes"]
    assert "Most paid per clip: TikTok US$200.00, Instagram US$100.00" in f["notes"]


def test_use_without_text_skips_claude(engine, provider):
    brief = scout_brief(engine, text="", brand="B" * 60, title="T" * 60)
    got = use(engine, brief.id)
    assert provider.requests == []
    assert got.fields["rules_text"] == ""
    assert got.fields["name"] == ("B" * 60 + ": " + "T" * 60)[:80]
    assert "required.hashtags" not in got.filled


def test_use_with_only_marks_in_its_text_skips_claude(engine, provider):
    """Text that is nothing once cleaned is no text: no sentence meant for an empty paste."""
    got = use(engine, scout_brief(engine, text="\u202e\u2066 \u2069").id)
    assert provider.requests == []
    assert (got.sentence, got.fields["rules_text"]) == (None, "")


def test_a_briefs_text_always_fits_the_rules_limit():
    """So Claude is always asked: use never meets the "too long to read" refusal meant for a paste."""
    assert TEXT_MAX <= parse.MAX_RULES_CHARS


def test_use_does_not_open_an_unlisted_host(engine):
    brief = scout_brief(engine, url="https://notwhop.com/c/1")
    got = use(engine, brief.id)
    assert got.opened is False
    assert opened(engine) == []
    assert got.fields["campaign_url"] is None
    assert "campaign_url" not in got.filled


def test_use_route_saves_nothing(engine):
    client = api_client(engine)
    brief = scout_brief(engine)
    r = client.post(f"/api/briefs/{brief.id}/use")
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["brief_id"], body["opened"], body["fields"]["name"]) == (
        brief.id,
        True,
        "MrBeast: Clip the new video",
    )
    assert client.post("/api/briefs/br_nope/use").status_code == 404
    briefs.update_brief(engine.db(), brief.id, status="gone")
    gone = client.post(f"/api/briefs/{brief.id}/use")
    assert (gone.status_code, gone.json()["error"]) == (409, "gone")
    assert campaigns.list_campaigns(engine.db()) == []


def test_saving_dates_the_budget_by_its_read(engine):
    """Saved from the brief, the budget left is dated the day it was read, not the day you saved it."""
    client = api_client(engine)
    brief = scout_brief(engine)
    fields = use(engine, brief.id).fields | {"footage_links": []}  # no channel to look up
    r = client.post("/api/campaigns", json=fields | {"brief_id": brief.id})
    assert r.status_code == 201, r.text
    assert r.json()["campaign"]["budget_entered_on"] == "2026-11-02"
    other = scout_brief(engine)
    changed = use(engine, other.id).fields | {"footage_links": [], "budget_left": 500.0, "brief_id": other.id}
    assert client.post("/api/campaigns", json=changed).json()["campaign"]["budget_entered_on"] == "2026-11-03"


def links_answer(req):
    """Claude's reading of a stranger's text that names its own links."""
    given = {
        "name": "Claude's name",
        "campaign_url": "https://evil.example/join",
        "submit_url": "https://whop.com/submit/clips",
    }
    return {name: None for name in parse.RulesAnswer.model_fields} | given


def test_use_keeps_only_links_on_a_marketplace_clipforge_reads():
    eng = fake_engine(now=Clock(at(9, 0)), provider=FakeProvider({"campaign": links_answer}))
    try:
        brief = scout_brief(
            eng, url="https://clippingalpha.com/c/1", text="Join at https://evil.example/join"
        )
        got = use(eng, brief.id)
        assert got.opened is False
        assert got.fields["campaign_url"] is None and "campaign_url" not in got.filled
        assert got.fields["submit_url"] == "https://whop.com/submit/clips" and "submit_url" in got.filled
        assert opened(eng) == []
    finally:
        eng.close()


def test_deleting_its_campaign_frees_the_brief(engine):
    client = api_client(engine)
    heard = []
    engine.events.listen(lambda e: heard.append((e.topic, e.data)))
    brief = scout_brief(engine)
    fields = use(engine, brief.id).fields | {"footage_links": []}
    made = client.post("/api/campaigns", json=fields | {"brief_id": brief.id})
    assert made.status_code == 201, made.text
    campaign_id = made.json()["campaign"]["id"]
    assert briefs.get_brief(engine.db(), brief.id).status == "used"
    assert client.delete(f"/api/campaigns/{campaign_id}").status_code == 204
    freed = briefs.get_brief(engine.db(), brief.id)
    assert (freed.status, freed.campaign_id) == ("seen", None)
    assert ("brief", {"ids": [brief.id], "status": "seen"}) in heard
    assert client.post(f"/api/briefs/{brief.id}/use").status_code == 200
