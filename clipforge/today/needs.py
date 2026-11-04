"""What needs you now (SPEC 14.10 item 1, 10.11): one need per kind and thing, with its title, what it could
earn or protects, whether it is due today and how many of its things each campaign has. A post not yet live
is worth what a new post on its account should earn on a running campaign; a live one, what its newest count
says. A need across currencies is in the one with the most value, the rest added through the exchange rates
you set, and a post without a campaign counts with nothing."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import replace
from datetime import date, timedelta
from typing import TYPE_CHECKING

from clipforge import repo
from clipforge.briefs import advice, run
from clipforge.briefs import store as brief_store
from clipforge.briefs.worth import fits, reposting, worth
from clipforge.campaigns import store as campaign_store
from clipforge.campaigns import tracker
from clipforge.campaigns.ending import end_reason
from clipforge.campaigns.money import campaign_money, money_text, totals
from clipforge.campaigns.rules import campaign_of_post
from clipforge.config import Settings
from clipforge.post import store as post_store
from clipforge.post.models import PlanRequest
from clipforge.post.plan import NAMES, build_plan, who
from clipforge.today import path
from clipforge.today.models import Estimate, Need
from clipforge.today.value import comparable, live_estimate, post_estimate
from clipforge.untrusted import clean_text
from clipforge.watch import drafts as draft_store
from clipforge.watch import store as watch_store

if TYPE_CHECKING:
    from clipforge.app import Engine
    from clipforge.campaigns.models import Campaign
    from clipforge.post.models import Post

DECIDE_AFTER_S = 86400  # a marketplace takes a day or so to decide on a submission
NO_CHANNEL = "none"  # drafts whose clipping channel was removed
PENDING = ("scheduled", "rendering", "needs_you", "draft")  # posts a login stands in the way of
BRIEFS_MAX = 10  # the new briefs one item counts, the richest first
SCOUT_FILE = "HeyClicky's campaign file"
SCOUT_PROBLEMS = {"missing": "is missing", "reshaped": "has changed shape"}  # else it couldn't be read
# What to do about it: a missing file is most often HeyClicky not running, or not on this Mac at all.
SCOUT_NEXT = {
    "missing": "Open HeyClicky so its Campaign Scout writes the file, or turn off Use Campaign Scout "
    "(HeyClicky) in Settings."
}
SCOUT_AGAIN = (
    "ClipForge tries it again tomorrow morning. To stop, turn off Use Campaign Scout (HeyClicky) in Settings."
)


def _n(count: int, one: str, many: str | None = None) -> str:
    return f"{count} {one if count == 1 else (many or one + 's')}"


def _total(settings: Settings, parts: list[Estimate]) -> tuple[float, str, bool]:
    """The parts' sum in the currency with the most value, the others added through their exchange rates
    (as if in US$ without one), and whether any part with an amount was a guess."""
    sums: dict[str, float] = defaultdict(float)
    guess = False
    for part in parts:
        if part.amount is not None:
            sums[part.currency] += part.amount
            guess = guess or part.guess
    if not sums:
        return 0.0, "USD", False
    usd = {c: comparable(settings, amount, c)[0] for c, amount in sums.items()}
    top = max(sorted(sums), key=lambda c: usd[c])
    rate = None if top == "USD" else settings.today.rates.get(top)
    total = sums[top] + sum(usd[c] / rate if rate else usd[c] for c in sums if c != top)
    return round(total, 2), top, guess


class _Gather:
    """One need's posts or things as they are found: their ids, their estimates and their campaigns."""

    def __init__(self) -> None:
        self.ids: list[str] = []
        self.parts: list[Estimate] = []
        self.campaigns: Counter[str] = Counter()
        self.left: dict[str, float] = {}  # each campaign's budget left that no planned post here has yet

    def add(self, item_id: str | None, campaign: Campaign | None, worth: Estimate | None) -> None:
        if item_id is not None:
            self.ids.append(item_id)
        if campaign is not None:
            self.campaigns[campaign.id] += 1
        if worth is not None:
            self.parts.append(worth)

    def add_planned(self, item_id: str | None, campaign: Campaign | None, worth: Estimate | None) -> None:
        """A post not yet live: what it should earn, out of what its campaign's budget has left once the
        posts before it here have theirs."""
        if (
            worth is not None
            and worth.amount is not None
            and campaign is not None
            and campaign.budget_left is not None
        ):
            left = self.left.get(campaign.id, campaign.budget_left)
            worth = replace(worth, amount=round(min(worth.amount, left), 2))
            self.left[campaign.id] = left - worth.amount
        self.add(item_id, campaign, worth)

    def need(self, engine: Engine, kind: str, ref: str, title: str, **fields) -> Need:
        value, currency, guess = _total(engine.settings, self.parts)
        return Need(
            kind,
            ref,
            self.ids,
            title,
            value=value,
            currency=currency,
            guess=guess,
            campaigns=dict(self.campaigns),
            **fields,
        )


