"""The Today page's board (SPEC 14.10): what sits around the day's list. Yesterday's best posts by the views
they gained, the rules of the campaigns still running, each account's posts today against its cap, the
posts you put up by hand, the submissions waiting on you and each campaign's share of the list, with the
ids of the links each one opens. Every piece of text is made safe to show here, as the API sends it."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from clipforge.campaigns import store as campaigns
from clipforge.campaigns import tracker
from clipforge.campaigns.ending import end_reason
from clipforge.post import store
from clipforge.post.distribute import full_caption
from clipforge.post.export import exported_file
from clipforge.post.plan import posts_today, who
from clipforge.server.views import cleaned, submission_json, web_link
from clipforge.today.needs import DECIDE_AFTER_S, _n
from clipforge.untrusted import clean_lines, clean_text

if TYPE_CHECKING:
    from clipforge.app import Engine
    from clipforge.campaigns.models import Campaign, Submission
    from clipforge.post.models import Account
    from clipforge.today.models import TodoItem

FRESH_S = 48 * 3600  # a post whose newest count is older isn't among yesterday's best
GAP_S = 20 * 3600  # what a post gained is counted from its newest count at least this much older
BEST = 3
BY_HAND = ("exported", "scheduled_on_tiktok")  # posts you put up yourself, or TikTok Studio puts up
OPEN = ("to_submit", "submitted", "approved")  # submissions that can still need you


def _handle(account: Account | None) -> str | None:
    return clean_text(who(account)) if account is not None else None


def best(engine: Engine, now: datetime) -> list[dict[str, Any]]:
    """The three posts that gained the most views, of those counted in the last 48 hours: their newest
    count less the newest one at least 20 hours older, or less nothing when there is none. A tie goes to
    the higher count."""
    conn = engine.db()
    at = now.timestamp()
    found = []
    for post in store.list_posts(conn, status=["posted"]):
        newest = campaigns.latest_metric(conn, post.id)
        if newest is None or at - newest.captured_at >= FRESH_S:
            continue
        before = [
            m for m in campaigns.metrics_of(conn, post.id) if m.captured_at <= newest.captured_at - GAP_S
        ]
        found.append((post, newest.views, newest.views - (before[-1].views if before else 0)))
    found.sort(key=lambda f: (-f[2], -f[1]))
    return [
        {
            "post_id": post.id,
            "hook": clean_text(post.clip_title),
            "handle": _handle(store.get_account(conn, post.account_id, deleted=True)),
            "platform": post.platform,
            "views": views,
            "gained": gained,
            "url": web_link(post.url),
        }
        for post, views, gained in found[:BEST]
    ]


def guidelines(engine: Engine, today: date) -> list[dict[str, Any]]:
    """Each campaign still running, newest first, as its short card says it: what it pays and up to what,
    what a caption must carry, what a clip mustn't have, where it goes, when it ends and its budget left."""
    shown = []
    for c in campaigns.list_campaigns(engine.db()):
        if end_reason(c, today) is not None:
            continue
        shown.append(
            cleaned(
                {
                    "id": c.id,
                    "name": c.name,
                    "marketplace": c.marketplace,
                    "currency": c.currency,
                    "rate": c.rate_per_1000,
                    "minimum": c.min_views,
                    "cap": c.max_payout,
                    "hashtags": c.required.hashtags,
                    "mentions": c.required.mentions,
                    "text": c.required.text,
                    "banned": c.banned,
                    "platforms": c.platforms,
                    "ends_on": c.ends_on,
                    "budget_left": c.budget_left,
                    "budget_entered_on": c.budget_entered_on,
                    "page_link": f"{c.id}:page" if c.campaign_url else None,
                }
            )
        )
    return shown


def accounts(engine: Engine) -> list[dict[str, Any]]:
    """Each account with its posts today against its cap, as its card has them."""
    return [
        {"id": a.id, "handle": _handle(a), "platform": a.platform, "today": posts_today(engine, a)}
        for a in store.list_accounts(engine.db())
    ]


