"""The posting queue (SPEC 10.6, 10.8): one job per post on its way, run on a post lane at the post's time,
and what each publisher's answer does to the post. ``sync_jobs`` makes the jobs match the posts whenever
either may have changed; ``run_post`` is the job."""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from clipforge import repo
from clipforge.campaigns import tracker
from clipforge.campaigns.rules import RENDER_RULES, broken_rule, campaign_of_post
from clipforge.errors import WaitUntil
from clipforge.jobs.pipeline import CLIP_FUNCTIONS, POST_KINDS
from clipforge.jobs.runner import Job, JobContext
from clipforge.jobs.stages.clips import CANCELLED
from clipforge.models import ClipRow
from clipforge.post import lifecycle, store
from clipforge.post.errors import Fatal, NeedsUser, Paused, Retryable
from clipforge.post.export import NO_VIDEO, caption_for, playable, render_for, video_for
from clipforge.post.models import ACTIVE, Account, Post, PublishResult
from clipforge.post.plan import LOOK_AHEAD, LOOK_BACK, TAKEN, at_text
from clipforge.post.publisher import Report, publisher_for
from clipforge.post.slots import Rules, rules_for, smart_slot, studio_round, studio_slot, sydney_day
from clipforge.post.variants import (
    VARIANT_FAILED,
    count_shown,
    needs_variant,
    queue_variant,
    variant_for,
    variant_ref,
)
from clipforge.untrusted import clean_text
from clipforge.watch import schedule

if TYPE_CHECKING:
    from clipforge.app import Engine

log = logging.getLogger(__name__)

LATE = timedelta(minutes=15)  # later than this, a post whose window has closed moves to the next free slot
AWAKE_GAP = timedelta(minutes=3)  # posting ticks further apart than this had the Mac asleep between them
RENDER_WAIT_S = 30.0
CHECK_AFTER = timedelta(minutes=10)  # TikTok Studio's posts list is read this long after it put a post up
TO_GO = ("scheduled", "rendering", "uploading", "processing", "needs_you", "failed")  # not up yet
HELD = ("scheduled", "scheduled_on_tiktok")  # the posts that wait for a time: their job is queued for it
BACKOFF_S = (60.0, 300.0, 900.0)  # an API post worth trying again is, after 1, 5 and 15 minutes
SITE = {"tiktok": "TikTok Studio", "instagram": "Instagram"}

ACCOUNT_DELETED = "The account was removed, so this post can't go out."
CLIP_DELETED = "The clip was deleted, so this post can't go out."
RENDERING = "Waiting for the clip to finish rendering."
WAITING = "Waiting for its time to post."
ASLEEP = "This Mac was asleep at {old}, so it moved to {new}."
CLOSED = "ClipForge wasn't open at {old}, so it moved to {new}."
PAUSED_AT = "Posting was paused at {old}, so it moved to {new}."
NO_SLOT = "This post missed its time and no slot is free in the next 14 days. Pick a new time for it."
STOPPED = "ClipForge stopped while this post was uploading. Check {site}, then press Retry or Mark as posted."
SOMETHING = "Something went wrong while posting. Check {site}, then press Retry or Mark as posted."
PAUSED_NOTE = "Paused before posting."
NO_ROW = (
    "TikTok's posts list didn't show this post yet. "
    "Check TikTok Studio, then mark it as posted with its link."
)


def _utc(t: float) -> datetime:
    return datetime.fromtimestamp(t, UTC)


def job_kind(post: Post) -> str:
    return "post.api" if post.method == "api" else "post.browser"


def left_to_you(engine: Engine, post: Post) -> bool:
    """Whether you make the press that makes the post public: the post's own switch from when it was
    confirmed, or its account's as it is now, so assisted posting turned on reaches the posts already in
    the Queue."""
    account = store.get_account(engine.db(), post.account_id)
    return post.options.assisted or (account is not None and account.settings.assisted)


