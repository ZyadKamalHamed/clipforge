"""What a brief is worth, and whether ClipForge may open its link (SPEC 10.11). An account fits a brief when
it is connected (or only its token is running out), not paused, on a platform the brief allows and tagged
with one of the tags of the brief's niche, and never a TikTok account for news and politics, which TikTok
bans paid posts about (10.6). A brief is worth, a day, the best of its fitting accounts: the
account's post estimate (today/value.py) on the brief's rate and cap for that platform, times the account's
daily cap, held to the budget left. With no account fitting, it is worth one post at the starting guess on
its best rate, marked a guess. Without a rate its worth isn't known, and once gone it is worth nothing. A
brief's link opens only when it is https on a marketplace ClipForge reads: the site of a brief source's
list page, or Content Rewards, Whop or Vyro, where Campaign Scout's briefs come from. The same holds for a
link Claude reads out of a brief's text. A brief is a reposting one when it asks you to post its own
ready-made videos as they are; those come first wherever briefs are ranked."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from clipforge.briefs import store
from clipforge.briefs.models import Brief
from clipforge.campaigns.models import Campaign, Platform, web_url
from clipforge.config import Niche
from clipforge.post import store as post_store
from clipforge.post.models import Account
from clipforge.post.slots import rules_for
from clipforge.today.value import post_estimate
from clipforge.untrusted import clean_lines, clean_text

if TYPE_CHECKING:
    from clipforge.app import Engine

SCOUT_HOSTS = ("contentrewards.com", "whop.com", "vyro.com")  # research 08: Campaign Scout's marketplaces
_READY = ("connected", "token_expiring")
NEWS = "news and politics"
_POLITICAL = {"politics", "political"}  # tags that make a niche political, whatever it is called
_WORD = re.compile(r"[\w']+")
_REPOST = re.compile(r"reposts?|reposting|reposted")
_SENTENCE = re.compile(r"[.!?;\n]+")  # "No AI. Reposting welcome": each sentence is read on its own
_NOT_BEFORE = {  # "no raw reposts", "you cannot repost content"
    "no",
    "not",
    "don't",
    "dont",
    "never",
    "without",
    "nor",
    "can't",
    "cant",
    "cannot",
    "avoid",
    "won't",
    "wont",
    "mustn't",
    "shouldn't",
    "zero",
    "ban",
    "bans",
    "banned",
    "prohibit",
}
_NOT_AFTER = {  # "reposts are not paid", "reposted content will be rejected"
    "not",
    "never",
    "aren't",
    "isn't",
    "won't",
    "banned",
    "prohibited",
    "forbidden",
    "can't",
    "cannot",
    "rejected",
    "disqualified",
    "disallowed",
    "ineligible",
    "denied",
    "removed",
    "unacceptable",
}


@dataclass(frozen=True)
class Worth:
    """What a brief could earn a day, in ``currency``; ``account_id`` is the account that earns it, None
    when none fits."""

    amount: float | None
    currency: str
    guess: bool
    account_id: str | None


def host_of(link: str | None) -> str | None:
    """The link's host in lower case without a leading www., or None when it has none."""
    try:
        host = urlsplit(link or "").hostname
    except ValueError:
        return None
    return host.removeprefix("www.") if host else None


def rate_for(brief: Brief, platform: Platform) -> float | None:
    """The brief's rate per 1,000 views on ``platform``: its own figure for it, else the brief's."""
    return brief.rates.get(platform, brief.rate_per_1000)


def cap_for(brief: Brief, platform: Platform) -> float | None:
    """The most one clip earns on ``platform``: its own figure for it, else the brief's."""
    return brief.caps.get(platform, brief.max_payout)


def stand_in(brief: Brief, platform: Platform) -> Campaign:
    """The brief as a campaign on one platform, for the estimates; never saved."""
    return Campaign(
        id=brief.id,
        name=brief.title[:80].strip() or brief.id,
        marketplace=brief.marketplace,
        currency=brief.currency,
        rate_per_1000=rate_for(brief, platform),
        min_views=brief.min_views,
        max_payout=cap_for(brief, platform),
        platforms=[platform],
        budget_left=brief.budget_left,
        brief_id=brief.id,
        created_at=brief.first_seen_at,
        updated_at=brief.last_seen_at,
    )


def political(niche: Niche) -> bool:
    """News and politics, which TikTok bans paid posts about (10.6)."""
    return niche.name.casefold() == NEWS or bool(_POLITICAL & set(niche.tags))