def post_list(engine: Engine) -> list[dict[str, Any]]:
    """The posts you put up yourself (exported) and those TikTok Studio holds for their slot, soonest first:
    each with its caption as it goes up and whether its exported file is still there."""
    conn = engine.db()
    shown = []
    for post in sorted(store.list_posts(conn, status=list(BY_HAND)), key=lambda p: (p.scheduled_at, p.id)):
        shown.append(
            {
                "post_id": post.id,
                "status": post.status,
                "handle": _handle(store.get_account(conn, post.account_id, deleted=True)),
                "platform": post.platform,
                "hook": clean_text(post.clip_title),
                "caption": clean_lines(post.caption),
                "hashtags": [clean_text(t) for t in post.hashtags],
                "full_caption": clean_lines(full_caption(post.caption, post.hashtags)),
                "scheduled_at": post.scheduled_at,
                "has_file": exported_file(engine, post) is not None,
            }
        )
    return shown


def _todo(
    engine: Engine, submission: Submission, campaign: Campaign, today: date, due: str | None
) -> list[str]:
    """What the submission needs of you today: submitting while its campaign runs, its decision once the
    marketplace has had a day, and its views when a count is due by hand."""
    todo = []
    if submission.status == "to_submit" and end_reason(campaign, today) is None:
        todo.append("submit")
    waited = engine.now().timestamp() - DECIDE_AFTER_S
    sent = submission.submitted_at
    if submission.status == "submitted" and sent is not None and sent <= waited:
        todo.append("decide")
    if due is not None:
        todo.append("views")
    return todo


def submit_list(engine: Engine, today: date) -> list[dict[str, Any]]:
    """The submissions to submit, the decisions waiting and the views due, newest first: each as its row on
    a campaign's page has it, with its post's live link, handle and hook, its campaign's name and
    marketplace, the id of the submit link and which of the three it needs."""
    conn = engine.db()
    shown = []
    for sub in campaigns.list_submissions(conn, status=OPEN):
        campaign = campaigns.get_campaign(conn, sub.campaign_id)
        post = store.get_post(conn, sub.post_id)
        if campaign is None or post is None:
            continue
        due = tracker.views_due(engine, post, campaign, today)
        todo = _todo(engine, sub, campaign, today, due)
        if not todo:
            continue
        shown.append(
            submission_json(engine, sub, campaign)
            | {
                "views_due": due,
                "handle": _handle(store.get_account(conn, post.account_id, deleted=True)),
                "platform": post.platform,
                "hook": clean_text(post.clip_title),
                "url": web_link(post.url),
                "campaign_name": clean_text(campaign.name),
                "marketplace": clean_text(campaign.marketplace),
                "currency": campaign.currency,
                "submit_link": f"{campaign.id}:submit" if campaign.submit_url else None,
                "todo": todo,
            }
        )
    return shown


def _title(engine: Engine, item: TodoItem, campaign: Campaign, count: int, today: date) -> str:
    """The item as a campaign's row says it: only that campaign's share of it."""
    if item.kind in ("login", "insights"):
        account = store.get_account(engine.db(), item.ref, deleted=True)
        if account is None:
            return clean_text(item.title)
        if item.kind == "insights":
            return clean_text(f"Give Instagram the insights permission: {who(account)}")
        again = "Log in again" if account.status != "token_expiring" else "Renew the token"
        return clean_text(f"{again}: {who(account)}")
    if item.kind == "ends":
        return "Ends today" if campaign.ends_on == today.isoformat() else "Ends tomorrow"
    if item.kind == "payout":
        return "Payout ready"
    if item.kind == "join":
        return clean_text(f"Join on {campaign.marketplace}")
    if item.kind == "needs_you":
        return f"Finish {_n(count, 'post')} that {'needs' if count == 1 else 'need'} you"
    titles = {
        "drafts": f"Approve {_n(count, 'draft')}",
        "submit": f"Submit {_n(count, 'live post')}",
        "views": f"Enter views for {_n(count, 'post')}",
        "decide": f"Record {_n(count, 'decision')}",
        "exported": f"Put up {_n(count, 'exported post')}",
    }
    return titles[item.kind]


def campaign_todo(
    engine: Engine, items: list[TodoItem], campaign: Campaign, today: date
) -> list[dict[str, Any]]:
    """The undone items of today's list with this campaign in them, in the list's order, each titled for
    this campaign's count."""
    return [
        {
            "kind": item.kind,
            "title": _title(engine, item, campaign, item.campaigns[campaign.id], today),
            "count": item.campaigns[campaign.id],
        }
        for item in items
        if not item.done and campaign.id in item.campaigns
    ]
