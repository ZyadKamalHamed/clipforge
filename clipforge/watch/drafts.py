"""A check's drafts (SPEC 10.9). Once the check has finished, every project it started is ready, failed or
refused and their clips have rendered, the best clips across them that pass QA are planned to the clipping
channel's accounts as the Post dialog would (Spread, smart schedule), up to what those accounts can post in
a day, and saved as drafts. A draft takes no slot and has no job: nothing posts until you approve it, which
the Post dialog's review step and Confirm do with the draft ids. Discard puts drafts aside and Undo brings
them back."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from clipforge import repo
from clipforge.campaigns import store as campaign_store
from clipforge.campaigns.rules import campaign_of
from clipforge.db import transaction
from clipforge.post import lifecycle, queue, store
from clipforge.post.distribute import PLATFORMS, clean_hashtags, spread
from clipforge.post.models import PlanRequest
from clipforge.post.plan import MAX_HASHTAGS, build_plan
from clipforge.post.service import post_from_plan
from clipforge.post.slots import rules_for
from clipforge.render.qa import QAResult
from clipforge.today import store as today_store
from clipforge.today.value import comparable, post_estimate
from clipforge.watch import store as watch_store

if TYPE_CHECKING:
    from clipforge.app import Engine
    from clipforge.campaigns.models import Campaign
    from clipforge.models import ClipRow, Project
    from clipforge.post.models import Account, Post
    from clipforge.watch.models import ClipChannel, WatchItem

SETTLED = ("ready", "failed", "refused")  # a project that has finished, one way or the other
GONE = "This draft was already approved or removed."
ACCOUNT_GONE = "Its account was removed, so the draft was discarded."
CLIP_GONE = "Its clip was deleted, so the draft was discarded."
TRIMMED = "Trimmed to keep today inside 15 minutes"


def plan_id(check_id: str) -> str:
    """The plan, and so the batch, a check's drafts are saved under: one per check."""
    return f"draft:{check_id}"


def _accounts(engine: Engine, channel: ClipChannel) -> list[Account]:
    """The clipping channel's accounts that are still there and aren't paused, in its order, on the
    platforms its campaign allows when it has one."""
    conn = engine.db()
    campaign = campaign_store.get_campaign(conn, channel.campaign_id) if channel.campaign_id else None
    found = (store.get_account(conn, aid) for aid in channel.account_ids)
    return [
        a
        for a in found
        if a is not None and not a.paused and (campaign is None or a.platform in campaign.platforms)
    ]


def day_capacity(engine: Engine, channel: ClipChannel) -> dict[str, int]:
    """How many posts a day the clipping channel's accounts can take, per platform, under their caps."""
    posting = engine.settings.posting
    capacity: dict[str, int] = {}
    for account in _accounts(engine, channel):
        capacity[account.platform] = capacity.get(account.platform, 0) + rules_for(posting, account).cap
    return capacity


def day_room(engine: Engine, channel: ClipChannel) -> int:
    """The most drafts one platform takes from a check: its accounts' caps, or Drafts a day when lower."""
    most = max(day_capacity(engine, channel).values(), default=0)
    return most if channel.clips_per_day is None else min(most, channel.clips_per_day)


def settle(engine: Engine, project_id: str) -> None:
    """The runner's hook after a job of a project, or of one of its clips: when a check started the
    project, its drafts are made once everything the check started has finished."""
    conn = engine.db()
    project = repo.get_project(conn, project_id)
    if project is None or project.clip_channel_id is None:
        return
    item = watch_store.item_of_project(conn, project_id)
    if item is not None and item.check_id is not None:
        settle_check(engine, item.check_id)


def settle_check(engine: Engine, check_id: str) -> None:
    """Makes the check's drafts once it has finished, each project it started is ready, failed or refused,
    and none of their clips has a job still to run. Nothing when they were made already."""
    conn = engine.db()
    with engine.lock:
        check = watch_store.get_check(conn, check_id)
        if check is None or check.finished_at is None or check.drafted_at is not None:
            return
        projects = [p for _, p in _clipped(engine, check_id) if p is not None]
        if any(p.status not in SETTLED for p in projects) or _rendering(engine, [p.id for p in projects]):
            return
        make_drafts(engine, check_id)


def _clipped(engine: Engine, check_id: str) -> list[tuple[WatchItem, Project | None]]:
    """The check's items that became projects, each with its project (None once it was deleted)."""
    conn = engine.db()
    items = [i for i in watch_store.items_of_check(conn, check_id) if i.status == "clipping"]
    return [(i, repo.get_project(conn, i.project_id) if i.project_id else None) for i in items]


def _rendering(engine: Engine, project_ids: list[str]) -> bool:
    """Whether a clip of one of the projects has a job queued or running."""
    if not project_ids:
        return False
    marks = ", ".join("?" for _ in project_ids)
    row = (
        engine.db()
        .execute(
            "SELECT 1 FROM jobs WHERE kind LIKE 'clip.%' AND status IN ('queued', 'running')"
            f" AND json_extract(payload_json, '$.project') IN ({marks}) LIMIT 1",
            project_ids,
        )
        .fetchone()
    )
    return row is not None