def studio_state(
    engine: Engine, post: Post, at: datetime, now: datetime, state: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The post's ``state`` (its own when not given) with TikTok Studio's schedule set to ``at`` when that
    is where it goes: an assisted TikTok post through Chrome with the setting on, its slot 20 minutes to 10
    days away. It is then uploaded at once and TikTok Studio puts it up (SPEC 10.3). Otherwise without
    it, so the post waits for its time as any other does."""
    state = {k: v for k, v in (post.state if state is None else state).items() if k != "studio"}
    if (
        post.platform == "tiktok"
        and post.method == "browser"
        and engine.settings.posting.tiktok_schedule
        and studio_slot(at, now) is not None
        and left_to_you(engine, post)
    ):
        state["studio"] = {"slot": at.isoformat()}
    return state


def assisted_again(engine: Engine, account_id: str) -> None:
    """Assisted posting turned on or off for an account, reaching the posts already in its Queue. Turned
    off, the switch each post took from it when confirmed goes off too, but for a TikTok post with a sound
    you add yourself; a window already open still waits for you, as its run began that way, and the
    post's Retry then posts on its own. Then TikTok Studio's schedule is set again (``studio_again``)."""
    with engine.lock:
        conn = engine.db()
        account = store.get_account(conn, account_id)
        if account is not None and not account.settings.assisted:
            for post in store.list_posts(conn, status=TO_GO, account_id=account_id):
                sound = post.options.sound
                yours = post.platform == "tiktok" and sound is not None and sound.source == "shortlist"
                if post.options.assisted and not yours:
                    change_post(engine, post.id, options=post.options.model_copy(update={"assisted": False}))
        studio_again(engine, account_id)


def studio_again(engine: Engine, account_id: str | None = None) -> None:
    """The account's TikTok posts waiting for their time (every account's without one) given TikTok Studio's
    schedule or not, as ``studio_state`` says now, and their jobs due to match: for assisted posting or
    TikTok's own schedule turned on or off."""
    with engine.lock:
        now = engine.now()
        waiting = store.list_posts(
            engine.db(), status=["scheduled"], account_id=account_id, platform="tiktok"
        )
        for post in waiting:
            state = studio_state(engine, post, _utc(post.scheduled_at), now)
            if state != post.state:
                change_post(engine, post.id, state=state)
        sync_jobs(engine)


def _studio_gone(post: Post, now: datetime) -> bool:
    """Whether TikTok Studio won't take the post's slot any more, so it goes up at its time instead."""
    slot = (post.state.get("studio") or {}).get("slot")
    return bool(slot) and studio_slot(datetime.fromisoformat(slot), now) is None


def due_at(post: Post) -> float:
    """When the job of a post waiting for a time runs: at once for one TikTok Studio will hold, its slot
    plus 10 minutes for one it holds (to read its link), else its slot."""
    if post.status == "scheduled_on_tiktok":
        return post.scheduled_at + CHECK_AFTER.total_seconds()
    if "studio" in post.state:
        return 0.0
    return post.scheduled_at


def post_event(engine: Engine, post: Post) -> None:
    """Everyone listening told of the post's change, and a posted post of a campaign's clip given its
    submission. A submission that fails never fails the post: the Campaigns page's sweep makes it later."""
    if post.status == "posted":
        try:
            tracker.on_post(engine, post)
        except Exception:
            log.exception("the submission for a posted post failed")
    engine.events.publish(
        "post",
        {
            "id": post.id,
            "status": post.status,
            "account_id": post.account_id,
            "batch_id": post.batch_id,
            "scheduled_at": post.scheduled_at,
            "error": clean_text(post.error),
        },
    )


def account_event(engine: Engine, account: Account) -> None:
    engine.events.publish("account", {"id": account.id, "status": account.status, "paused": account.paused})


def change_account(engine: Engine, account_id: str, **fields: Any) -> Account:
    """The account's fields changed, and everyone listening told."""
    with engine.lock:
        account = store.update_account(engine.db(), account_id, **fields)
        account_event(engine, account)
    return account


def move_post(engine: Engine, post_id: str, new: str, **fields: Any) -> Post:
    """``lifecycle.move``, and everyone listening told. A post no longer with the site loses its public
    address."""
    post = lifecycle.move(engine.db(), post_id, new, **fields)
    if post.status not in ("uploading", "processing"):
        engine.public_media.revoke(post_id)
    post_event(engine, post)
    return post


def change_post(engine: Engine, post_id: str, **fields: Any) -> Post:
    """The post's fields changed where it is, and everyone listening told."""
    post = store.update_post(engine.db(), post_id, **fields)
    post_event(engine, post)
    return post


def taken(engine: Engine, post: Post, now: datetime) -> tuple[list[datetime], list[datetime]]:
    """The times the post's account already posts at, and the platform's other accounts, the post left
    out: what its cap, spacing and gap count."""
    lo, hi = (now - LOOK_BACK).timestamp(), (now + LOOK_AHEAD).timestamp()
    mine: list[datetime] = []
    others: list[datetime] = []
    for p in store.list_posts(engine.db(), status=TAKEN, platform=post.platform):
        if p.id != post.id and lo <= p.scheduled_at < hi:
            (mine if p.account_id == post.account_id else others).append(_utc(p.scheduled_at))
    return mine, others


