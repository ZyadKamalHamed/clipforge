"""The path from nothing to your first payout on Today (SPEC 14.10): six levels of steps in the order that
earns soonest, from notifications and the background service through your first account, campaign, post and
payout to mornings that run themselves. A step is done once ClipForge sees it done or you tick it, and stays
done whatever changes after, so the path never goes back. A step whose steps before it are done joins the
list, worth what the account it leads to could earn a day; one another item stands for, such as Join or
Submit, is left to that item, and one waiting on the world (a decision, a payout) says so.

The first account is a niche one when one niche holds at least 40% of the money TikTok can take in the live
briefs, else a broad one tagged for the richest niches, so either way it fits most of the money. Its name
ideas, bio and picture idea come from fixed templates, never from a brief's words (briefs/names.py). Until
your first post is up, Pick your first campaign stands for the new briefs, which leave the list till then."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from clipforge import service
from clipforge.briefs import names
from clipforge.briefs import store as brief_store
from clipforge.briefs.models import Brief
from clipforge.briefs.worth import political, rate_for, reposting, stand_in
from clipforge.campaigns import store as campaign_store
from clipforge.campaigns.money import money_text, totals
from clipforge.config import Niche
from clipforge.post import store as post_store
from clipforge.today import store
from clipforge.today.models import Need
from clipforge.today.value import comparable, post_estimate
from clipforge.untrusted import clean_text
from clipforge.watch import store as watch_store

if TYPE_CHECKING:
    from clipforge.app import Engine

NICHE_SHARE = 0.4  # one niche with this share of the money gets an account of its own
TAGS_MAX = 10  # an account's niche tags
NAME_MAX = 60  # a brief's name in a step's title
_LIVE = ["new", "seen"]
_READY = ("connected", "token_expiring")
_DECIDED = ["submitted", "approved", "rejected", "paid"]
BROAD_NAMES = ["Late Night Clips", "Clips After Dark", "Daily Clip Drop"]
BROAD_BIO = (
    "The best moments from podcasts, streams and shows, every day."
    " A fan clips page, not affiliated with anyone shown."
)
BROAD_PICTURE = f"a bold two letter monogram on a dark background, {names.NO_LOGO}"


@dataclass(frozen=True)
class Level:
    n: int
    name: str
    goal: str


LEVELS = [
    Level(1, "Set up", "ClipForge can reach you and runs on its own"),
    Level(2, "Your first account", "A TikTok account ClipForge can post to"),
    Level(3, "Your first campaign", "A campaign that pays you for views"),
    Level(4, "Your first post", "Your first clip live and submitted"),
    Level(5, "Your first money", "Views counted, approved and paid out"),
    Level(6, "Your daily machine", "Clips made for you every morning, on more accounts"),
]


@dataclass(frozen=True)
class Step:
    """One step: its words (templates over the words ``_words`` gives), how long it takes, the steps that
    come before it, the account whose day it is worth ("tiktok", "instagram" or "second"), the kind of list
    item that stands for it, and where in ClipForge it is done."""

    id: str
    level: int
    title: str
    detail: str
    estimate_s: int = 60
    after: tuple[str, ...] = ()
    money: str | None = None
    covered_by: str | None = None
    to: str | None = None


STEPS = [
    Step(
        "notify",
        1,
        "Let ClipForge send you notifications",
        "The first time ClipForge sends one, macOS asks whether to allow them: choose Allow. Missed it? "
        "System Settings, "
        "Notifications, ClipForge Notifier, and turn on Allow notifications. Tick this once it's on.",
    ),
    Step(
        "service",
        1,
        "Turn on the background service",
        "Settings, Posting, Background service, Install. It keeps ClipForge running when the window is "
        "closed, so posts go up on time and your list is ready each morning. Then log out and back in once "
        "and check the morning notification still comes.",
        estimate_s=120,
        to="/settings",
    ),
    Step(
        "make_account",
        2,
        "Make {account}",
        "In the TikTok app, sign up for a new account, not your personal one. Name ideas: {names}. Bio: "
        "{bio} Profile picture: {picture}. {why} Tick this once it exists.",
        estimate_s=600,
        money="tiktok",
    ),
    Step(
        "link_account",
        2,
        "Log your new account in to ClipForge",
        "Accounts, Add account, TikTok in Chrome, then Log in with Chrome. A Chrome window opens on "
        "TikTok's own page: choose Use QR code. On your phone, switch the TikTok app to the new account, "
        "scan the code with the scanner inside the TikTok app (your camera app only opens TikTok) and tap "
        "Confirm. Other ways in don't work in that window: Google won't sign in to a browser a program "
        "drives, and TikTok's password login can say there have been too many attempts. ClipForge never "
        "sees your password.",
        estimate_s=180,
        after=("make_account",),
        money="tiktok",
        to="/accounts",
    ),
    Step(
        "check_account",
        2,
        "Check the account from ClipForge",
        "On Accounts, press Check in the account's card. It opens TikTok in Chrome and makes sure ClipForge "
        "can reach the upload page, without posting anything.",
        after=("link_account",),
        money="tiktok",
        to="/accounts",
    ),
    Step(
        "tag_account",
        2,
        "Set its niche tags: {tags}",
        "Accounts, Settings on the account's card, Niche tags: {tags}. They tell ClipForge which briefs "
        "the account can take, so the Briefs tab and this list rank them for you.",
        estimate_s=90,
        after=("link_account",),
        money="tiktok",
        to="/accounts",
    ),
    Step(
        "pick_campaign",
        3,
        "Pick your first campaign{campaign}",
        "Campaigns, Briefs tab: reposting campaigns come first, since posting their ready-made videos is the "
        "quickest money, then the richest, and each says which of your accounts fit. Open the top one, read "
        "its rules, then press Use this campaign, tick I've checked the filled fields and press Save.{best}",
        estimate_s=300,
        after=("tag_account",),
        money="tiktok",
        to="/campaigns?tab=briefs",
    ),
    Step(
        "join_campaign",
        3,
        "Join it on the marketplace",
        "Sign up on the campaign's own page (Whop, Vyro or the like) yourself, then press I've joined in "
        "ClipForge. Your list shows it as Join until then.",
        after=("pick_campaign",),
        covered_by="join",
        to="/campaigns",
    ),
    Step(
        "first_clips",
        4,
        "Make clips from your campaign's footage",
        "Create: paste a YouTube link the campaign's rules allow, then Start. When the project opens, pick "
        "your campaign in its header, so its clips follow the rules and count for it. A few minutes later "
        "it shows its clips, the best first.",
        estimate_s=180,
        after=("pick_campaign",),
        money="tiktok",
        to="/create",
    ),
    Step(
        "first_post",
        4,
        "Post your best clip, assisted",
        "Turn on Assisted posting in the account's Settings. In the project, tick the best clip, Post, "
        "pick your TikTok account, then Confirm and post. ClipForge fills TikTok in with TikTok's own "
        "schedule set to the post's time, and you press Schedule in TikTok yourself (Post, when it's due "
        "now). This ticks once it's live.",
        estimate_s=300,
        after=("first_clips", "check_account"),
        money="tiktok",
        to="/projects",
    ),
    Step(
        "submit_post",
        4,
        "Submit the post to the campaign",
        "Once it's live, your list says Submit: Copy link and open the marketplace, paste the link on "
        "the campaign's page and submit it there, then press Mark submitted in ClipForge.",
        after=("first_post", "join_campaign"),
        covered_by="submit",
        to="/campaigns",
    ),
    Step(
        "enter_views",
        5,
        "Enter your post's views",
        "Two and seven days after a post goes up, your list asks for its views. Type the number TikTok "
        "shows: it is how ClipForge works out what the post earned.",
        after=("first_post",),
        covered_by="views",
    ),
    Step(
        "first_approved",
        5,
        "Get your first approval",
        "The marketplace takes a day or so to decide. When your list says Record, look on the marketplace "
        "and press Approved or Rejected in the post's row on Today. A rejection says why: fix that on the "
        "next post.",
        after=("submit_post",),
        covered_by="decide",
    ),
    Step(
        "first_payout",
        5,
        "Withdraw your first payout",
        "When the approved money reaches the marketplace's minimum, your list says Payout ready. Withdraw "
        "on the marketplace, then press I've been paid in Money on Today.",
        after=("first_approved",),
        covered_by="payout",
    ),
    Step(
        "clip_channel",
        6,
        "Set up a clipping channel",
        "Channels, New clipping channel: pick YouTube channels your campaigns allow, and your TikTok "
        "account. Every morning it clips their new videos and the drafts wait for you on Today.",
        estimate_s=300,
        after=("tag_account",),
        money="tiktok",
        to="/channels",
    ),
    Step(
        "instagram",
        6,
        "Make it on Instagram too, and link it through Facebook",
        "Make the same account on Instagram ({first_name} or another of the names), switch it to a "
        "professional account and connect it to a Facebook Page. Then Accounts, Add account, Instagram "
        "through Facebook: the Instagram guide shows how to get the token. Use Instagram login only if "
        "the Facebook way won't do.",
        estimate_s=900,
        after=("make_account",),
        money="instagram",
        to="/accounts",
    ),
    Step(
        "first_reel",
        6,
        "Post your first Reel, then look at it logged out",
        "Post a clip to the Instagram account the way you did on TikTok. Then open it in a private window, "
        "logged out, to see that everyone can.",
        estimate_s=300,
        after=("instagram",),
        money="instagram",
        to="/projects",
    ),
    Step(
        "ig_limit",
        6,
        "Note Instagram's live posting limit",
        "Before each Reel ClipForge asks Meta how many posts the account may make in 24 hours. Keep the "
        "account's own daily cap (its Settings) well under that number. Tick this once you've looked.",
        after=("first_reel",),
        to="/accounts",
    ),
    Step(
        "trending_reel",
        6,
        "Post a Reel with a trending sound",
        "Trends lists the sounds Instagram lets you use today. In the Post dialog's Details, pick one for "
        "the post, to see whether Meta takes it.",
        estimate_s=300,
        after=("first_reel",),
        money="instagram",
        to="/trends",
    ),
    Step(
        "trial_reel",
        6,
        "Post a trial reel",
        "In the Post dialog's Details, turn on Trial reel: Instagram shows it first to people who don't "
        "follow you, so a miss costs your followers nothing.",
        estimate_s=300,
        after=("first_reel",),
        money="instagram",
        to="/projects",
    ),
    Step(
        "second_account",
        6,
        "Make a second TikTok account{second}",
        "A second account doubles the posts you can put up a day.{second_why} Link it, Check it and tag it "
        "like the first.",
        estimate_s=600,
        after=("first_post",),
        money="second",
        to="/accounts",
    ),
    Step(
        "full_morning",
        6,
        "Do one full morning in 15 minutes",
        "From the morning notification to the end of your list: drafts, posts, submits and views. Tick "
        "this once you've done it.",
        after=("clip_channel", "first_post"),
    ),
]
IDS = {step.id for step in STEPS}


def _accounts(engine: Engine) -> list:
    return post_store.list_accounts(engine.db())


def _posted(engine: Engine, platform: str | None = None) -> list:
    return post_store.list_posts(engine.db(), status=["posted"], platform=platform)


def _checked(engine: Engine, account: Any) -> bool:
    """Whether a Check of the account ran to the end and found it connected; a login doesn't count."""
    return any(
        job.kind == "account.selftest"
        and job.status == "done"
        and (job.result or {}).get("status") == "connected"
        for job in engine.runner.jobs_for(account.id)
    )


