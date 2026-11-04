"""A clipping channel's check (SPEC 10.9): each YouTube channel it watches is read for new uploads, which
become its watch items, and up to the day's limit of them become ordinary projects with the clipping
channel's options. A check runs as a job, so it outlasts a restart: running it again carries on where it
stopped and adds nothing twice."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from clipforge import repo
from clipforge.campaigns import store as campaigns
from clipforge.db import transaction
from clipforge.errors import ClipForgeError, SourceRefused, YouTubeRateLimited
from clipforge.ingest import ytdlp
from clipforge.ingest.urls import canonical_url
from clipforge.untrusted import clean_text
from clipforge.watch import store
from clipforge.watch.feed import UNREADABLE, FeedClient, FeedUnreadable
from clipforge.watch.models import (
    SKIP_SENTENCES,
    TITLE_MAX,
    ChannelListing,
    ClipChannel,
    FeedEntry,
    WatchCheck,
    WatchedSource,
    WatchItem,
    footage_key,
)

if TYPE_CHECKING:
    import sqlite3

    from clipforge.app import Engine
    from clipforge.campaigns.models import Campaign
    from clipforge.config import Settings
    from clipforge.jobs.runner import Job

log = logging.getLogger(__name__)

KIND = "watch.check"
FEED_GAP_S = 3.0  # between one YouTube request and the next, so a check never hurries YouTube
WAIT_DAYS = 3  # a video still waiting for its turn after this long is given up on
LISTING_LIMIT = 15  # how much of the channel page a check reads when the feed can't be read
WAITING_CODES = frozenset({"live", "upcoming", "processing"})  # these may be clipped at a later check


def today(engine: Engine) -> str:
    """The day it is in the posting time zone, as YYYY-MM-DD: the day a daily check belongs to."""
    return engine.now().astimezone(ZoneInfo(engine.settings.posting.timezone)).date().isoformat()


def start_check(engine: Engine, channel_id: str, *, manual: bool, day: str | None = None) -> WatchCheck:
    """A new check of the clipping channel, queued as a job. The day's own check (not ``manual``) marks
    ``day`` as checked, today unless it makes up yesterday's, so the schedule starts no second one; Check
    now leaves that alone. The check itself belongs to the day it runs."""
    runs_on = today(engine)
    made = WatchCheck(
        id=repo.new_id("wc"),
        clip_channel_id=channel_id,
        check_on=runs_on,
        manual=manual,
        started_at=engine.now().timestamp(),
    )
    conn = engine.db()
    with engine.lock, transaction(conn):
        if store.get_channel(conn, channel_id) is None:
            raise LookupError(channel_id)
        store.add_check(conn, made)
        if not manual:
            store.update_channel(conn, channel_id, last_check_on=day or runs_on)
        engine.runner.enqueue(KIND, made.id, {"check": made.id}, max_attempts=2)
    engine.events.publish("clip_channel", {"id": channel_id})
    return made


def classify(info: dict, settings: Settings) -> tuple[str, str | None]:
    """Whether a new upload is clipped now: ("pass", None); ("waiting", code) for a stream that is live,
    a premiere still to come and a video YouTube is still processing; ("skipped", code) for the rest,
    with a SKIP_SENTENCES code."""
    meta = ytdlp.meta_from_info(info)
    refusal = ytdlp.refusal_for(meta, max_hours=settings.watch.max_hours)
    if refusal is not None:
        if refusal.code == "unavailable" and not meta.duration_s:
            return "waiting", "processing"  # no length yet
        if refusal.code in WAITING_CODES:
            return "waiting", refusal.code
        return "skipped", refusal.code if refusal.code in SKIP_SENTENCES else "unavailable"
    if meta.duration_s < settings.watch.min_minutes * 60:
        return "skipped", "too_short"
    return "pass", None


class _Pace:
    """Keeps FEED_GAP_S between one YouTube request and the next."""

    def __init__(self) -> None:
        self.last: float | None = None

    def __call__(self) -> None:
        if self.last is not None:
            wait = FEED_GAP_S - (time.monotonic() - self.last)
            if wait > 0:
                time.sleep(wait)
        self.last = time.monotonic()


class _Counts:
    def __init__(self, check: WatchCheck):
        self.found, self.skipped, self.clipped = check.found, check.skipped, check.clipped

    def fields(self) -> dict[str, int]:
        return {"found": self.found, "skipped": self.skipped, "clipped": self.clipped}


def run_check(engine: Engine, check_id: str) -> WatchCheck:
    """Reads each YouTube channel the clipping channel watches, adds its new uploads as items, then turns
    the clipping channel's waiting items, newest first, into projects until the day's limit have passed.
    A rate limit from YouTube ends the check and the clipping channel's day. Safe to run again for the
    same check: a finished one is given back as it is, and one stopped partway carries on."""
    conn = engine.db()
    check = store.get_check(conn, check_id)
    if check is None:
        raise LookupError(check_id)
    if check.finished_at is not None:
        return check
    channel = store.get_channel(conn, check.clip_channel_id)
    if channel is None:  # deleted, and its checks with it, since the job was queued
        raise LookupError(check.clip_channel_id)
    campaign = campaigns.get_campaign(conn, channel.campaign_id) if channel.campaign_id else None
    counts, pace = _Counts(check), _Pace()
    try:
        for source in store.list_sources(conn, channel.id):
            if source.is_footage:
                continue  # a campaign's own videos, which have no feed to read
            if campaign is not None and _ruled_out(campaign, source):
                with engine.lock:
                    store.update_source(
                        conn, source.id, error=SKIP_SENTENCES["not_allowed"].format(name=campaign.name)
                    )
                continue
            _read_source(engine, check, channel, source, counts, pace)
        _clip_waiting(engine, check, channel, campaign, counts, pace)
    except YouTubeRateLimited:
        return _finish(engine, check, counts, error="rate_limited")
    return _finish(engine, check, counts)


def _ruled_out(campaign: Campaign | None, source: WatchedSource, video_id: str | None = None) -> bool:
    """Whether the clipping channel's campaign doesn't allow footage from the source (or this video of
    it). A source of a campaign's footage videos holds only that campaign's listed videos."""
    if source.is_footage:
        return (
            campaign is None
            or source.yt_channel_id != footage_key(campaign.id)
            or video_id not in campaign.footage.videos
        )
    return campaign is not None and not campaign.footage.allows(video_id, source.yt_channel_id)


def _read_source(
    engine: Engine,
    check: WatchCheck,
    channel: ClipChannel,
    source: WatchedSource,
    counts: _Counts,
    pace: _Pace,
) -> None:
    """One YouTube channel's new uploads, from its feed, or once from its channel page when the feed
    can't be read. When both fail, the source keeps the sentence and the check goes on."""
    conn = engine.db()
    now = engine.now().timestamp()
    pace()
    try:
        entries = FeedClient(engine.feed_transport).entries(source.yt_channel_id)
        fields: dict = {"error": None, "last_feed_at": now}
        if entries:
            newest = entries[0]
            fields |= {
                "newest_video_id": newest.video_id,
                "newest_title": newest.title,
                "newest_published_at": newest.published_at,
            }
        new = [e for e in entries if e.published_at > source.start_published_at]
    except FeedUnreadable as err:
        log.info("the feed of %s couldn't be read (%s), so its channel page is", source.id, err.details)
        pace()
        try:
            listing = engine.youtube.list_channel(source.url, LISTING_LIMIT)
        except YouTubeRateLimited:
            raise
        except ClipForgeError as failed:
            store.update_source(conn, source.id, error=failed.message)
            return
        fields = {"error": UNREADABLE}
        if listing.entries:
            video_id, title = listing.entries[0]
            fields |= {"newest_video_id": video_id, "newest_title": title, "newest_published_at": None}
        new = _listed(conn, channel, source, listing)
    with engine.lock, transaction(conn):
        for entry in new:
            if store.item_for_video(conn, channel.id, entry.video_id) is not None:
                continue
            counts.found += 1
            if entry.is_short:
                counts.skipped += 1
            store.add_item(
                conn,
                WatchItem(
                    id=repo.new_id("wi"),
                    source_id=source.id,
                    clip_channel_id=channel.id,
                    check_id=check.id if entry.is_short else None,
                    video_id=entry.video_id,
                    title=entry.title,
                    published_at=entry.published_at or None,
                    status="skipped" if entry.is_short else "new",
                    reason="short" if entry.is_short else None,
                    first_seen_at=now,
                    updated_at=now,
                ),
            )
        store.update_source(conn, source.id, **fields)
        store.update_check(conn, check.id, **counts.fields())