def _window_open(scheduled: datetime, now: datetime, rules: Rules) -> bool:
    """Whether ``now`` is still inside the window ``scheduled`` was in, on the same day in Sydney."""
    if sydney_day(scheduled, rules.tz) != sydney_day(now, rules.tz):
        return False
    then, clock = scheduled.astimezone(rules.tz).time(), now.astimezone(rules.tz).time()
    return any(start <= then < end and start <= clock < end for start, end in rules.windows)


def missed(engine: Engine, post: Post, account: Account, now: datetime) -> bool:
    """Whether the post is over 15 minutes late with its window closed."""
    scheduled = _utc(post.scheduled_at)
    rules = rules_for(engine.settings.posting, account)
    return now - scheduled > LATE and not _window_open(scheduled, now, rules)


@dataclass
class Awake:
    """When ClipForge could post: since the window opened it, and since the posting ticks last ran without
    a gap, the last of them at ``last``."""

    opened: datetime
    since: datetime
    last: datetime


def opened(engine: Engine) -> None:
    """Posting starts now, so a post due before now was due while ClipForge wasn't open."""
    now = engine.now()
    engine.awake = Awake(opened=now, since=now, last=now)


def _ticked(engine: Engine) -> None:
    """A posting tick. A gap since the last one means the Mac slept, so being awake starts again now."""
    awake, now = engine.awake, engine.now()
    if awake is None:
        return
    if now - awake.last > AWAKE_GAP:
        awake.since = now
    awake.last = now


def away(engine: Engine, post: Post, now: datetime) -> str | None:
    """Why ClipForge couldn't post at the post's time, as the sentence its move says: not open yet, or the
    Mac asleep. None when it was awake then, so a late post only waited its turn behind other posts, a
    login or a render."""
    awake, due = engine.awake, _utc(post.scheduled_at)
    if awake is None:  # posting never started here, so there is nothing to tell by
        return ASLEEP
    if due < awake.opened:
        return CLOSED
    if due < awake.since or (now - awake.last > AWAKE_GAP and due > awake.last):  # woken since the last tick
        return ASLEEP
    return None


def reslot(engine: Engine, post: Post, account: Account, now: datetime, note: str) -> Post | None:
    """Moves a post that missed its time to the next smart slot after ``now``, scheduled again with
    ``note`` (filled in with its old and new times). None when 14 days hold no free slot."""
    rules = rules_for(engine.settings.posting, account)
    mine, others = taken(engine, post, now)
    rng = random.Random(f"{post.id}:{post.scheduled_at}")
    at = smart_slot(rules, after=now, mine=mine, platform=others, rng=rng)
    if at is None:
        return None
    text = note.format(old=at_text(_utc(post.scheduled_at), rules.tz), new=at_text(at, rules.tz))
    state = studio_state(engine, post, at, now)
    return move_post(engine, post.id, "scheduled", scheduled_at=at.timestamp(), note=text, state=state)


def _cancel(engine: Engine, jobs: list[Job]) -> None:
    for job in jobs:
        engine.runner.cancel(job.id)


def sync_jobs(engine: Engine) -> None:
    """Makes the post jobs match the posts: one queued job for each post waiting for a time, due as
    ``due_at`` says (none while posting or its account is paused), one live job for each post on its way,
    none for anything else. A
    browser post left uploading with no job to finish it goes to the person. Then the Mac is kept awake
    for the next post due, or let sleep. Safe to run any time, as often as needed."""
    with engine.lock:
        conn = engine.db()
        kinds = list(POST_KINDS)
        marks = ", ".join("?" * len(kinds))
        live: dict[str, list[Job]] = {}
        rows = conn.execute(
            f"SELECT * FROM jobs WHERE status IN ('queued', 'running') AND kind IN ({marks}) ORDER BY id",
            kinds,
        )
        for row in rows:
            job = Job.from_row(row)
            live.setdefault(job.ref_id, []).append(job)
        posts = store.list_posts(conn, status=ACTIVE)
        accounts = {a.id: a for a in store.list_accounts(conn)}
        for ref in live.keys() - {p.id for p in posts}:
            _cancel(engine, [j for j in live[ref] if j.status == "queued"])
        for p in posts:
            jobs = live.get(p.id, [])
            running = [j for j in jobs if j.status == "running"]
            queued = [j for j in jobs if j.status == "queued"]
            if p.status in HELD and not running:
                account = accounts.get(p.account_id)
                wanted = not engine.settings.posting.paused and not (account is not None and account.paused)
                kind, due = job_kind(p), due_at(p)
                right = (j for j in queued if j.kind == kind and j.not_before == due)
                keep = next(right, None) if wanted else None
                _cancel(engine, [j for j in queued if j is not keep])
                if wanted and keep is None:
                    engine.runner.enqueue(kind, p.id, rerun=True, not_before=due)
            elif running:
                _cancel(engine, queued)
            elif queued:
                _cancel(engine, queued[1:])
            elif p.status == "rendering" or p.method == "api":
                engine.runner.enqueue(job_kind(p), p.id, rerun=True)
            else:
                move_post(engine, p.id, "needs_you", error=STOPPED.format(site=SITE[p.platform]))
        engine.power.update(next_due(engine), engine.now())