def _for_a_campaign(engine: Engine) -> bool:
    """Whether a project is linked to a campaign."""
    conn = engine.db()
    return any(campaign_store.linked_projects(conn, c.id) for c in campaign_store.list_campaigns(conn))


# How ClipForge sees a step done; the others only you can tick.
SEEN: dict[str, Callable[[Engine], bool]] = {
    "service": lambda e: service.plist_path(service.AGENTS).exists(),
    "make_account": lambda e: any(a.platform == "tiktok" for a in _accounts(e)),
    "link_account": lambda e: any(a.status in _READY for a in _accounts(e)),
    "check_account": lambda e: any(a.platform == "tiktok" and _checked(e, a) for a in _accounts(e)),
    "tag_account": lambda e: any(a.settings.niche_tags for a in _accounts(e)),
    "pick_campaign": lambda e: bool(campaign_store.list_campaigns(e.db())),
    "join_campaign": lambda e: any(
        c.joined_at is not None or c.brief_id is None for c in campaign_store.list_campaigns(e.db())
    ),
    "first_clips": _for_a_campaign,
    "first_post": lambda e: bool(campaign_store.list_submissions(e.db())),  # live, for a campaign
    "submit_post": lambda e: bool(campaign_store.list_submissions(e.db(), status=_DECIDED)),
    "enter_views": lambda e: campaign_store.any_metric(e.db()),
    "first_approved": lambda e: bool(campaign_store.list_submissions(e.db(), status=["approved", "paid"])),
    "first_payout": lambda e: bool(campaign_store.list_submissions(e.db(), status=["paid"])),
    "clip_channel": lambda e: bool(watch_store.list_channels(e.db())),
    "instagram": lambda e: any(a.platform == "instagram" for a in _accounts(e)),
    "first_reel": lambda e: bool(_posted(e, "instagram")),
    "trending_reel": lambda e: any(p.sound is not None for p in _posted(e, "instagram")),
    "trial_reel": lambda e: any(p.trial_reel for p in _posted(e, "instagram")),
    "second_account": lambda e: sum(a.platform == "tiktok" for a in _accounts(e)) >= 2,
}