def _listed(
    conn: sqlite3.Connection, channel: ClipChannel, source: WatchedSource, listing: ChannelListing
) -> list[FeedEntry]:
    """The channel page's uploads newer than the first one the check already knows. The page has no
    dates or Shorts, so with nothing known on it nothing counts as new: the next check starts from it."""
    new = []
    for video_id, title in listing.entries:
        if video_id == source.newest_video_id or store.item_for_video(conn, channel.id, video_id):
            return new
        new.append(FeedEntry(video_id=video_id, title=title, published_at=0.0))
    return []


def _clip_waiting(
    engine: Engine,
    check: WatchCheck,
    channel: ClipChannel,
    campaign: Campaign | None,
    counts: _Counts,
    pace: _Pace,
) -> None:
    conn = engine.db()
    now = engine.now().timestamp()
    earlier = store.clipped_on(conn, channel.id, check.check_on, besides=check.id)  # the day's other checks
    sources = {s.id: s for s in store.list_sources(conn, channel.id)}
    for item in store.items_for(conn, channel.id, status=("new", "waiting")):
        source = sources.get(item.source_id)
        if source is not None and _ruled_out(campaign, source, item.video_id):
            _decide(engine, check, item, counts, "skipped", "not_allowed")
            continue
        footage = source is not None and source.is_footage  # a campaign's videos wait for their turn
        if not footage and now - item.first_seen_at > WAIT_DAYS * 86400:
            _decide(engine, check, item, counts, "skipped", "expired")
            continue
        if earlier + counts.clipped >= channel.daily_video_limit:
            if item.status == "new" and item.reason != "over_limit":
                with engine.lock:
                    store.update_item(conn, item.id, reason="over_limit")
            continue
        pace()
        try:
            info = engine.youtube.fetch_info(canonical_url(item.video_id))
        except YouTubeRateLimited:
            raise
        except SourceRefused as err:
            if err.code in WAITING_CODES:
                _decide(engine, check, item, counts, "waiting", err.code)
            else:
                code = err.code if err.code in SKIP_SENTENCES else "unavailable"
                _decide(engine, check, item, counts, "skipped", code)
            continue
        except ClipForgeError as err:  # network trouble or a yt-dlp hiccup: the next check tries again
            log.info("%s couldn't be looked up (%s: %s)", item.video_id, err.code, err.details)
            continue
        verdict, code = classify(info, engine.settings)
        if verdict == "pass":
            _clip(engine, check, channel, item, info, counts)
        else:
            _decide(engine, check, item, counts, verdict, code)


