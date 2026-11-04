"""What a campaign is worth so far (SPEC 10.10, 14.11, 14.12): each post's estimate from its newest view
count, the campaign's counts from made to paid, and the totals per marketplace and currency, never mixed,
with Withdraw ready once the pending money reaches the marketplace's minimum payout."""

import time

import pytest

from clipforge import repo
from clipforge.campaigns import money, tracker
from clipforge.campaigns import store as campaigns
from clipforge.campaigns.models import Campaign
from clipforge.config import Marketplace
from clipforge.post import store
from tests.fakes import Clock, FakePublisher, fake_engine
from tests.posting import add_account, add_campaign, at, custom, linked_clip, posted

NOW = at(7, 10)  # Tue 3 Nov 2026
PATHS = {
    "to_submit": [],
    "submitted": ["submitted"],
    "approved": ["submitted", "approved"],
    "rejected": ["submitted", "rejected"],
    "paid": ["submitted", "approved", "paid"],
}


@pytest.fixture
def clock():
    return Clock(NOW)


@pytest.fixture
def engine(clock):
    eng = fake_engine(now=clock, publishers={"tiktok:browser": FakePublisher()})
    yield eng
    eng.close()


def campaign(**fields) -> Campaign:
    now = time.time()
    return Campaign(
        id=repo.new_id("cp"), name="Beast clips", marketplace="Whop", created_at=now, updated_at=now, **fields
    )


def submitted(
    engine, campaign: Campaign, views: int | None, status: str = "submitted", rank: int = 1, **paid
):
    """A posted post of ``campaign`` with ``views`` entered, its submission moved on to ``status``."""
    done = posted(engine, linked_clip(engine, campaign, rank), add_account(engine))
    sub = campaigns.submission_of_post(engine.db(), done.id)
    for step in PATHS[status]:
        tracker.move_submission(engine, sub.id, step, **(paid if step == "paid" else {}))
    if views is not None:
        tracker.add_views(engine, done.id, views)
    return done


def test_estimate_edges():
    assert money.estimate(campaign(), 10_000) is None  # no rate
    rated = campaign(rate_per_1000=1.5, min_views=10_000, max_payout=60.0)
    assert money.estimate(rated, None) is None
    assert money.estimate(rated, 9_999) == 0.0
    assert money.estimate(rated, 10_000) == 15.0
    assert money.estimate(rated, 33_333) == 50.0  # to the cent
    assert money.estimate(rated, 40_000) == 60.0
    assert money.estimate(rated, 5_000_000) == 60.0  # the cap
    assert money.estimate(campaign(rate_per_1000=2.0), 0) == 0.0


def test_newest_count_counts(engine, clock):
    beast = add_campaign(engine, rate_per_1000=2.0)
    done = submitted(engine, beast, 1000)
    clock.advance(days=2)
    tracker.add_views(engine, done.id, 800)  # a recount that came in lower
    got = money.campaign_money(engine, beast)
    assert (got["views"], got["estimated"], got["pending"], got["paid"]) == (800, 1.6, 0.0, 0.0)


def test_campaign_money_counts_and_sums(engine, clock):
    beast = add_campaign(engine, rate_per_1000=2.0, max_payout=30.0)
    empty = money.campaign_money(engine, beast)
    zero = {"made": 0, "approved": 0, "live": 0, "submitted": 0, "accepted": 0, "paid": 0}
    assert empty == {"counts": zero, "views": None, "estimated": 0.0, "pending": 0.0, "paid": 0.0}
    submitted(engine, beast, 1000, "to_submit", 1)  # live, not sent yet: no estimate
    submitted(engine, beast, 2000, "submitted", 2)  # 4.00
    submitted(engine, beast, 5000, "approved", 3)  # 10.00, pending
    submitted(engine, beast, 100_000, "approved", 4)  # 200.00, capped at 30.00
    submitted(engine, beast, 9000, "rejected", 5)
    submitted(engine, beast, 7000, "paid", 6, paid_amount=13.5)
    submitted(engine, beast, None, "submitted", 7)  # no count yet
    waiting = custom(engine, linked_clip(engine, beast, 8), add_account(engine), at(19))
    draft = custom(engine, linked_clip(engine, beast, 9), add_account(engine), at(20))
    store.update_post(engine.db(), draft.id, status="draft")
    gone = custom(engine, linked_clip(engine, beast, 10), add_account(engine), at(21))
    store.update_post(engine.db(), gone.id, status="discarded")
    off = custom(engine, linked_clip(engine, beast, 11), add_account(engine), at(19, 30))
    store.update_post(engine.db(), off.id, state={"off_campaign": True})
    got = money.campaign_money(engine, beast)
    counts = {"made": 10, "approved": 8, "live": 7, "submitted": 6, "accepted": 3, "paid": 1}
    assert got == {
        "counts": counts,
        "views": 124_000,
        "estimated": 44.0,
        "pending": 40.0,
        "paid": 13.5,
    }
    assert waiting.status == "scheduled"
    unrated = add_campaign(engine, name="Unrated")
    submitted(engine, unrated, 5000, "approved", 12)
    plain = money.campaign_money(engine, unrated)
    assert (plain["views"], plain["estimated"], plain["pending"]) == (5000, None, None)


def test_totals_are_per_currency(engine):
    usd = add_campaign(engine, rate_per_1000=1.0)
    aud = add_campaign(engine, name="Aussie clips", rate_per_1000=2.0, currency="AUD")
    other = add_campaign(engine, name="Streams", marketplace="Vyro", rate_per_1000=1.0)
    lower = add_campaign(engine, name="Same place", marketplace="whop", rate_per_1000=1.0)
    submitted(engine, usd, 1000, "approved", 1)
    submitted(engine, aud, 1000, "approved", 2)
    submitted(engine, other, 3000, "paid", 3, paid_amount=2.5)
    submitted(engine, lower, 4000, "submitted", 4)
    lines = [
        (t["marketplace"], t["currency"], t["estimated"], t["pending"], t["paid"])
        for t in money.totals(engine)
    ]
    assert lines == [
        ("Vyro", "USD", 0.0, 0.0, 2.5),
        ("Whop", "AUD", 2.0, 2.0, 0.0),
        ("Whop", "USD", 5.0, 1.0, 0.0),  # whop and Whop are one marketplace
    ]


def test_ready_to_withdraw(engine):
    whop = add_campaign(engine, rate_per_1000=1.0)
    vyro = add_campaign(engine, name="Streams", marketplace="Vyro", rate_per_1000=1.0)
    keep = {"whop": Marketplace(withdraw_url="https://whop.com/@me/wallet", min_payout=10.0)}
    engine.settings = engine.settings.model_copy(update={"marketplaces": keep})

    def ready():
        return {t["marketplace"]: (t["ready"], t["min_payout"], t["withdraw"]) for t in money.totals(engine)}

    submitted(engine, vyro, 5000, "submitted", 1)
    submitted(engine, whop, 8000, "approved", 2)
    assert ready() == {"Vyro": (False, None, False), "Whop": (False, 10.0, True)}  # nothing pending on Vyro
    submitted(engine, whop, 2000, "approved", 3)
    submitted(engine, vyro, 10, "approved", 4)
    assert ready() == {
        "Vyro": (True, None, False),
        "Whop": (True, 10.0, True),
    }  # any pending, with no minimum