@dataclass(frozen=True)
class Plan:
    """The first account to make: niche or broad, its name ideas, bio, picture idea and niche tags, the
    niche with the most money and its share of it, the niche a second account would be for, and what
    each could earn a day in US$ at the starting guess."""

    broad: bool
    niche: str | None
    names: list[str]
    bio: str
    picture: str
    tags: list[str]
    top: str | None
    share: float
    second: str | None
    tiktok: float
    instagram: float
    second_day: float


@dataclass(frozen=True)
class State:
    """A step as it stands: done, todo (on the list), listed (another item stands for it), waiting (on
    something that isn't on the list yet) or locked (a step before it isn't done); how it was done."""

    step: Step
    state: str
    how: str | None

    @property
    def id(self) -> str:
        return self.step.id


def on(engine: Engine) -> bool:
    return engine.settings.today.guide


def done_how(engine: Engine) -> dict[str, str]:
    """Each done step and how: "app" when ClipForge saw it, "hand" when you ticked it."""
    return store.path_done(engine.db())


def record(engine: Engine, step_id: str, how: str) -> None:
    """The step done for good; one done already keeps how it was. Make your first account keeps the
    account it was planned as."""
    if step_id not in IDS:
        raise LookupError(f"There is no step {step_id} on the path.")
    kept = None
    if step_id == "make_account":
        first = plan(engine)
        kept = {name: getattr(first, name) for name in KEPT}
    store.put_path_step(engine.db(), step_id, engine.now().timestamp(), how, kept)


