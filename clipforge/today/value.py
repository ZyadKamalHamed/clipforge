"""What a post could earn (SPEC 10.11). With 5 or more of an account's last 30 posts counted at 7 days, the
amount is the share that reached the campaign's minimum, times the median views of those that did, over
1,000, times the rate, capped at the most per clip and at the budget left. With fewer, it is the starting
guess from Settings, marked as a guess. Amounts in other currencies are compared through the exchange rates
you set, and without one they are compared as if they were US$, never through a made-up rate."""

from __future__ import annotations

from statistics import median

from clipforge.campaigns import money, tracker
from clipforge.campaigns import store as campaign_store
from clipforge.campaigns.models import Campaign
from clipforge.config import Settings
from clipforge.post import store as post_store
from clipforge.post.models import Post
from clipforge.today.models import Estimate

# The spans, in the posting time zone's days after a post went up, whose first count is its 2-day and its
# 7-day count: the counts the tracker asks for on those days, entered on the day or a little late.
TWO_DAYS = (2, 7)
SEVEN_DAYS = (7, 14)
COUNTED_POSTS = 30
FEW = 5


def seven_day_views(engine, post: Post) -> int | None:
    """The post's 7-day count, the first on or after its seventh day and before its fourteenth, or None."""
    if post.posted_at is None:
        return None
    return tracker.views_on(
        engine, campaign_store.metrics_of(engine.db(), post.id), post.posted_at, *SEVEN_DAYS
    )


def counted(engine, account_id: str) -> list[int]:
    """The 7-day views of the account's last 30 posts that have them, newest first."""
    posts = post_store.list_posts(engine.db(), status=["posted"], account_id=account_id)
    views: list[int] = []
    for post in sorted(posts, key=lambda p: p.posted_at or 0.0, reverse=True):
        if (n := seven_day_views(engine, post)) is not None:
            views.append(n)
            if len(views) == COUNTED_POSTS:
                break
    return views


def post_estimate(engine, account_id: str | None, campaign: Campaign) -> Estimate:
    """What a new post on the account should earn on the campaign, from its history or the starting guess;
    with no account (None), the starting guess."""
    if campaign.rate_per_1000 is None:
        return Estimate(None, campaign.currency, False)
    history = counted(engine, account_id) if account_id is not None else []
    least = campaign.min_views or 0
    if len(history) >= FEW:
        reached = [n for n in history if n >= least]
        share, views, guess = len(reached) / len(history), (median(reached) if reached else 0), False
    else:
        views = engine.settings.today.starting_views
        share, guess = (1.0 if views >= least else 0.0), True
    amount = share * views / 1000 * campaign.rate_per_1000
    for cap in (campaign.max_payout, campaign.budget_left):
        if cap is not None:
            amount = min(amount, cap)
    return Estimate(round(amount, 2), campaign.currency, guess)


def live_estimate(engine, post: Post, campaign: Campaign) -> Estimate:
    """What a post that went up should earn: from its newest count once it has one, else as a new post."""
    newest = campaign_store.latest_metric(engine.db(), post.id)
    if newest is None:
        return post_estimate(engine, post.account_id, campaign)
    return Estimate(money.estimate(campaign, newest.views), campaign.currency, False)


def comparable(settings: Settings, amount: float, currency: str) -> tuple[float, bool]:
    """The amount in US$ and whether it is only as if in US$, for want of an exchange rate."""
    if currency == "USD":
        return amount, False
    rate = settings.today.rates.get(currency)
    return (amount * rate, False) if rate is not None else (amount, True)