def next_due(engine: Engine) -> datetime | None:
    """When the next post goes out: now for one already on its way. None while posting is paused, or when
    no post waits on an account that isn't paused; one TikTok Studio holds doesn't count, as TikTok puts it
    up. Now, too, while a clipping channel's check or the projects it started are running, so the Mac stays
    awake for them (SPEC 10.9)."""
    if schedule.busy(engine):
        return engine.now()
    if engine.settings.posting.paused:
        return None
    conn = engine.db()
    paused = {a.id for a in store.list_accounts(conn) if a.paused}
    now = engine.now().timestamp()
    times = [
        due_at(p) if p.status == "scheduled" else now
        for p in store.list_posts(conn, status=ACTIVE)
        if p.account_id not in paused and p.status != "scheduled_on_tiktok"
    ]
    return _utc(min(times)) if times else None


def tick(engine: Engine) -> None:
    """The window's minute: the jobs matched to the posts again, the Mac kept awake for the next one, and
    Instagram Login keys refreshed before they run out."""
    _ticked(engine)
    sync_jobs(engine)
    from clipforge.post import instagram_api  # it imports this module

    try:
        instagram_api.refresh_tokens(engine)
    except Exception:
        log.exception("refreshing Instagram keys failed")


def recover_posts(engine: Engine) -> None:
    """At open: a browser post the last run left uploading may or may not be on the site, so it goes to the
    person. API posts carry on from their state."""
    with engine.lock:
        for p in store.list_posts(engine.db(), status=("uploading", "processing")):
            if p.method != "api":
                move_post(engine, p.id, "needs_you", error=STOPPED.format(site=SITE[p.platform]))


def finished(engine: Engine, job: Job) -> None:
    """The runner's hook for a post job. One that failed (a bug, or gone quiet) leaves its post with the
    person, so it never posts again by itself; then the jobs are matched to the posts."""
    if job.status == "cancelled":
        return
    with engine.lock:
        post = store.get_post(engine.db(), job.ref_id)
        if job.status == "failed" and post is not None and post.status in ACTIVE:
            error = SOMETHING.format(site=SITE[post.platform])
            move_post(engine, post.id, "needs_you", error=error, details=job.error_details)
    sync_jobs(engine)