def steps(engine: Engine, kinds: set[str]) -> list[State]:
    """Every step as it stands, in order, with the ones ClipForge sees done now recorded; ``kinds`` are the
    kinds of the other items on the list."""
    done = done_how(engine)
    for step in STEPS:
        seen = SEEN.get(step.id)
        if step.id not in done and seen is not None and seen(engine):
            record(engine, step.id, "app")
            done[step.id] = "app"
    found = []
    for step in STEPS:
        if step.id in done:
            state = "done"
        elif any(before not in done for before in step.after):
            state = "locked"
        elif step.covered_by is not None:
            state = "listed" if step.covered_by in kinds else "waiting"
        else:
            state = "todo"
        found.append(State(step, state, done.get(step.id)))
    return found


def _live(engine: Engine) -> list[tuple[Brief, Niche]]:
    """The live briefs in a niche Settings lists, with their niche."""
    niches = {n.name.casefold(): n for n in engine.settings.briefs.niches}
    found = []
    for brief in brief_store.briefs(engine.db(), status=_LIVE):
        niche = niches.get((brief.niche or "").casefold())
        if niche is not None:
            found.append((brief, niche))
    return found


def _per_post(engine: Engine, brief: Brief, niche: Niche, platform: str) -> float | None:
    """What one post on ``platform`` could earn on the brief at the starting guess, in US$; None when it
    can't go there or has no rate."""
    if platform not in brief.platforms or (platform == "tiktok" and political(niche)):
        return None
    if rate_for(brief, platform) is None:  # type: ignore[arg-type]
        return None
    estimate = post_estimate(engine, None, stand_in(brief, platform))  # type: ignore[arg-type]
    if estimate.amount is None:
        return None
    return comparable(engine.settings, estimate.amount, estimate.currency)[0]


