"""Accounts worth adding (SPEC 10.11). From the live briefs (new or seen, in a niche Settings lists), the
money on offer per niche a day, in US$ through your exchange rates. A niche a clipping channel already posts
for is left out, and so is one you hid; the 3 with the most money become suggestions, each with why (how
many briefs, their budget left and the best 3), the accounts to make (never TikTok for news and politics),
name ideas, a bio and a picture idea that never pass as the show or brand, the clipping channel to set up
and a posting plan within the caps. The card also says which of your accounts could take a brief today."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import TYPE_CHECKING, Any, Literal

from clipforge.briefs import names, store
from clipforge.briefs.models import Brief
from clipforge.briefs.worth import Worth, fits, political, reposting, stand_in, worth
from clipforge.campaigns.models import Platform
from clipforge.campaigns.money import money_text
from clipforge.config import Niche
from clipforge.ingest.urls import canonical_url, youtube_id
from clipforge.post import store as post_store
from clipforge.post.models import Account
from clipforge.post.plan import NAMES, posts_today, who
from clipforge.post.slots import rules_for
from clipforge.today.value import comparable, post_estimate
from clipforge.untrusted import clean_text
from clipforge.watch import store as watch_store
from clipforge.watch.sources import channel_link

if TYPE_CHECKING:
    from clipforge.app import Engine

SUGGESTIONS_MAX = 3
BEST_MAX = 3
SOURCES_MAX = 10
COULD_TAKE_MAX = 3
HIDE_DAYS = 30
PLATFORMS: tuple[Platform, ...] = ("tiktok", "instagram")
_LIVE = ["new", "seen"]
WINDOWS = ", in your posting windows."
Coverage = Literal["channel", "tagged"]


@dataclass(frozen=True)
class Suggestion:
    """A niche worth an account. Money is in US$ through your exchange rates: ``as_if`` when some of it
    had no rate, ``guess`` when some of it is a starting guess. ``budget_left`` is None when no brief said."""

    niche: str
    language: str
    label: str
    count: int
    budget_left: float | None
    per_day: float
    as_if: bool
    guess: bool
    best: list[dict[str, Any]]
    make: list[Platform]
    names: list[str]
    bio: str
    picture: str
    channel: dict[str, Any]
    plan: str


@dataclass(frozen=True)
class Advice:
    suggestions: list[Suggestion]
    could_take: list[str]


def _tagged(accounts: list[Account], niche: Niche) -> list[Account]:
    return [a for a in accounts if set(niche.tags) & set(a.settings.niche_tags)]


def covered(engine: Engine) -> dict[str, Coverage]:
    """Each niche your accounts cover, by name: "channel" when a clipping channel posts to an account with
    one of its tags, "tagged" when an account has one but no clipping channel posts to it. A paused or
    logged-out account still covers its niches: it is there to come back to."""
    conn = engine.db()
    accounts = post_store.list_accounts(conn)
    posted_to = {a for channel in watch_store.list_channels(conn) for a in channel.account_ids}
    found: dict[str, Coverage] = {}
    for niche in engine.settings.briefs.niches:
        tagged = _tagged(accounts, niche)
        if any(a.id in posted_to for a in tagged):
            found[niche.name] = "channel"
        elif tagged:
            found[niche.name] = "tagged"
    return found


def _usd(engine: Engine, amount: float, currency: str) -> tuple[float, bool]:
    return comparable(engine.settings, amount, currency)


def _richest_first(engine: Engine, pairs: list[tuple[Brief, Worth]]) -> list[tuple[Brief, Worth]]:
    """By worth in US$, highest first, those whose worth isn't known last; ties keep their order."""

    def key(pair: tuple[Brief, Worth]) -> tuple[int, float]:
        value = pair[1]
        if value.amount is None:
            return (1, 0.0)
        return (0, -_usd(engine, value.amount, value.currency)[0])

    return sorted(pairs, key=key)


def reposting_first(engine: Engine, pairs: list[tuple[Brief, Worth]]) -> list[tuple[Brief, Worth]]:
    """The reposting briefs, then the rest, each richest first: the order a brief to take comes in."""
    return sorted(_richest_first(engine, pairs), key=lambda pair: not reposting(pair[0]))


def _sources(briefs: list[Brief]) -> list[str]:
    """The briefs' allowed footage as clipping channel sources: YouTube videos and channels only, each in
    its plain form, at most 10."""
    found: list[str] = []
    for brief in briefs:
        for link in brief.footage.links:
            video = youtube_id(link)
            plain = canonical_url(video) if video else channel_link(link)
            if plain and plain not in found:
                found.append(plain)
    return found[:SOURCES_MAX]


def _posts(count: int) -> str:
    return f"{count} post{'' if count == 1 else 's'}"


def plan_text(caps: dict[Platform, int]) -> str:
    """The posting plan for the accounts' caps, by platform."""
    if len(caps) == 1:
        return f"Up to {_posts(next(iter(caps.values())))} a day{WINDOWS}"
    if len(set(caps.values())) == 1:
        return f"Up to {_posts(next(iter(caps.values())))} a day on each{WINDOWS}"
    (first, most), (second, other) = caps.items()
    return f"Up to {_posts(most)} a day on {NAMES[first]} and {other} on {NAMES[second]}{WINDOWS}"