def _campaign(engine: Engine, post: Post) -> Campaign | None:
    return (
        draft_store.campaign_of_draft(engine, post)
        if post.status == "draft"
        else campaign_of_post(engine, post)
    )


def _planned(engine: Engine, post: Post, campaign: Campaign | None, today: date) -> Estimate | None:
    """What a post not yet live should earn: nothing without a campaign or once the campaign has ended."""
    if campaign is None or end_reason(campaign, today) is not None:
        return None
    return post_estimate(engine, post.account_id, campaign)


def _live(engine: Engine, post: Post, campaign: Campaign | None) -> Estimate | None:
    return None if campaign is None else live_estimate(engine, post, campaign)


def _unticked(engine: Engine, live: list[Post]) -> set[str]:
    """The plan keys of the drafts with a campaign rule you still have to check by hand."""
    if not live:
        return set()
    try:
        plan = build_plan(
            engine, PlanRequest(draft_ids=[p.id for p in live]), plan_id="drafts", now=engine.now()
        )
    except LookupError:  # one went while this was read; the next refresh leaves it out
        return set()
    return {w.key for w in plan.warnings if w.code == "rule_unchecked" and w.key is not None}


def _drafts(engine: Engine, today: date) -> list[Need]:
    conn = engine.db()
    live = draft_store.live_drafts(engine)
    unticked = _unticked(engine, live)
    found: dict[str, _Gather] = defaultdict(_Gather)
    by_hand: Counter[str] = Counter()
    for post in live:
        clip = repo.get_clip(conn, post.clip_id) if post.clip_id else None
        project = repo.get_project(conn, clip.project_id) if clip else None
        channel_id = project.clip_channel_id if project else None
        ref = channel_id if channel_id and watch_store.get_channel(conn, channel_id) else NO_CHANNEL
        campaign = draft_store.campaign_of_draft(engine, post)
        found[ref].add_planned(post.id, campaign, _planned(engine, post, campaign, today))
        if f"{post.clip_id}:{post.account_id}" in unticked:
            by_hand[ref] += 1
    needs = []
    for ref, got in found.items():
        title = f"Approve {_n(len(got.ids), 'draft')}"
        if ref != NO_CHANNEL:
            title += f" ({watch_store.get_channel(conn, ref).name})"
        detail = f"{by_hand[ref]} with rules to check by hand" if by_hand[ref] else None
        needs.append(got.need(engine, "drafts", ref, title, detail=detail))
    return needs


def _needs_you(engine: Engine, today: date) -> list[Need]:
    got = _Gather()
    for post in post_store.list_posts(engine.db(), status=["needs_you"]):
        campaign = _campaign(engine, post)
        got.add_planned(post.id, campaign, _planned(engine, post, campaign, today))
    if not got.ids:
        return []
    title = f"{_n(len(got.ids), 'post')} {'needs' if len(got.ids) == 1 else 'need'} you"
    return [got.need(engine, "needs_you", "all", title, due=True)]


def _logins(engine: Engine, today: date) -> list[Need]:
    needs = []
    for account in post_store.list_accounts(engine.db()):
        if account.status not in ("needs_login", "token_expiring"):
            continue
        got = _Gather()
        got.ids.append(account.id)
        for post in post_store.list_posts(engine.db(), status=PENDING, account_id=account.id):
            campaign = _campaign(engine, post)
            got.add_planned(None, campaign, _planned(engine, post, campaign, today))
        where = f"{who(account)} on {NAMES[account.platform]}"
        title = f"Log in again: {where}" if account.status == "needs_login" else f"Renew the token: {where}"
        needs.append(got.need(engine, "login", account.id, title, due=True))
    return needs


