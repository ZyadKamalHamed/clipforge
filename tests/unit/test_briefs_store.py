import time

import pytest

from clipforge import repo
from clipforge.briefs import store
from clipforge.briefs.models import Brief, Footage
from clipforge.campaigns import service
from clipforge.campaigns import store as campaign_store
from clipforge.campaigns.models import Campaign
from clipforge.db import connect, migrate


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    migrate(c)
    yield c
    c.close()


def brief(**kw) -> Brief:
    now = time.time()
    return Brief(
        id=kw.pop("id", repo.new_id("br")),
        source_id=kw.pop("source_id", "bs_scout"),
        external_id=kw.pop("external_id", "https://contentrewards.com/c/1"),
        url=kw.pop("url", "https://contentrewards.com/c/1"),
        marketplace=kw.pop("marketplace", "Whop"),
        title=kw.pop("title", "Podcast clips"),
        first_seen_at=now,
        last_seen_at=now,
        **kw,
    )


def test_seeded_sources(conn):
    rows = store.sources(conn)
    assert [(s.id, s.name, s.marketplace, s.list_url, s.reader, s.enabled) for s in rows] == [
        ("bs_scout", "Campaign Scout (HeyClicky)", "Whop and Vyro", None, "campaign_scout", True),
        (
            "bs_content_rewards",
            "Whop Content Rewards",
            "Whop",
            "https://contentrewards.com/discover",
            "paste",
            True,
        ),
        ("bs_vyro", "Vyro", "Vyro", "https://vyro.com/campaigns", "paste", True),
    ]
    scout, whop, vyro = rows
    assert (
        scout.terms_note
        == "Reads HeyClicky's Campaign Scout file on this Mac. It makes no request to any site."
    )
    assert whop.terms_checked_on == vyro.terms_checked_on == "2026-10-29"
    assert whop.terms_note == (
        "Whop's terms forbid automated reading, so paste its briefs. "
        "HeyClicky's Campaign Scout file already lists its campaigns."
    )
    assert vyro.terms_note == "Vyro's terms forbid automated reading, so paste its briefs."
    assert all(s.last_run_at is None and s.last_result is None for s in rows)


def test_update_source(conn):
    store.update_source(conn, "bs_vyro", enabled=False, last_result="blocked", error="It said no.")
    vyro = store.get_source(conn, "bs_vyro")
    assert (vyro.enabled, vyro.last_result, vyro.error) == (False, "blocked", "It said no.")
    assert vyro.updated_at >= vyro.created_at
    with pytest.raises(ValueError):
        store.update_source(conn, "bs_vyro", id="other")


def test_brief_round_trips(conn):
    b = brief(
        brand="Acme",
        niche="Gaming",
        language="en",
        rate_per_1000=1.5,
        rates={"tiktok": 0.5, "instagram": 1.5},
        min_views=10_000,
        max_payout=150.0,
        caps={"tiktok": 150.0, "instagram": 100.0},
        budget_total=1000.0,
        budget_spent=187.5,
        budget_left=812.5,
        budget_read_at=1_761_000_000.0,
        ends_on="2026-11-30",
        platforms=["instagram"],
        footage=Footage(kind="Brand's own videos", links=["https://www.youtube.com/@acme"]),
        text="Clip the best moments.",
        fields={"rate_per_1000": "stated", "niche": "claude", "language": "guess"},
        notes={"verdict": "Maybe"},
        text_hash="abc",
    )
    store.put_brief(conn, b)
    assert store.get_brief(conn, b.id) == b
    assert store.briefs(conn) == [b]


def test_one_brief_per_link(conn):
    first = brief()
    store.put_brief(conn, first)
    again = brief(source_id="bs_vyro", external_id="vyro-1", title="New title")
    kept = store.put_brief(conn, again)
    assert kept.id == first.id
    assert [(b.id, b.title, b.source_id) for b in store.briefs(conn)] == [(first.id, "New title", "bs_vyro")]
    assert store.brief_by_key(conn, first.url, "bs_scout", "nothing").id == first.id


def test_brief_without_a_link_is_keyed_by_source(conn):
    a = store.put_brief(conn, brief(url=None, external_id="row-1"))
    b = store.put_brief(conn, brief(url=None, external_id="row-2"))
    assert a.id != b.id and len(store.briefs(conn)) == 2
    assert store.brief_by_key(conn, None, "bs_scout", "row-2").id == b.id
    assert store.brief_by_key(conn, None, "bs_vyro", "row-2") is None


def test_briefs_filters(conn):
    store.put_brief(conn, brief(niche="Gaming"))
    store.put_brief(
        conn, brief(url="https://vyro.com/c/2", external_id="v2", marketplace="Vyro", status="seen")
    )
    store.put_brief(conn, brief(url="https://vyro.com/c/3", external_id="v3", status="gone"))
    assert [b.external_id for b in store.briefs(conn, status=["new", "seen"])] == [
        "https://contentrewards.com/c/1",
        "v2",
    ]
    assert [b.external_id for b in store.briefs(conn, niche="Gaming")] == ["https://contentrewards.com/c/1"]
    assert [b.external_id for b in store.briefs(conn, marketplace="Vyro")] == ["v2"]
    assert store.briefs(conn, status=[]) == []
    store.update_brief(conn, store.briefs(conn, marketplace="Vyro")[0].id, status="hidden")
    assert store.briefs(conn, marketplace="Vyro")[0].status == "hidden"


def test_hidden_advice_lasts_until_its_day(conn):
    store.hide_advice(conn, "niche", "Gaming", "en", until="2026-11-28")
    assert store.hidden_advice(conn, "2026-11-27") == {("niche", "Gaming", "en")}
    assert store.hidden_advice(conn, "2026-11-28") == set()


def test_site_reads_are_per_day(conn):
    assert store.site_read(conn, "vyro.com", "2026-10-29") == (0, None)
    store.count_read(conn, "vyro.com", "2026-10-29", page=False, at=10.0)
    store.count_read(conn, "vyro.com", "2026-10-29", page=True, at=15.0)
    store.count_read(conn, "vyro.com", "2026-10-29", page=True, at=20.0)
    assert store.site_read(conn, "vyro.com", "2026-10-29") == (2, 20.0)
    assert store.site_read(conn, "vyro.com", "2026-10-30") == (0, None)
    assert store.site_read(conn, "example.com", "2026-10-29") == (0, None)


def test_campaign_keeps_its_brief(conn):
    now = time.time()
    c = Campaign(
        id="cp1",
        name="Acme",
        marketplace="Whop",
        brief_id="br1",
        joined_at=12.0,
        created_at=now,
        updated_at=now,
    )
    campaign_store.add_campaign(conn, c)
    got = campaign_store.get_campaign(conn, "cp1")
    assert (got.brief_id, got.joined_at) == ("br1", 12.0)
    assert campaign_store.update_campaign(conn, "cp1", joined_at=20.0).joined_at == 20.0
    assert not {"brief_id", "joined_at"} & set(service.FORM)
