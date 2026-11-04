"""The Money page's data (SPEC 14.12): a row per campaign post, the day it went up on the posting time zone's
clock, its account, campaign and hook, its views at 2 days, at 7 days and now, its status and what it should
earn or was paid, in the campaign's own currency with the rate Settings gives that currency. The page works
every total, period and split from these rows, so the money rules stay those of the Campaigns sheet: an
estimate while submitted or approved, pending while approved, and paid once you enter it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from clipforge.campaigns import money, tracker
from clipforge.campaigns import store as campaigns
from clipforge.post import store as posts
from clipforge.post.plan import who
from clipforge.server.views import web_link
from clipforge.today.value import SEVEN_DAYS, TWO_DAYS, comparable
from clipforge.untrusted import clean_text

if TYPE_CHECKING:
    from clipforge.app import Engine
    from clipforge.post.models import Account


def _account_json(account: Account) -> dict[str, Any]:
    return {
        "id": account.id,
        "platform": account.platform,
        "handle": clean_text(who(account)),
        "niche": clean_text(account.settings.niche_tags[0]) if account.settings.niche_tags else None,
    }


def money_posts(engine: Engine) -> list[dict[str, Any]]:
    """One row per submission whose post still exists, newest posted first."""
    conn = engine.db()
    known = {c.id: c for c in campaigns.list_campaigns(conn)}
    accounts: dict[str, Account | None] = {}
    rows: list[tuple[float, dict[str, Any]]] = []
    for sub in campaigns.list_submissions(conn):
        campaign = known.get(sub.campaign_id)
        post = posts.get_post(conn, sub.post_id)
        if campaign is None or post is None:
            continue
        if post.account_id not in accounts:
            accounts[post.account_id] = posts.get_account(conn, post.account_id, deleted=True)
        account = accounts[post.account_id]
        shown = _account_json(account) if account is not None else None
        metrics = campaigns.metrics_of(conn, post.id)
        views = metrics[-1].views if metrics else None
        worth = money.estimate(campaign, views)
        went_up = post.posted_at if post.posted_at is not None else sub.created_at
        timed = post.posted_at is not None
        rate, as_if = comparable(engine.settings, 1.0, campaign.currency)
        row = {
            "post_id": post.id,
            "posted_on": tracker.day_of(engine, went_up).isoformat(),
            "account_id": post.account_id,
            "handle": shown["handle"] if shown else None,
            "platform": post.platform,
            "niche": shown["niche"] if shown else None,
            "campaign_id": campaign.id,
            "campaign": clean_text(campaign.name),
            "marketplace": clean_text(campaign.marketplace),
            "hook": clean_text(post.clip_title),
            "url": web_link(post.url),
            "views_2d": tracker.views_on(engine, metrics, went_up, *TWO_DAYS) if timed else None,
            "views_7d": tracker.views_on(engine, metrics, went_up, *SEVEN_DAYS) if timed else None,
            "views": views,
            "status": sub.status,
            "reason": clean_text(sub.reason),
            "currency": campaign.currency,
            "estimated": worth if sub.status in money.OUTSTANDING else None,
            "pending": worth if sub.status == "approved" else None,
            "paid": sub.paid_amount if sub.status == "paid" else None,
            "rate": rate,
            "as_if": as_if,
        }
        rows.append((went_up, row))
    rows.sort(key=lambda pair: pair[0], reverse=True)  # stable: a tie keeps the newest submission first
    return [row for _, row in rows]


def money_view(engine: Engine) -> dict[str, Any]:
    """The posts, the accounts to filter them by (every live one, and a deleted one that has a post here),
    by platform then handle, the currencies counted as if US$ for want of a rate, and today's date."""
    conn = engine.db()
    rows = money_posts(engine)
    shown = {a.id: a for a in posts.list_accounts(conn)}
    for row in rows:
        if row["account_id"] not in shown:
            gone = posts.get_account(conn, row["account_id"], deleted=True)
            if gone is not None:
                shown[gone.id] = gone
    accounts = sorted(
        (_account_json(a) for a in shown.values()),
        key=lambda a: (a["platform"], (a["handle"] or "").casefold()),
    )
    return {
        "today": tracker.day_of(engine, engine.now()).isoformat(),
        "posts": rows,
        "accounts": accounts,
        "as_if": sorted({row["currency"] for row in rows if row["as_if"]}),
    }