def _day(engine: Engine, live: list[tuple[Brief, Niche]], platform: str, tags: list[str], cap: int) -> float:
    """What an account on ``platform`` tagged ``tags`` could earn a day: its ``cap`` best posts on the live
    briefs it fits."""
    amounts = []
    for brief, niche in live:
        if set(niche.tags) & set(tags):
            amount = _per_post(engine, brief, niche, platform)
            if amount:
                amounts.append(amount)
    return round(sum(sorted(amounts, reverse=True)[:cap]), 2)


def _first_tags(niches: list[Niche]) -> list[str]:
    tags: list[str] = []
    for niche in niches:
        if niche.tags[0] not in tags:
            tags.append(niche.tags[0])
    return tags[:TAGS_MAX]


KEPT = ("broad", "niche", "names", "bio", "picture", "tags")  # the first account, once it's made


def plan(engine: Engine) -> Plan:
    """The first account to make, from the money TikTok can take in the live briefs, or as it was planned
    once it's made; the second always from the live briefs."""
    live = _live(engine)
    niches = engine.settings.briefs.niches
    money: dict[str, float] = defaultdict(float)
    for brief, niche in live:
        money[niche.name] += _per_post(engine, brief, niche, "tiktok") or 0.0
    rich = sorted((n for n in niches if money[n.name] > 0), key=lambda n: -money[n.name])
    total = sum(money.values())
    top = rich[0] if rich else None
    share = money[top.name] / total if top else 0.0
    kept = store.path_plan(engine.db(), "make_account")
    if kept is not None:
        words: dict[str, Any] = kept
    elif top is not None and share >= NICHE_SHARE:
        words = {
            "broad": False,
            "niche": top.name,
            "names": names.ideas(top),
            "bio": names.bio(top),
            "picture": names.picture(top),
            "tags": top.tags[:TAGS_MAX],
        }
    else:
        pool = rich or [n for n in niches if not political(n)]
        words = {
            "broad": True,
            "niche": None,
            "names": BROAD_NAMES,
            "bio": BROAD_BIO,
            "picture": BROAD_PICTURE,
            "tags": _first_tags(pool),
        }
    # a broad account's next is the richest niche; a niche account's, the richest other one
    second = top if words["broad"] else next((n for n in rich if n.name != words["niche"]), None)
    posting = engine.settings.posting
    return Plan(
        **words,
        top=top.name if top else None,
        share=round(share, 2),
        second=second.name if second else None,
        tiktok=_day(engine, live, "tiktok", words["tags"], posting.tiktok_daily_cap),
        instagram=_day(engine, live, "instagram", words["tags"], posting.instagram_daily_cap),
        second_day=_day(engine, live, "tiktok", second.tags if second else [], posting.tiktok_daily_cap),
    )


def _a(word: str) -> str:
    return f"an {word}" if word[:1].lower() in "aeiou" else f"a {word}"


def _best(engine: Engine, live: list[tuple[Brief, Niche]], tags: list[str]) -> tuple[Brief, float] | None:
    """The live brief an account tagged ``tags`` would earn most a post on TikTok, a reposting one ahead of
    the rest, and what it earns, in US$."""
    found = [
        (brief, amount)
        for brief, niche in live
        if set(niche.tags) & set(tags) and (amount := _per_post(engine, brief, niche, "tiktok"))
    ]
    return max(found, key=lambda pair: (reposting(pair[0]), pair[1])) if found else None


def _tags(engine: Engine, first: Plan) -> list[str]:
    """The tags your accounts have, or the first account's to be when none has any."""
    held = sorted({t for a in _accounts(engine) for t in a.settings.niche_tags})
    return held or first.tags