def _decide(
    engine: Engine, check: WatchCheck, item: WatchItem, counts: _Counts, status: str, reason: str | None
) -> None:
    conn = engine.db()
    with engine.lock, transaction(conn):
        if status == "skipped":
            counts.skipped += 1
            store.update_item(conn, item.id, status=status, reason=reason, check_id=check.id)
            store.update_check(conn, check.id, **counts.fields())
        else:
            store.update_item(conn, item.id, status=status, reason=reason)


def _clip(
    engine: Engine, check: WatchCheck, channel: ClipChannel, item: WatchItem, info: dict, counts: _Counts
) -> None:
    conn = engine.db()
    with engine.lock:
        fresh = store.get_item(conn, item.id)
        if fresh is None or fresh.status not in ("new", "waiting"):
            return
        project = engine.add_watched(channel, fresh, info)  # or the one a stopped check made already
        title = fresh.title or (clean_text(info.get("title") or "") or "").strip()[:TITLE_MAX]
        with transaction(conn):
            counts.clipped += 1
            store.update_item(
                conn,
                item.id,
                status="clipping",
                reason=None,
                project_id=project.id,
                check_id=check.id,
                title=title,
            )
            store.update_check(conn, check.id, **counts.fields())


def _finish(engine: Engine, check: WatchCheck, counts: _Counts, *, error: str | None = None) -> WatchCheck:
    conn = engine.db()
    now = engine.now().timestamp()
    fields: dict = counts.fields() | {"finished_at": now, "error": error}
    if counts.clipped == 0:  # no projects, so no drafts to wait for
        fields |= {"drafts": 0, "drafted_at": now}
    with engine.lock, transaction(conn):
        store.update_check(conn, check.id, **fields)
        if error == "rate_limited":  # YouTube asked for a break: no more checks of this channel today
            store.update_channel(conn, check.clip_channel_id, last_check_on=today(engine))
    engine.events.publish("clip_channel", {"id": check.clip_channel_id})
    if counts.clipped:
        _settle(engine, check.id)
    done = store.get_check(conn, check.id)
    assert done is not None
    return done


def _settle(engine: Engine, check_id: str) -> None:
    """Drafts now, should the check's projects all have finished before it did."""
    from clipforge.watch import drafts  # here, as drafts reaches posting, which reaches the schedule

    drafts.settle_check(engine, check_id)


def finished(engine: Engine, job: Job) -> None:
    """The runner's hook for a check's job: a check that failed or was stopped is closed, so nothing
    waits on it."""
    if job.status not in ("failed", "cancelled"):
        return
    conn = engine.db()
    check = store.get_check(conn, (job.payload or {}).get("check") or job.ref_id)
    if check is None or check.finished_at is not None:
        return
    now = engine.now().timestamp()
    fields: dict = {"finished_at": now, "error": job.error_code or "failed"}
    if check.clipped == 0:
        fields |= {"drafts": 0, "drafted_at": now}
    with engine.lock:
        store.update_check(conn, check.id, **fields)
    engine.events.publish("clip_channel", {"id": check.clip_channel_id})
    if check.clipped:
        _settle(engine, check.id)