def reposting(brief: Brief) -> bool:
    """Whether the brief asks for reposts: its title, text or footage says repost (reposts, reposting,
    re-post) in a sentence with no word against it in the 5 words before (no, not, can't, avoid) or the 4
    after (not, rejected, prohibited). A brief wrongly left out only ranks lower, and one wrongly let in
    steers you toward a post it forbids, so a doubtful sentence counts as against."""
    for part in (brief.title, brief.text, brief.footage.kind):
        plain = (part or "").casefold().replace("\u2019", "'").replace("re-post", "repost")
        for sentence in _SENTENCE.split(plain):
            words = _WORD.findall(sentence)
            for at, word in enumerate(words):
                if not _REPOST.fullmatch(word):
                    continue
                if _NOT_BEFORE.isdisjoint(words[max(0, at - 5) : at]) and _NOT_AFTER.isdisjoint(
                    words[at + 1 : at + 5]
                ):
                    return True
    return False


def _niche(engine: Engine, name: str | None) -> Niche | None:
    """The niche called ``name`` (in any case) in Settings, or None."""
    if name:
        for niche in engine.settings.briefs.niches:
            if niche.name.casefold() == name.casefold():
                return niche
    return None


def niche_tags(engine: Engine, name: str | None) -> set[str]:
    """The tags of the niche called ``name`` (in any case) in Settings, or none."""
    niche = _niche(engine, name)
    return set(niche.tags) if niche else set()


def _platforms(engine: Engine, brief: Brief) -> list[Platform]:
    """The platforms the brief may be posted on: those it allows, less TikTok for news and politics."""
    niche = _niche(engine, brief.niche)
    if niche is not None and political(niche):
        return [p for p in brief.platforms if p != "tiktok"]
    return list(brief.platforms)


def fits(engine: Engine, brief: Brief) -> list[Account]:
    """The accounts that could post for the brief, oldest first."""
    tags = niche_tags(engine, brief.niche)
    if not tags:
        return []
    allowed = _platforms(engine, brief)
    return [
        a
        for a in post_store.list_accounts(engine.db())
        if a.status in _READY and not a.paused and a.platform in allowed and tags & set(a.settings.niche_tags)
    ]


def worth(engine: Engine, brief: Brief, fitting: list[Account] | None = None) -> Worth:
    """What the brief could earn a day; ``fitting``, when given, is ``fits(engine, brief)``."""
    if brief.status == "gone":
        return Worth(0.0, brief.currency, False, None)
    best: Worth | None = None
    for account in fits(engine, brief) if fitting is None else fitting:
        estimate = post_estimate(engine, account.id, stand_in(brief, account.platform))
        if estimate.amount is None:
            continue
        amount = estimate.amount * rules_for(engine.settings.posting, account).cap
        if brief.budget_left is not None:
            amount = min(amount, brief.budget_left)
        if best is None or amount > (best.amount or 0.0):
            best = Worth(round(amount, 2), brief.currency, estimate.guess, account.id)
    if best is not None:
        return best
    rated = [p for p in _platforms(engine, brief) if rate_for(brief, p) is not None]
    if not rated:
        return Worth(None, brief.currency, False, None)
    platform = max(rated, key=lambda p: rate_for(brief, p) or 0.0)
    return Worth(post_estimate(engine, None, stand_in(brief, platform)).amount, brief.currency, True, None)


def _hosts(engine: Engine) -> set[str]:
    """The marketplaces ClipForge reads: each source's list page's site, and Campaign Scout's."""
    found = {host_of(s.list_url) for s in store.sources(engine.db())}
    return {h for h in found if h} | set(SCOUT_HOSTS)


def openable_link(engine: Engine, link: str | None) -> bool:
    """Whether ClipForge may open ``link``: https, on a marketplace it reads or a site under one."""
    try:
        checked = web_url(link or "")
    except ValueError:
        return False
    parts = urlsplit(checked)
    host = parts.hostname or ""
    return parts.scheme == "https" and any(host == h or host.endswith(f".{h}") for h in _hosts(engine))


def openable(engine: Engine, brief: Brief) -> bool:
    """Whether ClipForge may open the brief's link (openable_link)."""
    return openable_link(engine, brief.url)


def brief_json(engine: Engine, brief: Brief) -> dict[str, Any]:
    """A brief as the Briefs tab shows it: its words cleaned (Campaign Scout's notes kept apart from them),
    what it is worth and through which account, the accounts that fit it, and its link's id when it opens."""
    fitting = fits(engine, brief)
    can_open = openable(engine, brief)
    footage = brief.footage.model_dump() | {"kind": clean_text(brief.footage.kind)}
    return brief.model_dump(exclude={"text_hash"}) | {
        "url": clean_text(brief.url),
        "brand": clean_text(brief.brand),
        "title": clean_text(brief.title),
        "text": clean_lines(brief.text),
        "notes": {key: clean_text(value) for key, value in brief.notes.items()},
        "footage": footage,
        "worth": asdict(worth(engine, brief, fitting)),
        "fits": [{"id": a.id, "platform": a.platform, "handle": a.handle, "label": a.label} for a in fitting],
        "openable": can_open,
        "link_id": f"brief:{brief.id}" if can_open else None,
        "reposting": reposting(brief),
    }