def _insights(engine: Engine) -> list[Need]:
    needs = []
    for account in post_store.list_accounts(engine.db()):
        if account.platform != "instagram" or account.method != "api":
            continue
        posts = post_store.list_posts(engine.db(), status=["posted"], account_id=account.id)
        blind = [p for p in posts if p.state.get("insights") == "no_permission"]
        if not blind:
            continue
        got = _Gather()
        got.ids.append(account.id)
        for post in blind:
            campaign = campaign_of_post(engine, post)
            got.add(None, campaign, _live(engine, post, campaign))
        title = f"Give Instagram the insights permission: {who(account)}"
        needs.append(got.need(engine, "insights", account.id, title))
    return needs


def _ends_soon(campaign: Campaign, today: date) -> bool:
    """Whether the campaign's last day is today or tomorrow, while it still runs."""
    if campaign.ends_on is None or end_reason(campaign, today) is not None:
        return False
    return date.fromisoformat(campaign.ends_on) <= today + timedelta(days=1)


def _to_submit(engine: Engine, campaign: Campaign) -> list[Post]:
    conn = engine.db()
    subs = campaign_store.list_submissions(conn, campaign.id, status=["to_submit"])
    return [p for s in subs if (p := post_store.get_post(conn, s.post_id)) is not None]


def _submits(engine: Engine, today: date) -> list[Need]:
    needs = []
    for campaign in campaign_store.list_campaigns(engine.db()):
        if end_reason(campaign, today) is not None:
            continue
        got = _Gather()
        for post in _to_submit(engine, campaign):
            got.add(post.id, campaign, live_estimate(engine, post, campaign))
        if got.ids:
            title = f"Submit {_n(len(got.ids), 'live post')} to {campaign.marketplace} ({campaign.name})"
            needs.append(got.need(engine, "submit", campaign.id, title, due=_ends_soon(campaign, today)))
    return needs


def _views(engine: Engine, today: date) -> list[Need]:
    found: dict[str, _Gather] = defaultdict(_Gather)
    for post in post_store.list_posts(engine.db(), status=["posted"]):
        campaign = campaign_of_post(engine, post)
        if campaign is not None and tracker.views_due(engine, post, campaign, today) is not None:
            found[post.platform].add(post.id, campaign, live_estimate(engine, post, campaign))
    return [
        got.need(
            engine,
            "views",
            platform,
            f"Enter {NAMES[platform]} views for {_n(len(got.ids), 'post')}",
            due=True,
        )
        for platform, got in found.items()
    ]


def _spellings(engine: Engine) -> dict[str, str]:
    """Each marketplace as its oldest campaign spells it, by its name in any case."""
    names: dict[str, str] = {}
    for campaign in reversed(campaign_store.list_campaigns(engine.db())):
        names.setdefault(campaign.marketplace.casefold(), campaign.marketplace)
    return names


def _decisions(engine: Engine) -> list[Need]:
    conn = engine.db()
    waited = engine.now().timestamp() - DECIDE_AFTER_S
    found: dict[str, _Gather] = defaultdict(_Gather)
    for sub in campaign_store.list_submissions(conn, status=["submitted"]):
        campaign = campaign_store.get_campaign(conn, sub.campaign_id)
        post = post_store.get_post(conn, sub.post_id)
        if campaign is None or post is None or sub.submitted_at is None or sub.submitted_at > waited:
            continue
        found[campaign.marketplace.casefold()].add(post.id, campaign, live_estimate(engine, post, campaign))
    names = _spellings(engine)
    return [
        got.need(engine, "decide", ref, f"Record {_n(len(got.ids), f'{names[ref]} decision')}")
        for ref, got in found.items()
    ]


def _endings(engine: Engine, today: date) -> list[Need]:
    needs = []
    for campaign in campaign_store.list_campaigns(engine.db()):
        if not _ends_soon(campaign, today):
            continue
        got = _Gather()
        got.add(campaign.id, campaign, None)
        pending = campaign_money(engine, campaign)["pending"]
        if pending is not None:
            got.parts.append(Estimate(pending, campaign.currency, False))
        for post in _to_submit(engine, campaign):
            got.parts.append(live_estimate(engine, post, campaign))
        when = "today" if campaign.ends_on == today.isoformat() else "tomorrow"
        needs.append(got.need(engine, "ends", campaign.id, f"{campaign.name} ends {when}", due=True))
    return needs