def run_post(engine: Engine, ctx: JobContext) -> None:
    """The job of a post that is due: checks it can go, hands it to its publisher, and moves it on by what
    the publisher says."""
    if ctx.cancelled():
        return
    with engine.lock:
        conn, now = engine.db(), engine.now()
        post = store.get_post(conn, ctx.job.ref_id)
        if post is None or post.status not in ACTIVE:
            return
        account = store.get_account(conn, post.account_id)
        if account is None:
            move_post(engine, post.id, "failed", error=ACCOUNT_DELETED, error_code="account_deleted")
            return
        if (engine.settings.posting.paused or account.paused) and post.status != "processing":
            if post.status not in HELD:
                move_post(engine, post.id, "scheduled", note=PAUSED_NOTE)
            return
        if post.status == "scheduled" and _studio_gone(post, now):  # it waited its turn too long for that
            post = change_post(engine, post.id, state={k: v for k, v in post.state.items() if k != "studio"})
        if post.status in HELD and due_at(post) > now.timestamp():
            raise WaitUntil(due_at(post), WAITING)
        if post.status in ("scheduled", "rendering"):
            started = _start(engine, post, account, now)
            if started is None:
                return
            post = started
        elif post.status != "scheduled_on_tiktok":  # carrying on after a restart or a wait
            post = store.update_post(conn, post.id, attempts=post.attempts + 1)
        video = None if post.status == "scheduled_on_tiktok" else video_for(engine, post)
    if post.status == "scheduled_on_tiktok":
        _read_link(engine, ctx, post, account)
        return
    try:
        publisher = publisher_for(engine, account)
        with ctx.beating():
            result = publisher.publish(post, Report(engine, ctx, post, account, video))
    except WaitUntil:
        raise
    except Paused as err:
        _paused(engine, post.id, err)
    except NeedsUser as err:
        _needs_you(engine, post.id, err)
    except Retryable as err:
        _retryable(engine, post, err)
    except Fatal as err:
        _settle(engine, post.id, "failed", error=err.sentence, error_code=err.code, details=err.details)
    except Exception as exc:
        log.exception("post %s failed", post.id)
        error, details = SOMETHING.format(site=SITE[post.platform]), f"{type(exc).__name__}: {exc}"
        _settle(engine, post.id, "needs_you", error=error, details=details)
    else:
        if result.scheduled:
            _held(engine, post.id, result)
            return
        _settle(
            engine,
            post.id,
            "posted",
            external_id=result.external_id,
            url=result.url,
            posted_at=engine.now().timestamp(),
            note="; ".join(result.warnings) or None,
            error=None,
            error_code=None,
            details=None,
        )


def _start(engine: Engine, post: Post, account: Account, now: datetime) -> Post | None:
    """A due post that hasn't started: moved to its next slot when it missed its time, made to wait for a
    render under way or for its account's own version of the clip (``variants``), failed when there is no
    clip or video, else uploading with the render it sends, the gameplay stretch that shows written down.
    None when it doesn't go on."""
    conn = engine.db()
    clip = repo.get_clip(conn, post.clip_id) if post.clip_id else None
    if clip is None:
        move_post(engine, post.id, "failed", error=CLIP_DELETED, error_code="clip_deleted")
        return None
    if missed(engine, post, account, now) and (note := away(engine, post, now)) is not None:
        moved = reslot(engine, post, account, now, note)
        if moved is None:
            move_post(engine, post.id, "needs_you", error=NO_SLOT)
            return None
        raise WaitUntil(due_at(moved), moved.note or WAITING)
    rendering = any(
        j.kind in CLIP_FUNCTIONS and j.status in ("queued", "running")
        for j in engine.runner.jobs_for(clip.id)
    )
    if rendering:
        if post.status != "rendering":
            move_post(engine, post.id, "rendering")
        raise WaitUntil(now.timestamp() + RENDER_WAIT_S, RENDERING)
    if needs_variant(engine, clip, account) and variant_for(engine, clip, account) is None:
        return _await_variant(engine, post, clip, account, now)
    render = render_for(engine, post)
    if render is None or playable(render) is None:
        move_post(engine, post.id, "failed", error=NO_VIDEO, error_code="no_render")
        return None
    caption, campaign = post.caption, None
    if render.id != post.render_id:  # rendered again since it was approved, or the account's own version
        campaign = campaign_of_post(engine, post)
        caption = caption_for(engine, post, render)  # it credits the music that goes up
    if campaign is not None:  # the new render keeps the campaign's rules too
        broken = broken_rule(
            engine,
            post,
            campaign,
            kinds=RENDER_RULES,
            body=caption,
            hashtags=post.hashtags,
            render=render,
        )
        if broken is not None:
            move_post(engine, post.id, "needs_you", error=broken)
            return None
    count_shown(engine, post, render)
    return move_post(
        engine,
        post.id,
        "uploading",
        caption=caption,
        render_id=render.id,
        attempts=post.attempts + 1,
        error=None,
        error_code=None,
        details=None,
        log_path=str(engine.paths.post_logs / post.id / "steps.jsonl"),
    )