def _words(engine: Engine, first: Plan, live: list[tuple[Brief, Niche]]) -> dict[str, str]:
    """What the steps' templates are filled in with."""
    pct = round(first.share * 100)
    if first.broad:
        account = f"a broad clips account on TikTok, like {first.names[0]}"
        why = (
            f"Why broad: the money in the live briefs is spread over many niches (the most, {first.top}, has "
            f"{pct}% of it), so one account posting the best of each can take most of it."
            if first.top
            else "Why broad: there are no live briefs yet to pick a niche from, so start with an account "
            "that can take any of them."
        )
    else:
        account = f"{_a((first.niche or '').lower())} clips account on TikTok"
        why = (
            f"Why {(first.niche or '').lower()}: it holds {pct}% of the money in the live briefs, so an "
            "account just for it can take most of it."
        )
    best = _best(engine, live, _tags(engine, first))
    campaign, about = "", ""
    if best is not None:
        brief, _ = best
        name = (clean_text(brief.brand or brief.title) or "").strip()[:NAME_MAX]
        campaign = f": {name}" if name else ""
        amount = post_estimate(engine, None, stand_in(brief, "tiktok")).amount or 0.0
        about = f" The best for you now pays about {money_text(amount, brief.currency)} a post at a guess."
    second = f" for {first.second.lower()} clips" if first.second else ""
    second_why = (
        f" {first.second} has the most money your first account doesn't post for." if first.second else ""
    )
    if first.second and first.broad:
        second_why = f" {first.second} has the most money in the live briefs: an account just for it."
    return {
        "account": account,
        "names": ", ".join(first.names),
        "first_name": first.names[0],
        "bio": first.bio,
        "picture": first.picture,
        "why": why,
        "tags": ", ".join(first.tags),
        "campaign": campaign,
        "best": about,
        "second": second,
        "second_why": second_why,
    }


def _values(engine: Engine, first: Plan, live: list[tuple[Brief, Niche]]) -> dict[str, float]:
    """What each step's account could earn a day: the first account's on your accounts' tags once they
    have some."""
    tags = _tags(engine, first)
    posting = engine.settings.posting
    if tags == first.tags:
        return {"tiktok": first.tiktok, "instagram": first.instagram, "second": first.second_day}
    return {
        "tiktok": _day(engine, live, "tiktok", tags, posting.tiktok_daily_cap),
        "instagram": _day(engine, live, "instagram", tags, posting.instagram_daily_cap),
        "second": first.second_day,
    }


def _done(states: list[State], step_id: str) -> bool:
    return any(s.id == step_id and s.state == "done" for s in states)


def walk(engine: Engine, found: list[Need]) -> list[Need]:
    """``found`` with the path's steps that are on the list now, and, until your first post is up, without
    the new briefs, which Pick your first campaign stands for till then."""
    if not on(engine):
        return found
    states = steps(engine, {n.kind for n in found})
    if not _done(states, "first_post"):
        found = [n for n in found if n.kind != "briefs"]
    todo = [s for s in states if s.state == "todo"]
    if not todo:
        return found
    live = _live(engine)
    first = plan(engine)
    words = _words(engine, first, live)
    values = _values(engine, first, live)
    for s in todo:
        value = values.get(s.step.money or "", 0.0)
        found.append(
            Need(
                "path",
                s.id,
                [s.id],
                s.step.title.format(**words),
                detail=s.step.detail.format(**words),
                value=value,
                guess=value > 0,
                estimate_s=s.step.estimate_s,
            )
        )
    return found


def _metrics(engine: Engine) -> dict[str, Any]:
    """Your accounts, live posts and money paid so far, in US$ through your exchange rates."""
    earned = sum(comparable(engine.settings, line["paid"], line["currency"])[0] for line in totals(engine))
    return {
        "accounts": len(_accounts(engine)),
        "posts": len(_posted(engine)),
        "earned": round(earned, 2),
        "currency": "USD",
    }


def view(engine: Engine, kinds: set[str]) -> dict[str, Any]:
    """The path as Today shows it: the level you are on (None once every step is done), each level's
    steps as they stand, how many are done, your numbers so far and the first account to make."""
    if not on(engine):
        return {"on": False}
    states = steps(engine, kinds)
    live = _live(engine)
    first = plan(engine)
    words = _words(engine, first, live)
    level = next((s.step.level for s in states if s.state != "done"), None)
    return {
        "on": True,
        "level": level,
        "done": sum(s.state == "done" for s in states),
        "total": len(states),
        "levels": [
            {
                "n": lv.n,
                "name": lv.name,
                "goal": lv.goal,
                "steps": [
                    {
                        "id": s.id,
                        "title": s.step.title.format(**words),
                        "detail": s.step.detail.format(**words),
                        "state": s.state,
                        "done_how": s.how,
                        "to": s.step.to,
                    }
                    for s in states
                    if s.step.level == lv.n
                ],
            }
            for lv in LEVELS
        ],
        "metrics": _metrics(engine),
        "account": {
            "broad": first.broad,
            "niche": first.niche,
            "names": first.names,
            "bio": first.bio,
            "picture": first.picture,
            "tags": first.tags,
            "per_day": first.tiktok,
            "currency": "USD",
        },
    }