def _caps(engine: Engine, make: list[Platform], tagged: list[Account]) -> dict[Platform, int]:
    """The daily caps the plan speaks of: the platforms' own for accounts to make, else the highest of the
    tagged accounts' on each platform."""
    posting = engine.settings.posting
    if make:
        defaults = {"tiktok": posting.tiktok_daily_cap, "instagram": posting.instagram_daily_cap}
        return {p: defaults[p] for p in make}
    caps: dict[Platform, int] = {}
    for platform in PLATFORMS:
        held = [rules_for(posting, a).cap for a in tagged if a.platform == platform]
        if held:
            caps[platform] = max(held)
    return caps


def _suggestion(
    engine: Engine,
    niche: Niche,
    language: str,
    ranked: list[tuple[Brief, Worth]],
    coverage: Coverage | None,
    tagged: list[Account],
) -> Suggestion | None:
    per_day, budget, as_if, guess = 0.0, None, False, False
    for brief, value in ranked:
        if value.amount is not None:
            amount, unsure = _usd(engine, value.amount, value.currency)
            per_day, as_if, guess = per_day + amount, as_if or unsure, guess or value.guess
        if brief.budget_left is not None:
            left, unsure = _usd(engine, brief.budget_left, brief.currency)
            budget, as_if = (budget or 0.0) + left, as_if or unsure
    allowed = [p for p in PLATFORMS if any(p in brief.platforms for brief, _ in ranked)]
    if political(niche):
        allowed = [p for p in allowed if p != "tiktok"]
    make = [] if coverage == "tagged" else allowed
    if not allowed:
        return None
    label = names.label(niche)
    return Suggestion(
        niche=niche.name,
        language=language,
        label=label,
        count=len(ranked),
        budget_left=None if budget is None else round(budget, 2),
        per_day=round(per_day, 2),
        as_if=as_if,
        guess=guess,
        best=[
            {
                "id": brief.id,
                "title": clean_text(brief.title),
                "brand": clean_text(brief.brand),
                "worth": asdict(value),
            }
            for brief, value in ranked[:BEST_MAX]
        ],
        make=make,
        names=names.ideas(niche),
        bio=names.bio(niche),
        picture=names.picture(niche),
        channel={
            "name": f"{label} clips",
            "sources": _sources([brief for brief, _ in ranked]),
            "account_ids": [a.id for a in tagged] if coverage == "tagged" else [],
        },
        plan=plan_text(_caps(engine, make, tagged)),
    )


def _could_take(
    engine: Engine, ranked: list[tuple[Brief, Worth]], fitting: dict[str, list[Account]]
) -> list[str]:
    """Up to 3 lines, the reposting briefs then the richest first: the first account that fits each and has
    room under its cap today, each account once."""
    lines: list[str] = []
    used: set[str] = set()
    for brief, _ in ranked:
        for account in fitting[brief.id]:
            if account.id in used:
                continue
            today = posts_today(engine, account)
            if today["posts"] >= today["cap"]:
                continue
            estimate = post_estimate(engine, account.id, stand_in(brief, account.platform))
            if estimate.amount is None:
                continue
            used.add(account.id)
            name = clean_text(brief.brand or brief.title)
            about = money_text(estimate.amount, brief.currency)
            lines.append(f"{who(account)} could take {name}'s brief today (about {about} a post)")
            break
        if len(lines) == COULD_TAKE_MAX:
            break
    return lines


def advice(engine: Engine, today: date) -> Advice:
    """The suggestions and the could-take lines for ``today``; what you hid stays out until its day."""
    conn = engine.db()
    niches = {n.name.casefold(): n for n in engine.settings.briefs.niches}
    live = [b for b in store.briefs(conn, status=_LIVE) if b.niche and b.niche.casefold() in niches]
    fitting = {b.id: fits(engine, b) for b in live}
    ranked = _richest_first(engine, [(b, worth(engine, b, fitting[b.id])) for b in live])
    hidden = store.hidden_advice(conn, today.isoformat())
    coverage = covered(engine)
    accounts = post_store.list_accounts(conn)
    groups: dict[tuple[str, str], list[tuple[Brief, Worth]]] = {}
    for brief, value in ranked:
        niche = niches[(brief.niche or "").casefold()]
        if political(niche) and "instagram" not in brief.platforms:
            continue  # only TikTok could post it, and news never goes there
        groups.setdefault((niche.name, brief.language or niche.language), []).append((brief, value))
    found: list[Suggestion] = []
    for (name, language), briefs in groups.items():
        state = coverage.get(name)
        if state == "channel" or {("suggestion", name, language), ("niche", name, language)} & hidden:
            continue
        niche = niches[name.casefold()]
        made = _suggestion(engine, niche, language, briefs, state, _tagged(accounts, niche))
        if made is not None:
            found.append(made)
    found.sort(key=lambda s: -s.per_day)
    return Advice(found[:SUGGESTIONS_MAX], _could_take(engine, reposting_first(engine, ranked), fitting))
