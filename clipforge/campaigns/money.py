"""What the campaigns are worth so far (SPEC 10.10, 14.11, 14.12), from your own entries: each post's
estimate from its newest view count and the campaign's rate, minimum and cap; each campaign's counts from
made to paid; and the totals per marketplace and currency, never mixed, ready to withdraw once the pending
money reaches the marketplace's minimum payout."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from clipforge.campaigns import store

if TYPE_CHECKING:
    from clipforge.app import Engine
    from clipforge.campaigns.models import Campaign

UNAPPROVED = ("draft", "discarded")  # a post made for the campaign that you haven't approved
OUTSTANDING = ("submitted", "approved")  # sent, and not yet turned down or paid
ACCEPTED = ("approved", "paid")
PREFIX = {"USD": "US$", "AUD": "A$"}


def money_text(amount: float, currency: str) -> str:
    """``US$1,062.40`` or ``12.40 EUR``, as the Money page writes it."""
    if currency in PREFIX:
        return f"{PREFIX[currency]}{amount:,.2f}"
    return f"{amount:,.2f} {currency}"


def estimate(campaign: Campaign, views: int | None) -> float | None:
    """What a post with ``views`` should earn: None without a rate or a count, nothing under the
    campaign's minimum views, else the rate per 1,000 views up to the campaign's most per post."""
    if campaign.rate_per_1000 is None or views is None:
        return None
    if campaign.min_views is not None and views < campaign.min_views:
        return 0.0
    value = views / 1000 * campaign.rate_per_1000
    if campaign.max_payout is not None:
        value = min(value, campaign.max_payout)
    return round(value, 2)


def _posts_made(engine: Engine, campaign_id: str) -> list[tuple[str, str]]:
    """(status, state JSON) of each post of a clip of a project linked to the campaign."""
    rows = engine.db().execute(
        "SELECT p.status, p.state_json FROM posts p JOIN clips c ON c.id = p.clip_id"
        " JOIN projects pr ON pr.id = c.project_id WHERE pr.campaign_id = ?",
        (campaign_id,),
    )
    return [(r[0], r[1]) for r in rows]


def campaign_money(engine: Engine, campaign: Campaign) -> dict[str, Any]:
    """The campaign's counts (made and approved by you; live, submitted, accepted by the marketplace and
    paid), its views (the newest count of each post, None before any), what its outstanding submissions
    should earn, what of that is pending payment (None for both without a rate), and what it paid."""
    conn = engine.db()
    made = [s for s, state in _posts_made(engine, campaign.id) if not json.loads(state).get("off_campaign")]
    submissions = store.list_submissions(conn, campaign.id)
    views: int | None = None
    estimated = pending = None if campaign.rate_per_1000 is None else 0.0
    paid = 0.0
    for sub in submissions:
        count = store.latest_metric(conn, sub.post_id)
        if count is not None:
            views = (views or 0) + count.views
        worth = estimate(campaign, count.views if count else None)
        if sub.status in OUTSTANDING and estimated is not None:
            estimated += worth or 0.0
            if sub.status == "approved" and pending is not None:
                pending += worth or 0.0
        if sub.status == "paid":
            paid += sub.paid_amount or 0.0
    counts = {
        "made": len(made),
        "approved": sum(1 for status in made if status not in UNAPPROVED),
        "live": len(submissions),
        "submitted": sum(1 for s in submissions if s.status != "to_submit"),
        "accepted": sum(1 for s in submissions if s.status in ACCEPTED),
        "paid": sum(1 for s in submissions if s.status == "paid"),
    }
    return {
        "counts": counts,
        "views": views,
        "estimated": None if estimated is None else round(estimated, 2),
        "pending": None if pending is None else round(pending, 2),
        "paid": round(paid, 2),
    }


def _add(total: float | None, value: float | None) -> float | None:
    if value is None:
        return total
    return (total or 0.0) + value


def totals(engine: Engine) -> list[dict[str, Any]]:
    """One line per marketplace (named alike whatever the case, as its first campaign spells it) and
    currency: what's estimated, pending and paid there, its minimum payout and whether it has a Withdraw
    link, and ``ready`` when something is pending and it reaches the minimum, or there is no minimum."""
    lines: dict[tuple[str, str], dict[str, Any]] = {}
    for campaign in reversed(store.list_campaigns(engine.db())):  # oldest first
        key = (campaign.marketplace.casefold(), campaign.currency)
        line = lines.setdefault(
            key,
            {
                "marketplace": campaign.marketplace,
                "currency": campaign.currency,
                "estimated": None,
                "pending": None,
                "paid": 0.0,
            },
        )
        got = campaign_money(engine, campaign)
        line["estimated"] = _add(line["estimated"], got["estimated"])
        line["pending"] = _add(line["pending"], got["pending"])
        line["paid"] += got["paid"]
    places = {name.casefold(): place for name, place in engine.settings.marketplaces.items()}
    found = []
    for key in sorted(lines):
        line = lines[key]
        place = places.get(key[0])
        least = place.min_payout if place is not None else None
        pending = line["pending"] or 0.0
        for name in ("estimated", "pending", "paid"):
            if line[name] is not None:
                line[name] = round(line[name], 2)
        line |= {
            "min_payout": least,
            "withdraw": bool(place and place.withdraw_url),
            "ready": pending > 0 and (least is None or pending >= least),
        }
        found.append(line)
    return found
