"""Builders for the posting tests: clips with renders, accounts, confirmed posts, a post's job run as a
worker runs it, campaigns with clips linked to them, and posts that went up with their view counts."""

from __future__ import annotations

import time
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from clipforge import repo
from clipforge.campaigns import store as campaigns
from clipforge.campaigns.models import Campaign, Metric, Submission
from clipforge.models import ClipRow, ProjectOptions, RenderRow
from clipforge.post import store
from clipforge.post.models import Account, AccountSettings, PlanRequest, Post
from clipforge.render.qa import Check, QAResult

if TYPE_CHECKING:
    from clipforge.render.edl import EDL

SYD = ZoneInfo("Australia/Sydney")
PACK = {
    "hook_title": "The deadline",
    "tiktok_caption": "TikTok words",
    "tiktok_hashtags": ["#money"],
    "instagram_caption": "Instagram words",
    "instagram_hashtags": ["#money"],
    "credit": "Source: Lex Fridman",
}
GOOD = [Check(name="codec", ok=True, hard=True)]
DAY = 86400.0


def at(hour: int, minute: int = 0, *, day: int = 3) -> datetime:
    """A time in Sydney in the first week of November 2026; the 3rd is a Tuesday."""
    return datetime(2026, 11, day, hour, minute, tzinfo=SYD)


def sydney(t: float) -> datetime:
    return datetime.fromtimestamp(t, SYD)


def add_render(
    engine, clip: ClipRow, version: int = 1, *, length: float | None = None, edl: EDL | None = None
) -> RenderRow:
    """A ready render of ``clip``, its file on disk, made the clip's current one; with ``edl``, the edit list
    it was made from written beside it, as a real render keeps it."""
    conn = engine.db()
    edl_path = engine.paths.home / f"{clip.id}-v{version}.edl.json"
    render = RenderRow(
        id=repo.new_id("r"),
        clip_id=clip.id,
        version=version,
        style_key="default",
        path=str(engine.paths.home / f"{clip.id}-v{version}.mp4"),
        duration_s=length or clip.end_s - clip.start_s,
        size_bytes=1_000_000,
        qa=QAResult(checks=list(GOOD)).model_dump(),
        edl_path=str(edl_path) if edl is not None else None,
    )
    engine.paths.home.mkdir(parents=True, exist_ok=True)
    if edl is not None:
        edl_path.write_text(edl.model_dump_json())
    (engine.paths.home / f"{clip.id}-v{version}.mp4").write_bytes(f"video {version}".encode())
    repo.add_render(conn, render)
    repo.update_clip(conn, clip.id, current_render_id=render.id)
    return render


def add_clip(engine, rank: int = 1, *, length: float = 30.0, rendered: bool = True, rights: str = "own"):
    conn = engine.db()
    project = repo.create_project(
        conn,
        source_kind="youtube",
        source_url="https://www.youtube.com/watch?v=arj7oStGLkU",
        video_id=repo.new_id("v"),
        options=ProjectOptions(),
        focus_s=None,
        prompt=None,
        title="A talk",
    )
    repo.update_project(conn, project.id, rights_basis=rights)
    row = ClipRow(
        id=repo.new_id("c"),
        project_id=project.id,
        rank=rank,
        start_s=10.0,
        end_s=10.0 + length,
        sentence_ids=["S1"],
        packaging=PACK,
        status="ready",
    )
    repo.replace_ideas(conn, project.id, [row])
    clip = repo.get_clip(conn, row.id)
    if rendered:
        add_render(engine, clip)
    return repo.get_clip(conn, row.id)


def add_account(
    engine, platform: str = "tiktok", handle: str | None = None, *, method: str | None = None, **settings
) -> Account:
    a = Account(
        id=repo.new_id("a"),
        platform=platform,
        method=method or ("browser" if platform == "tiktok" else "api"),
        handle=handle or repo.new_id("h"),
        status="connected",
        settings=AccountSettings(**settings),
        created_at=time.time(),
    )
    return store.add_account(engine.db(), a)


def request(clips, accounts, **req) -> PlanRequest:
    return PlanRequest(clip_ids=[c.id for c in clips], account_ids=[a.id for a in accounts], **req)


def confirm(engine, clips, accounts, **req):
    """Plan, then Confirm and post, as the Post dialog does: the batch id and its posts."""
    r = request(clips, accounts, **req)
    plan = engine.posting.plan(r)
    assert not plan.blocked, [w.sentence for w in plan.warnings if w.blocking]
    return engine.posting.confirm(r, plan.plan_id)


def custom(engine, clip, account, when: datetime):
    """``clip`` confirmed to ``account`` at ``when``: the one post."""
    _, (post,) = confirm(
        engine, [clip], [account], timing="custom", custom_times={f"{clip.id}:{account.id}": when.timestamp()}
    )
    return post


def post(engine, post_id: str):
    return store.get_post(engine.db(), post_id)


def jobs(engine, post_id: str, status: str = "queued"):
    return [j for j in engine.runner.jobs_for(post_id) if j.status == status]


def claim(engine, post_id: str):
    """The post's queued job, claimed as a worker claims it."""
    (job,) = jobs(engine, post_id)
    engine.db().execute(
        "UPDATE jobs SET status = 'running', attempts = attempts + 1, started_at = ? WHERE id = ?",
        (time.time(), job.id),
    )
    return engine.runner.get(job.id)


def run(engine, post_id: str, job=None):
    """The post's job run as a worker runs it, whatever its ``not_before``: the job and the post after."""
    job = job or claim(engine, post_id)
    engine.runner._run(engine.db(), job)
    return engine.runner.get(job.id), post(engine, post_id)