def _await_variant(engine: Engine, post: Post, clip: ClipRow, account: Account, now: datetime) -> None:
    """The post waits while its account's own version of the clip renders, queued now when none is on its
    way. When the one it waited for failed or was stopped, the post fails saying why, and Retry, which
    forgets that job, queues it again."""
    made = engine.runner.jobs_for(variant_ref(clip.id, account.id))
    last = made[-1] if made else None
    if last is not None and last.status in ("queued", "running"):
        job_id = last.id
    elif (
        last is not None
        and last.status in ("failed", "cancelled")
        and last.id == post.state.get("variant_job")
    ):
        error = VARIANT_FAILED.format(error=last.error or CANCELLED)
        move_post(engine, post.id, "failed", error=error, error_code="variant_failed")
        return None
    else:
        job_id = queue_variant(engine, clip, account)
    state = post.state | {"variant_job": job_id}
    if post.status != "rendering":
        move_post(engine, post.id, "rendering", state=state)
    elif state != post.state:
        change_post(engine, post.id, state=state)
    raise WaitUntil(now.timestamp() + RENDER_WAIT_S, RENDERING)


def _settle(engine: Engine, post_id: str, new: str, **fields: Any) -> None:
    with engine.lock:
        move_post(engine, post_id, new, **fields)


def _held(engine: Engine, post_id: str, result: PublishResult) -> None:
    """Uploaded with TikTok Studio's schedule set, so TikTok puts it up at its slot, rounded as its schedule
    holds it. It is never uploaded again: ``on_tiktok`` stays with it, and its link is read after."""
    with engine.lock:
        post = store.get_post(engine.db(), post_id)
        slot = (post.state.get("studio") or {}).get("slot")
        at = datetime.fromisoformat(slot) if slot else _utc(post.scheduled_at)
        state = {k: v for k, v in post.state.items() if k not in ("actions", "screenshot")}
        move_post(
            engine,
            post_id,
            "scheduled_on_tiktok",
            scheduled_at=studio_round(at).timestamp(),
            external_id=result.external_id,
            url=result.url,
            state=state | {"on_tiktok": True},
            note="; ".join(result.warnings) or None,
            error=None,
            error_code=None,
            details=None,
        )


def _read_link(engine: Engine, ctx: JobContext, post: Post, account: Account) -> None:
    """One read of TikTok Studio's posts list for a post it put up, uploading nothing: posted, at its slot,
    with the link of the row that holds its caption, else with you, once."""
    reader = getattr(engine.publishers.get(f"{post.platform}:{post.method}"), "find_link", None)
    try:
        if reader is None:
            raise Fatal(NO_ROW, "no_route")
        with ctx.beating():
            found = reader(post, Report(engine, ctx, post, account, None))
    except Exception as exc:
        log.warning("reading post %s's link failed: %s", post.id, type(exc).__name__)
        _settle(engine, post.id, "needs_you", error=NO_ROW, details=f"{type(exc).__name__}: {exc}")
        return
    if found is None or not found.url:
        _settle(engine, post.id, "needs_you", error=NO_ROW)
        return
    _settle(
        engine,
        post.id,
        "posted",
        external_id=found.external_id,
        url=found.url,
        posted_at=post.scheduled_at,
        error=None,
        error_code=None,
        details=None,
    )


def _paused(engine: Engine, post_id: str, err: Paused) -> None:
    """Back to scheduled before the step that makes it public; one already processing goes to the person."""
    with engine.lock:
        post = store.get_post(engine.db(), post_id)
        if lifecycle.can_move(post.status, "scheduled"):
            move_post(engine, post_id, "scheduled", note=PAUSED_NOTE)
        else:
            move_post(engine, post_id, "needs_you", error=err.sentence)


def _needs_you(engine: Engine, post_id: str, err: NeedsUser) -> None:
    """With the person, with the buttons and the screenshot the Queue shows."""
    with engine.lock:
        post = store.get_post(engine.db(), post_id)
        state = {**post.state, "actions": list(err.actions), "screenshot": err.screenshot}
        move_post(engine, post_id, "needs_you", error=err.sentence, details=err.details, state=state)


def _retryable(engine: Engine, post: Post, err: Retryable) -> None:
    """An API post that is safe to send again waits 1, 5 then 15 minutes, then fails. A browser post, or one
    the site may already have, goes to the person."""
    with engine.lock:
        current = store.get_post(engine.db(), post.id)
        tries = int(current.state.get("retries", 0))
        if post.method != "api" or not err.safe:
            move_post(engine, post.id, "needs_you", error=err.sentence, details=err.details)
        elif tries < len(BACKOFF_S):
            change_post(engine, post.id, state={**current.state, "retries": tries + 1}, note=err.sentence)
            raise WaitUntil(engine.now().timestamp() + max(BACKOFF_S[tries], err.after_s), err.sentence)
        else:
            move_post(
                engine, post.id, "failed", error=err.sentence, error_code="retries_used", details=err.details
            )