def _qa(engine: Engine, clip: ClipRow) -> QAResult | None:
    render = repo.get_render(engine.db(), clip.current_render_id) if clip.current_render_id else None
    return QAResult.model_validate(render.qa) if render is not None else None


def _best(engine: Engine, projects: list[Project]) -> list[tuple[ClipRow, QAResult]]:
    """The projects' clips that weren't discarded and have a render, each with its QA, by score, highest
    first (rank, then the order the check started them, breaking ties)."""
    found = []
    for project in projects:
        for clip in engine.clips(project.id):
            qa = _qa(engine, clip) if clip.discarded_at is None else None
            if qa is not None:
                found.append((clip, qa))
    return sorted(found, key=lambda cq: (cq[0].final_score is None, -(cq[0].final_score or 0.0)))


def _pairs(
    channel: ClipChannel,
    accounts: list[Account],
    capacity: dict[str, int],
    best: list[tuple[ClipRow, QAResult]],
    linked: set[str],
) -> list[tuple[str, str]]:
    """For each platform, its top clips that pass QA there, as many as its accounts can take in a day (or
    the clipping channel's clips per day, when lower), spread over its accounts, the ``linked`` projects'
    clips as their campaign's. TikTok's first."""
    pairs: list[tuple[str, str]] = []
    for platform in PLATFORMS:
        ring = [a for a in accounts if a.platform == platform]
        room = capacity.get(platform, 0)
        if channel.clips_per_day is not None:
            room = min(room, channel.clips_per_day)
        fit = [clip for clip, qa in best if qa.passed_for(platform)][:room]
        # Spread takes the best first by rank, and these come from several projects: rank them by score
        pairs += spread([c.model_copy(update={"rank": n}) for n, c in enumerate(fit, 1)], ring, linked=linked)
    return pairs


def make_drafts(engine: Engine, check_id: str) -> list[Post]:
    """Plans the check's best clips to its clipping channel's accounts and saves them as drafts in one
    batch; its items go drafted (their project was ready) or failed, and the check gets its count of
    drafts. Once made, the same drafts are given back."""
    conn = engine.db()
    pid = plan_id(check_id)
    with engine.lock:
        done = store.batch_for_plan(conn, pid)
        if done is not None:
            return store.list_posts(conn, batch_id=done)
        check = watch_store.get_check(conn, check_id)
        if check is None:
            raise LookupError(check_id)
        channel = watch_store.get_channel(conn, check.clip_channel_id)
        if channel is None:
            raise LookupError(check.clip_channel_id)
        if check.drafted_at is not None:
            return []
        clipped = _clipped(engine, check_id)
        ready = [p for _, p in clipped if p is not None and p.status == "ready"]
        accounts = _accounts(engine, channel)
        best = _best(engine, ready)
        campaigns = {p.id: campaign_of(engine, p) for p in ready}
        linked = {project_id for project_id, campaign in campaigns.items() if campaign is not None}
        project_of = {clip.id: clip.project_id for clip, _ in best}
        pairs = _pairs(channel, accounts, day_capacity(engine, channel), best, linked)
        now = engine.now()
        with transaction(conn):
            posts: list[Post] = []
            if pairs:
                req = PlanRequest(
                    clip_ids=list(dict.fromkeys(c for c, _ in pairs)),
                    account_ids=list(dict.fromkeys(a for _, a in pairs)),
                    mode="spread",
                    timing="smart",
                )
                plan = build_plan(engine, req, plan_id=pid, now=now, pairs=pairs, hold_drafts=True)
                bid = store.add_batch(conn, pid, "spread", "smart", {})
                for planned in plan.posts:
                    campaign = campaigns[project_of[planned.clip_id]]
                    required = campaign.required.hashtags if campaign is not None else []
                    tags = clean_hashtags([*required, *channel.options.hashtags, *planned.hashtags])
                    planned = planned.model_copy(
                        update={"hashtags": tags[: MAX_HASHTAGS.get(planned.platform)]}
                    )
                    posts.append(post_from_plan(conn, bid, planned, status="draft"))
                store.add_posts(conn, posts)
            for item, project in clipped:
                ok = project is not None and project.status == "ready"
                watch_store.update_item(conn, item.id, status="drafted" if ok else "failed")
            watch_store.update_check(conn, check_id, drafts=len(posts), drafted_at=now.timestamp())
        for post in posts:
            queue.post_event(engine, post)
        engine.events.publish(
            "watch", {"id": check_id, "status": "drafted", "drafts": len(posts), "name": channel.name}
        )
        engine.events.publish("clip_channel", {"id": channel.id})
        day = now.astimezone(ZoneInfo(engine.settings.posting.timezone)).date().isoformat()
        if posts and not trim(engine, day):  # a trim tells the count itself
            publish_count(engine)
        return posts