def bare_post(conn, status: str = "posted") -> str:
    """A post row with only what the table needs, for tests of rows that point at a post."""
    now = time.time()
    account, batch, post_id = repo.new_id("a"), repo.new_id("b"), repo.new_id("po")
    conn.execute(
        "INSERT INTO accounts (id, platform, method, created_at) VALUES (?, 'tiktok', 'browser', ?)",
        (account, now),
    )
    conn.execute(
        "INSERT INTO batches (id, plan_id, mode, schedule_mode, created_at)"
        " VALUES (?, ?, 'spread', 'smart', ?)",
        (batch, batch, now),
    )
    conn.execute(
        "INSERT INTO posts (id, batch_id, account_id, platform, method, scheduled_at, status, video_key,"
        " start_s, end_s, created_at, updated_at)"
        " VALUES (?, ?, ?, 'tiktok', 'browser', ?, ?, 'v', 0, 30, ?, ?)",
        (post_id, batch, account, now, status, now, now),
    )
    return post_id


def add_campaign(engine, **fields) -> Campaign:
    now = time.time()
    fields = {"name": "Beast clips", "marketplace": "Whop"} | fields
    made = Campaign(id=repo.new_id("cp"), created_at=now, updated_at=now, **fields)
    return campaigns.add_campaign(engine.db(), made)


def linked_clip(engine, campaign: Campaign, rank: int = 1) -> ClipRow:
    """A rendered clip of a project linked to ``campaign``."""
    clip = add_clip(engine, rank, rights="clipping_programme")
    repo.update_project(engine.db(), clip.project_id, campaign_id=campaign.id)
    return clip


def posted(engine, clip, account, **state):
    """``clip`` posted to ``account`` now by the engine's publisher, with ``state`` on the post before it
    went. The gap between accounts of a platform is lifted while it is planned, so a test can post several
    at once."""
    kept = engine.settings
    engine.settings = kept.model_copy(
        update={"posting": kept.posting.model_copy(update={"platform_gap_min": 0})}
    )
    try:
        planned = custom(engine, clip, account, engine.now())
    finally:
        engine.settings = kept
    if state:
        store.update_post(engine.db(), planned.id, state=planned.state | state)
    _, done = run(engine, planned.id)
    assert done.status == "posted", done.error
    return done


def interrupt_first_visit(monkeypatch, path: str) -> list[str]:
    """Right after a login the site goes home by itself, and when that crosses ClipForge's first visit to
    ``path``, Chrome stops the visit with this error. Returns every address visited."""
    from playwright.sync_api import Error, Page

    real = Page.goto
    visits: list[str] = []

    def goto(self, url, **kwargs):
        visits.append(url)
        if url.endswith(path) and visits.count(url) == 1:
            raise Error(f'Page.goto: Navigation to "{url}" is interrupted by another navigation to "/"')
        return real(self, url, **kwargs)

    monkeypatch.setattr(Page, "goto", goto)
    return visits


def posted_post(engine, account, *, days_ago: float, counts=()):
    """A post that went up ``days_ago`` days before the engine's now on ``account``, with a count of
    ``views`` taken ``d`` days after it went up for each ``(d, views)``."""
    conn = engine.db()
    when = engine.now().timestamp() - days_ago * DAY
    batch, post_id = repo.new_id("b"), repo.new_id("po")
    conn.execute(
        "INSERT INTO batches (id, plan_id, mode, schedule_mode, created_at)"
        " VALUES (?, ?, 'spread', 'smart', ?)",
        (batch, batch, when),
    )
    conn.execute(
        "INSERT INTO posts (id, batch_id, account_id, platform, method, scheduled_at, status, video_key,"
        " start_s, end_s, created_at, updated_at, posted_at)"
        " VALUES (?, ?, ?, ?, ?, ?, 'posted', 'v', 0, 30, ?, ?, ?)",
        (post_id, batch, account.id, account.platform, account.method, when, when, when, when),
    )
    for d, views in counts:
        campaigns.add_metric(
            conn, Metric(post_id=post_id, captured_at=when + d * DAY, source="hand", views=views)
        )
    return store.get_post(conn, post_id)


def campaign_post(
    engine,
    campaign: Campaign,
    account: Account,
    *,
    days_ago: float,
    counts=(),
    status: str = "submitted",
    paid: float | None = None,
    title: str = "A clip",
    reason: str | None = None,
) -> Post:
    """A ``posted_post`` called ``title`` with a submission to ``campaign`` in ``status``: paid ``paid`` on
    the engine's day when given, and turned down for ``reason`` when given."""
    post = posted_post(engine, account, days_ago=days_ago, counts=counts)
    conn = engine.db()
    store.update_post(conn, post.id, clip_title=title)
    now = time.time()
    paid_on = engine.now().date().isoformat() if paid is not None else None
    made = Submission(
        id=repo.new_id("sb"),
        post_id=post.id,
        campaign_id=campaign.id,
        status=status,
        reason=reason,
        paid_amount=paid,
        paid_on=paid_on,
        created_at=now,
        updated_at=now,
    )
    campaigns.add_submission(conn, made)
    return store.get_post(conn, post.id)


def history(engine, account, weekly: list[int]):
    """One post a day for each 7-day count in ``weekly``, newest first, each counted a week after it went
    up."""
    return [posted_post(engine, account, days_ago=8 + n, counts=[(7, v)]) for n, v in enumerate(weekly)]
