"""What a post could earn (SPEC 10.11): the share of an account's last 30 counted posts that reached the
campaign's minimum, times the median 7-day views of those that did, over 1,000, times the rate, capped at
the most per clip and the budget left; a starting guess for an account with fewer than 5 counted posts; and
amounts compared across currencies only through the rates you set."""

import pytest

from clipforge.config import Settings
from clipforge.today.models import Estimate
from clipforge.today.value import comparable, counted, live_estimate, post_estimate, seven_day_views
from tests.fakes import Clock, fake_engine
from tests.posting import add_account, add_campaign, at, history, posted_post


@pytest.fixture
def engine():
    eng = fake_engine(now=Clock(at(9, 0)))
    yield eng
    eng.close()


def test_estimate_from_history(engine):
    account = add_account(engine, "tiktok", "mine")
    history(engine, account, [500, 3000, 4000, 4000, 800, 5000, 1000, 9000])  # 6 reach 1,000; median 4,000
    campaign = add_campaign(engine, rate_per_1000=1.5, min_views=1000)
    assert post_estimate(engine, account.id, campaign) == Estimate(4.5, "USD", False)  # 0.75 * 4 * 1.5
    nobody = add_campaign(engine, rate_per_1000=1.5, min_views=50_000)
    assert post_estimate(engine, account.id, nobody) == Estimate(0.0, "USD", False)
    no_minimum = add_campaign(engine, rate_per_1000=2.0, currency="AUD")
    assert post_estimate(engine, account.id, no_minimum) == Estimate(7.0, "AUD", False)  # all 8; median 3,500


def test_estimate_caps(engine):
    account = add_account(engine, "tiktok", "mine")
    history(engine, account, [500, 3000, 4000, 4000, 800, 5000, 1000, 9000])
    capped = add_campaign(engine, rate_per_1000=1.5, min_views=1000, max_payout=3.0)
    assert post_estimate(engine, account.id, capped).amount == 3.0
    budget = add_campaign(engine, rate_per_1000=1.5, min_views=1000, max_payout=3.0, budget_left=2.0)
    assert post_estimate(engine, account.id, budget).amount == 2.0


def test_estimate_guess_under_five(engine):
    account = add_account(engine, "tiktok", "mine")
    history(engine, account, [40_000, 40_000, 40_000, 40_000])  # 4 counted: too few to go on
    campaign = add_campaign(engine, rate_per_1000=2.0)
    assert post_estimate(engine, account.id, campaign) == Estimate(2.0, "USD", True)  # 1,000 views
    high = add_campaign(engine, rate_per_1000=2.0, min_views=5000)
    assert post_estimate(engine, account.id, high) == Estimate(0.0, "USD", True)
    engine.update_settings(
        engine.settings.model_copy(
            update={"today": engine.settings.today.model_copy(update={"starting_views": 6000})}
        )
    )
    assert post_estimate(engine, account.id, high) == Estimate(12.0, "USD", True)


def test_no_rate_no_estimate(engine):
    account = add_account(engine, "tiktok", "mine")
    history(engine, account, [4000] * 6)
    campaign = add_campaign(engine, currency="EUR")
    assert post_estimate(engine, account.id, campaign) == Estimate(None, "EUR", False)
    post = posted_post(engine, account, days_ago=1, counts=[(1, 900)])
    assert live_estimate(engine, post, campaign).amount is None


def test_seven_day_count_is_the_first_after_a_week(engine):
    account = add_account(engine, "tiktok", "mine")
    post = posted_post(engine, account, days_ago=10, counts=[(2, 300), (7, 2000), (9, 2600)])
    assert seven_day_views(engine, post) == 2000
    early = posted_post(engine, account, days_ago=10, counts=[(2, 300), (6.6, 1800), (9, 2600)])
    assert seven_day_views(engine, early) == 2600  # 6.6 days on is still day 6 on the posting zone's clock
    # up at 21:00 on Sat 24 Oct, counted at 08:00 on Sat 31 Oct: 155 hours on, but the day the tracker asks
    evening = posted_post(engine, account, days_ago=9.5, counts=[(155 / 24, 1700)])
    assert seven_day_views(engine, evening) == 1700
    stale = posted_post(engine, account, days_ago=20, counts=[(15, 5000)])
    assert seven_day_views(engine, stale) is None  # a count from day 15 on is not a week's
    young = posted_post(engine, account, days_ago=3, counts=[(2, 300)])
    assert seven_day_views(engine, young) is None


def test_counted_takes_the_last_30_with_a_count(engine):
    account = add_account(engine, "tiktok", "mine")
    other = add_account(engine, "tiktok", "two")
    history(engine, account, list(range(1000, 1035)))  # 35 counted posts, newest first
    posted_post(engine, account, days_ago=2, counts=[(1, 99)])  # too young for a 7-day count
    history(engine, other, [7])
    assert counted(engine, account.id) == list(range(1000, 1030))
    assert counted(engine, other.id) == [7]


def test_live_estimate_uses_the_newest_count(engine):
    account = add_account(engine, "tiktok", "mine")
    history(engine, account, [4000] * 6)
    campaign = add_campaign(engine, rate_per_1000=1.0, min_views=1000, max_payout=5.0)
    counted_post = posted_post(engine, account, days_ago=3, counts=[(1, 800), (2, 2500)])
    assert live_estimate(engine, counted_post, campaign) == Estimate(2.5, "USD", False)
    big = posted_post(engine, account, days_ago=3, counts=[(2, 90_000)])
    assert live_estimate(engine, big, campaign).amount == 5.0
    fresh = posted_post(engine, account, days_ago=0.1)
    assert live_estimate(engine, fresh, campaign) == post_estimate(engine, account.id, campaign)


def test_amounts_compare_through_rates_only():
    """Review Focus 3: no rate means "as if US$", never a made-up conversion."""
    s = Settings()
    assert comparable(s, 10.0, "AUD") == (10.0, True)
    rated = s.model_copy(update={"today": s.today.model_copy(update={"rates": {"AUD": 0.65}})})
    assert comparable(rated, 10.0, "AUD") == (6.5, False)
    assert comparable(rated, 4.0, "USD") == (4.0, False)
    assert comparable(rated, 3.0, "EUR") == (3.0, True)
