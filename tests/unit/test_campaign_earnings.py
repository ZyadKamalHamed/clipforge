"""The Money page's data (SPEC 14.12, phase 5e): one row per campaign post with its posting day, account,
campaign, hook, views at 2 and 7 days and now, status and what it should earn or was paid, each in its own
currency with the rate Settings gives it, and the accounts to filter by. Every sum the page shows is worked
from these rows, so all-time totals must equal the sheet's."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

import pytest

from clipforge.campaigns import earnings, money
from clipforge.post import store as post_store
from clipforge.untrusted import clean_text
from tests.fakes import Clock, fake_engine
from tests.posting import DAY, SYD, add_account, add_campaign, at, campaign_post


@pytest.fixture
def engine():
    eng = fake_engine(now=Clock(at(7, 10)))  # 7:10 am on Tue 3 Nov 2026 in Sydney
    yield eng
    eng.close()


def with_rates(engine, rates: dict[str, float]) -> None:
    settings = engine.settings
    engine.settings = settings.model_copy(
        update={"today": settings.today.model_copy(update={"rates": rates})}
    )


def by_title(engine) -> dict[str, dict]:
    return {row["hook"]: row for row in earnings.money_posts(engine)}


def test_views_at_two_and_seven_days_go_by_the_posting_days(engine):
    """The 2-day and 7-day figures are the counts the tracker asks for, on the posting time zone's days: a
    post at 21:00 counted at 08:00 two days on and a week on keeps both; a count from day 30 is neither."""
    beast = add_campaign(engine, rate_per_1000=2.0)
    t1 = add_account(engine, "tiktok", "t1")
    went_up = datetime(2026, 10, 25, 21, 0, tzinfo=SYD)  # a Sunday; now is 07:10 on Tue 3 Nov
    ago = (at(7, 10) - went_up).total_seconds() / DAY
    counts = ((35 / 24, 5000), (155 / 24, 9000))  # 08:00 on Tue 27 Oct and on Sun 1 Nov
    campaign_post(engine, beast, t1, days_ago=ago, counts=counts, title="Late")
    campaign_post(engine, beast, t1, days_ago=31, counts=[(30, 7000)], title="Counted at the end")
    campaign_post(engine, beast, t1, days_ago=8, counts=[(3, 800), (7, 900)], title="Late first count")
    rows = by_title(engine)
    assert (rows["Late"]["views_2d"], rows["Late"]["views_7d"]) == (5000, 9000)
    end = rows["Counted at the end"]
    assert (end["views_2d"], end["views_7d"], end["views"]) == (None, None, 7000)
    assert (rows["Late first count"]["views_2d"], rows["Late first count"]["views_7d"]) == (800, 900)


def test_money_posts_carry_their_numbers(engine):
    beast = add_campaign(engine, rate_per_1000=2.0, min_views=1000, max_payout=50.0)
    t1 = add_account(engine, "tiktok", "t1", niche_tags=["gaming", "streamers"])
    counts = ((1, 900), (2, 4000), (7, 12000))
    campaign_post(engine, beast, t1, days_ago=9, counts=counts, title="Sent")
    campaign_post(engine, beast, t1, days_ago=8, counts=counts, status="approved", title="Approved")
    campaign_post(engine, beast, t1, days_ago=7.5, counts=counts, status="paid", paid=30.0, title="Paid")
    campaign_post(
        engine,
        beast,
        t1,
        days_ago=7,
        counts=counts,
        status="rejected",
        reason="Wrong format\u202e",
        title="No",
    )
    rows = by_title(engine)

    sent = rows["Sent"]
    assert (sent["views_2d"], sent["views_7d"], sent["views"]) == (4000, 12000, 12000)
    assert (sent["estimated"], sent["pending"], sent["paid"]) == (24.0, None, None)
    assert (sent["handle"], sent["platform"], sent["niche"]) == ("@t1", "tiktok", "gaming")
    assert (sent["campaign_id"], sent["campaign"], sent["marketplace"]) == (beast.id, "Beast clips", "Whop")
    assert (sent["status"], sent["currency"], sent["account_id"]) == ("submitted", "USD", t1.id)
    assert rows["Approved"]["estimated"] == rows["Approved"]["pending"] == 24.0
    assert (rows["Paid"]["estimated"], rows["Paid"]["pending"], rows["Paid"]["paid"]) == (None, None, 30.0)
    no = rows["No"]
    assert (no["estimated"], no["pending"], no["paid"]) == (None, None, None)
    assert no["reason"] == "Wrong format"
    assert [row["hook"] for row in earnings.money_posts(engine)] == ["No", "Paid", "Approved", "Sent"]


def test_a_post_without_counts_has_no_views_or_estimate(engine):
    beast = add_campaign(engine, rate_per_1000=2.0)
    campaign_post(engine, beast, add_account(engine, "tiktok", "t1"), days_ago=1)
    (row,) = earnings.money_posts(engine)
    assert (row["views_2d"], row["views_7d"], row["views"], row["estimated"]) == (None, None, None, None)


def test_posted_on_is_the_posting_zones_day(engine):
    assert engine.settings.posting.timezone == "Australia/Sydney"
    beast = add_campaign(engine, rate_per_1000=2.0)
    post = campaign_post(engine, beast, add_account(engine, "tiktok", "t1"), days_ago=1)
    late = datetime(2026, 11, 2, 13, 30, tzinfo=UTC).timestamp()  # 12:30 am on 3 Nov in Sydney
    post_store.update_post(engine.db(), post.id, posted_at=late)
    (row,) = earnings.money_posts(engine)
    assert row["posted_on"] == "2026-11-03"


def test_rate_and_as_if_per_post(engine):
    with_rates(engine, {"AUD": 0.66})
    t1 = add_account(engine, "tiktok", "t1")
    for currency in ("USD", "AUD", "EUR"):
        campaign = add_campaign(engine, name=currency, currency=currency, rate_per_1000=1.0)
        campaign_post(engine, campaign, t1, days_ago=1, title=currency)
    rows = by_title(engine)
    assert (rows["USD"]["rate"], rows["USD"]["as_if"]) == (1.0, False)
    assert (rows["AUD"]["rate"], rows["AUD"]["as_if"]) == (0.66, False)
    assert (rows["EUR"]["rate"], rows["EUR"]["as_if"]) == (1.0, True)
    assert earnings.money_view(engine)["as_if"] == ["EUR"]


def test_all_time_sums_match_the_sheet(engine):
    t1 = add_account(engine, "tiktok", "t1")
    i1 = add_account(engine, "instagram", "i1")
    made = [
        add_campaign(engine, name="A", currency="USD", rate_per_1000=1.5, max_payout=10.0),
        add_campaign(engine, name="B", marketplace="Vyro", currency="USD", rate_per_1000=0.8),
        add_campaign(engine, name="C", marketplace="Vyro", currency="AUD", rate_per_1000=2.0, min_views=500),
        add_campaign(engine, name="D", currency="AUD"),  # no rate: nothing estimated
    ]
    statuses = ("to_submit", "submitted", "approved", "rejected", "paid")
    for n, campaign in enumerate(made):
        for k, status in enumerate(statuses):
            paid = 4.25 + n + k if status == "paid" else None
            counts = ((1, 300 * (n + 1) * (k + 1)), (8, 2100 * (n + 1) + k))
            campaign_post(
                engine, campaign, (t1, i1)[k % 2], days_ago=9, counts=counts, status=status, paid=paid
            )
    sums: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in earnings.money_posts(engine):
        for field in ("estimated", "pending", "paid"):
            sums[row["currency"]][field] += row[field] or 0.0
    sheet: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for line in money.totals(engine):
        for field in ("estimated", "pending", "paid"):
            sheet[line["currency"]][field] += line[field] or 0.0
    assert set(sums) == set(sheet) == {"USD", "AUD"}
    for currency in sheet:
        for field in ("estimated", "pending", "paid"):
            assert round(sums[currency][field], 2) == round(sheet[currency][field], 2), (currency, field)


def test_deleted_account_keeps_its_handle(engine):
    beast = add_campaign(engine, rate_per_1000=2.0)
    gone = add_account(engine, "tiktok", "gone")
    kept = add_account(engine, "instagram", "kept")
    add_account(engine, "tiktok", "idle")
    campaign_post(engine, beast, gone, days_ago=3)
    post_store.delete_account(engine.db(), gone.id)
    view = earnings.money_view(engine)
    assert [row["handle"] for row in view["posts"]] == ["@gone"]
    assert [(a["platform"], a["handle"]) for a in view["accounts"]] == [
        ("instagram", "@kept"),
        ("tiktok", "@gone"),
        ("tiktok", "@idle"),
    ]
    assert view["today"] == "2026-11-03"
    assert {a["id"] for a in view["accounts"]} >= {gone.id, kept.id}


def test_hook_and_names_are_cleaned(engine):
    beast = add_campaign(engine, name="Beast\u202e clips", marketplace="Wh\u0007op", rate_per_1000=2.0)
    title = "Wait for\u202e it\u0007"
    campaign_post(engine, beast, add_account(engine, "tiktok", "t1"), days_ago=1, title=title)
    (row,) = earnings.money_posts(engine)
    assert row["hook"] == clean_text(title)
    assert (row["campaign"], row["marketplace"]) == (clean_text(beast.name), clean_text(beast.marketplace))
    for text in (row["hook"], row["campaign"], row["marketplace"]):
        assert "\u202e" not in text and "\u0007" not in text