def campaign_of_draft(engine: Engine, post: Post) -> Campaign | None:
    """A draft's campaign is its project's, as its review card shows, unless it was taken off it."""
    if post.state.get("off_campaign") or post.clip_id is None:
        return None
    clip = repo.get_clip(engine.db(), post.clip_id)
    return campaign_of(engine, repo.get_project(engine.db(), clip.project_id) if clip else None)


def _made_on(engine: Engine, day: str) -> list[Post]:
    """The drafts the checks made on ``day`` that still wait, less any you brought back after a trim."""
    conn = engine.db()
    zone = ZoneInfo(engine.settings.posting.timezone)
    first = date.fromisoformat(day)
    start = datetime.combine(first, time(0), zone).timestamp()
    end = datetime.combine(first + timedelta(days=1), time(0), zone).timestamp()
    made: list[Post] = []
    for check in watch_store.drafted_between(conn, start, end):
        batch = store.batch_for_plan(conn, plan_id(check.id))
        if batch is not None:
            made += store.list_posts(conn, status=["draft"], batch_id=batch)
    return [p for p in made if not p.state.get("trimmed")]


def trim(engine: Engine, day: str) -> list[Post]:
    """Keeps the drafts the checks made on ``day`` to the day's drafts cap across every clipping channel:
    the rest, lowest estimate first and then lowest score, are discarded with the reason, and can be
    brought back like any discarded draft. The ones trimmed, none without a cap."""
    conn = engine.db()
    with engine.lock:
        plan = today_store.get_day(conn, day)
        made = _made_on(engine, day)
        if plan is None or plan.drafts_cap is None or len(made) <= plan.drafts_cap:
            return []

        def worth(post: Post) -> tuple[float, float]:
            campaign = campaign_of_draft(engine, post)
            guess = post_estimate(engine, post.account_id, campaign) if campaign is not None else None
            usd = comparable(engine.settings, guess.amount or 0.0, guess.currency)[0] if guess else 0.0
            clip = repo.get_clip(conn, post.clip_id) if post.clip_id else None
            score = clip.final_score if clip is not None else None
            return (usd, score or 0.0)

        ranked = sorted(made, key=worth, reverse=True)
        cut = ranked[plan.drafts_cap :]
        with transaction(conn):
            trimmed = [
                lifecycle.move(conn, p.id, "discarded", note=TRIMMED, state=p.state | {"trimmed": True})
                for p in cut
            ]
        for post in trimmed:
            queue.post_event(engine, post)
        if trimmed:
            publish_count(engine)
        return trimmed


def live_drafts(engine: Engine) -> list[Post]:
    """The drafts to review, by time, each with its clip's current render. A draft whose account or clip
    was deleted is discarded with a note saying so; one whose clip was put aside waits with it."""
    conn = engine.db()
    live: list[Post] = []
    with engine.lock:
        for post in store.list_posts(conn, status=["draft"]):
            clip = repo.get_clip(conn, post.clip_id) if post.clip_id else None
            if store.get_account(conn, post.account_id) is None:
                queue.move_post(engine, post.id, "discarded", note=ACCOUNT_GONE)
            elif clip is None:
                queue.move_post(engine, post.id, "discarded", note=CLIP_GONE)
            elif clip.discarded_at is None:
                if post.render_id != clip.current_render_id:  # rendered again since it was drafted
                    post = store.update_post(conn, post.id, render_id=clip.current_render_id)
                live.append(post)
    return live


def publish_count(engine: Engine) -> None:
    """The ``drafts`` event, with how many drafts wait for review, for Today's badge in the rail."""
    engine.events.publish("drafts", {"count": len(live_drafts(engine))})


def of_channel(engine: Engine, channel_id: str) -> list[Post]:
    """The clipping channel's drafts, those hidden with a clip that was put aside among them."""
    conn = engine.db()
    mine = []
    for post in store.list_posts(conn, status=["draft"]):
        clip = repo.get_clip(conn, post.clip_id) if post.clip_id else None
        project = repo.get_project(conn, clip.project_id) if clip else None
        if project is not None and project.clip_channel_id == channel_id:
            mine.append(post)
    return mine


def discard(engine: Engine, ids: list[str]) -> list[Post]:
    """The drafts put aside, all of them or, when one was approved or removed already, none: LookupError
    with the sentence. One already discarded stays so."""
    return _move(engine, ids, "draft", "discarded")


def restore(engine: Engine, ids: list[str]) -> list[Post]:
    """Undo: the discarded drafts back, all of them or none, as ``discard``. One already back stays so."""
    return _move(engine, ids, "discarded", "draft", note=None)


def _move(engine: Engine, ids: list[str], old: str, new: str, **fields) -> list[Post]:
    conn = engine.db()
    with engine.lock:
        posts = [store.get_post(conn, pid) for pid in dict.fromkeys(ids)]
        if any(p is None or p.status not in (old, new) for p in posts):
            raise LookupError(GONE)
        with transaction(conn):
            moved = [p if p.status == new else lifecycle.move(conn, p.id, new, **fields) for p in posts]
        for post in moved:
            queue.post_event(engine, post)
        publish_count(engine)
        return moved