def _payouts(engine: Engine) -> list[Need]:
    conn = engine.db()
    needs = []
    for line in totals(engine):
        if not line["ready"]:
            continue
        ref = f"{line['marketplace']}:{line['currency']}"
        approved: Counter[str] = Counter()
        for campaign in campaign_store.list_campaigns(conn):
            if (campaign.marketplace.casefold(), campaign.currency) == (
                line["marketplace"].casefold(),
                line["currency"],
            ):
                subs = campaign_store.list_submissions(conn, campaign.id, status=["approved"])
                if subs:
                    approved[campaign.id] = len(subs)
        pending = line["pending"]
        needs.append(
            Need(
                "payout",
                ref,
                [ref],
                f"Payout ready on {line['marketplace']}",
                detail=f"{money_text(pending, line['currency'])} approved",
                value=pending,
                currency=line["currency"],
                campaigns=dict(approved),
            )
        )
    return needs


def _exported(engine: Engine, today: date) -> list[Need]:
    got = _Gather()
    for post in post_store.list_posts(engine.db(), status=["exported"]):
        campaign = _campaign(engine, post)
        got.add_planned(post.id, campaign, _planned(engine, post, campaign, today))
    if not got.ids:
        return []
    return [got.need(engine, "exported", "all", f"Put up {_n(len(got.ids), 'exported post')}")]


def _briefs(engine: Engine, today: date) -> list[Need]:
    """The new briefs worth a look: those an account of yours fits, or in a niche suggested for a new
    account, the reposting ones then the richest, 10 at most, worth what they could earn a day together."""
    suggested = {s.niche.casefold() for s in advice.advice(engine, today).suggestions}
    found = []
    for brief in brief_store.briefs(engine.db(), status=["new"]):
        fitting = fits(engine, brief)
        if fitting or (brief.niche or "").casefold() in suggested:
            found.append((brief, worth(engine, brief, fitting)))
    if not found:
        return []
    got = _Gather()
    top = advice.reposting_first(engine, found)[:BRIEFS_MAX]
    for brief, value in top:
        got.add(brief.id, None, Estimate(value.amount, value.currency, value.guess))
    title = f"Look at {_n(len(got.ids), 'new brief')}"
    if reposts := sum(reposting(brief) for brief, _ in top):
        title += f", {reposts} of them reposting" if len(top) > 1 else ", a reposting one"
    if any(part.amount is not None for part in got.parts):
        value, currency, guess = _total(engine.settings, got.parts)
        how = ", a guess" if guess else " at your views"
        title += f" (about {money_text(value, currency)} a day{how})"
    return [got.need(engine, "briefs", "new", title)]


def _sources(engine: Engine) -> list[Need]:
    """Each source whose read didn't go through today: an added site's briefs to paste, or what is wrong
    with Campaign Scout's file. Each tries again the next morning."""
    needs = []
    for source in brief_store.sources(engine.db()):
        if not run.stopped_today(engine, source):
            continue
        if source.reader == "campaign_scout":
            problem = SCOUT_PROBLEMS.get(source.error or "", "couldn't be read")
            detail = SCOUT_NEXT.get(source.error or "", SCOUT_AGAIN)
            needs.append(Need("source", source.id, [source.id], f"{SCOUT_FILE} {problem}", detail=detail))
        else:
            title = f"Paste briefs from {source.marketplace}"
            needs.append(Need("source", source.id, [source.id], title, detail=clean_text(source.error)))
    return needs


def _joins(engine: Engine, today: date) -> list[Need]:
    """Each running campaign made from a brief that you haven't said you joined, worth what its brief
    could earn a day."""
    conn = engine.db()
    needs = []
    for campaign in campaign_store.list_campaigns(conn):
        if campaign.brief_id is None or campaign.joined_at is not None:
            continue
        if end_reason(campaign, today) is not None:
            continue
        got = _Gather()
        brief = brief_store.get_brief(conn, campaign.brief_id)
        value = worth(engine, brief) if brief is not None else None
        got.add(campaign.id, campaign, value and Estimate(value.amount, value.currency, value.guess))
        title = f"Join {campaign.name} on {campaign.marketplace}"
        needs.append(got.need(engine, "join", campaign.id, title))
    return needs


def needs(engine: Engine, today: date) -> list[Need]:
    """Everything that needs you now, one need per kind and ref, in no order, with the path's open steps
    after them while the path is on."""
    found = [
        *_drafts(engine, today),
        *_needs_you(engine, today),
        *_logins(engine, today),
        *_insights(engine),
        *_submits(engine, today),
        *_views(engine, today),
        *_decisions(engine),
        *_endings(engine, today),
        *_payouts(engine),
        *_exported(engine, today),
        *_briefs(engine, today),
        *_sources(engine),
        *_joins(engine, today),
    ]
    return path.walk(engine, found)
