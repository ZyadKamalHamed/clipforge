"""Saving campaign profiles and linking clipping channels and projects to them (SPEC 10.10). A save checks
every field before anything is looked up, then looks up each footage channel once; a linked clipping
channel watches the campaign's footage channels and holds its footage videos as items of a hidden source,
and a linked project or channel has the rights basis Clipping programme until it is unlinked."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from clipforge import repo
from clipforge.briefs import store as brief_store
from clipforge.briefs.models import FIXED, MISSING
from clipforge.campaigns import parse, store, tracker
from clipforge.campaigns.models import UC_ID, VIDEO_ID, Campaign, FootageChannel
from clipforge.db import transaction
from clipforge.errors import ClipForgeError
from clipforge.ingest.urls import _split_url, youtube_id
from clipforge.models import Project
from clipforge.post.distribute import clean_hashtags
from clipforge.untrusted import clean_lines, clean_text
from clipforge.watch import store as watch_store
from clipforge.watch.check import today
from clipforge.watch.models import (
    SKIP_SENTENCES,
    TITLE_MAX,
    ClipChannel,
    WatchedSource,
    WatchItem,
    footage_key,
)
from clipforge.watch.sources import channel_link

if TYPE_CHECKING:
    import sqlite3

    from clipforge.app import Engine
    from clipforge.briefs.models import Brief

FORM = frozenset(parse.blank("")) | {"status"}  # what a save may set
NOT_ALLOWED = SKIP_SENTENCES["not_allowed"]
NO_FILES = "The campaign {name} doesn't allow footage from files or Drive links you add."
HAS_SUBMISSIONS = "This campaign has {n} {what}, so mark it ended instead."
NOT_CHANNEL = "{link} isn't a YouTube channel link. Paste one like youtube.com/@name."
NOT_VIDEO = "{link} isn't a YouTube video link."
NOT_FOUND = "ClipForge couldn't look up {link}. {why}"
UNKNOWN = "A campaign has no field called {name}."
LENGTHS = "Clip lengths must be between 5 and 600 seconds."
# What each field needs, said when a value doesn't fit it and its check gives no sentence of its own
PROBLEMS = {
    "name": "Give the campaign a name of up to 80 characters.",
    "marketplace": "Say which marketplace the campaign is on, in up to 40 characters.",
    "rate_per_1000": "The rate per 1,000 views must be a number of 0 or more.",
    "min_views": "The minimum views must be a whole number of 0 or more.",
    "max_payout": "The most paid per clip must be a number of 0 or more.",
    "budget_left": "The budget left must be a number of 0 or more.",
    "platforms": "Pick TikTok, Instagram or both.",
    "min_s": LENGTHS,
    "max_s": LENGTHS,
    "footage_links": "A campaign can name up to 20 footage channels.",
    "footage.videos": "A campaign can name up to 50 footage videos.",
    "required.hashtags": "A caption can need up to 10 hashtags of up to 100 characters each.",
    "required.mentions": "A caption can need up to 10 mentions, each like @name.",
    "required.text": "A caption can need up to 5 lines of text of up to 200 characters each.",
    "banned": "That isn't something a campaign can ban.",
    "hand_rules": "A campaign can have up to 20 rules of up to 200 characters each.",
    "notes": "Notes can be up to 4,000 characters.",
    "rules_text": "The rules text can be up to 20,000 characters.",
    "status": "A campaign is active or ended.",
}
OTHER = "That value doesn't fit this field."


class Refused(ClipForgeError):
    """A save refused, with the field the sentence is about."""

    def __init__(self, field: str, message: str):
        super().__init__("campaign_field", message)
        self.field = field


# Saving


def create(engine: Engine, fields: dict[str, Any], *, brief_id: Any = None) -> Campaign:
    """A new campaign from the form's ``fields``, and with ``brief_id`` from that brief (Use this), which
    then becomes the campaign's. Refused, naming the field, for a value that doesn't fit, a footage link
    that isn't a YouTube channel or couldn't be looked up, or a brief that isn't here, is a campaign
    already or has gone; nothing is saved then."""
    now = engine.now().timestamp()
    brief = _usable(engine.db(), brief_id) if brief_id is not None else None
    made = _checked(engine, fields, None, now)
    if brief is not None:
        made = made.model_copy(update={"brief_id": brief.id} | _read_on(engine, made, brief))
    conn = engine.db()
    with engine.lock, transaction(conn):
        if brief_id is not None:
            _usable(conn, brief_id)  # again, in case it changed while the footage was looked up
            brief_store.update_brief(conn, brief_id, status="used", campaign_id=made.id)
        store.add_campaign(conn, made)
    engine.events.publish("campaign", {"id": made.id})
    if brief_id is not None:
        engine.events.publish("brief", {"ids": [brief_id], "status": "used"})
    return made


def _usable(conn: sqlite3.Connection, brief_id: Any) -> Brief:
    brief = brief_store.get_brief(conn, brief_id) if isinstance(brief_id, str) else None
    if brief is None:
        raise Refused("brief_id", MISSING)
    if brief.status in FIXED:
        raise Refused("brief_id", FIXED[brief.status])
    return brief


def _read_on(engine: Engine, made: Campaign, brief: Brief) -> dict[str, Any]:
    """The budget dated the day the brief's read found it, when the form kept the brief's figure."""
    if made.budget_left is None or made.budget_left != brief.budget_left or brief.budget_read_at is None:
        return {}
    return {"budget_entered_on": tracker.day_of(engine, brief.budget_read_at).isoformat()}


def joined(engine: Engine, campaign_id: str, value: bool) -> Campaign:
    """The campaign marked joined on its marketplace now, or not joined for False. LookupError when there
    is no such campaign."""
    conn = engine.db()
    with engine.lock, transaction(conn):
        if store.get_campaign(conn, campaign_id) is None:
            raise LookupError(campaign_id)
        saved = store.update_campaign(
            conn, campaign_id, joined_at=engine.now().timestamp() if value else None
        )
    engine.events.publish("campaign", {"id": campaign_id})
    return saved


def update(engine: Engine, campaign_id: str, fields: dict[str, Any]) -> Campaign:
    """The campaign with ``fields`` changed, checked as ``create`` checks them, and each linked clipping
    channel's footage brought up to date. LookupError when there is no such campaign."""
    current = store.get_campaign(engine.db(), campaign_id)
    if current is None:
        raise LookupError(campaign_id)
    checked = _checked(engine, fields, current, engine.now().timestamp())
    conn = engine.db()
    with engine.lock, transaction(conn):
        if store.get_campaign(conn, campaign_id) is None:  # deleted while its footage was looked up
            raise LookupError(campaign_id)
        saved = store.update_campaign(
            conn, campaign_id, **checked.model_dump(exclude={"id", "created_at", "updated_at"})
        )
        sync_footage(engine, saved)
    engine.events.publish("campaign", {"id": campaign_id})
    return saved


def delete(engine: Engine, campaign_id: str) -> None:
    """Deletes the campaign, its clipping channels and projects unlinked first and the brief it was made
    from seen again, so it can be used once more. Refused once it has submissions, which keep their
    campaign: it is marked ended instead."""
    conn = engine.db()
    with engine.lock, transaction(conn):
        if store.get_campaign(conn, campaign_id) is None:
            raise LookupError(campaign_id)
        n = len(store.list_submissions(conn, campaign_id))
        if n:
            what = "submission" if n == 1 else "submissions"
            raise ClipForgeError("has_submissions", HAS_SUBMISSIONS.format(n=n, what=what))
        channels = store.linked_channels(conn, campaign_id)
        for channel_id in channels:
            _unlink(conn, channel_id, campaign_id)
        projects = store.linked_projects(conn, campaign_id)
        for project_id in projects:
            repo.update_project(conn, project_id, campaign_id=None, rights_basis="not_cleared")
        freed = [b.id for b in brief_store.briefs(conn, campaign_id=campaign_id)]
        for brief_id in freed:
            brief_store.update_brief(conn, brief_id, status="seen", campaign_id=None)
        store.delete_campaign(conn, campaign_id)
    for channel_id in channels:
        engine.events.publish("clip_channel", {"id": channel_id})
    for project_id in projects:
        engine.update_project(project_id)  # tells everyone listening
    if freed:
        engine.events.publish("brief", {"ids": freed, "status": "seen"})
    engine.events.publish("campaign", {"id": campaign_id})


def _checked(engine: Engine, given: dict[str, Any], current: Campaign | None, now: float) -> Campaign:
    """The campaign ``given`` makes of ``current`` (or of a new one): every field checked first, then the
    footage links looked up, and the budget dated when it changes."""
    for name in given:
        if name not in FORM:
            raise Refused(name, UNKNOWN.format(name=clean_text(str(name))))
    if current is None:
        start = Campaign(id=repo.new_id("cp"), name="c", marketplace="c", created_at=now, updated_at=now)
        data = start.model_dump() | {"name": "", "marketplace": ""}
    else:
        data = current.model_dump()
    links = given.get("footage_links")
    for name, value in given.items():
        if name not in ("footage", "footage_links"):
            data[name] = _tidy(name, value)
    footage = given.get("footage")
    if isinstance(footage, dict):
        if "videos" in footage:
            data["footage"]["videos"] = _videos(footage["videos"])
        if "files" in footage:
            data["footage"]["files"] = footage["files"]
    elif footage is not None:
        raise Refused("footage", OTHER)
    if links is not None:
        data["footage"]["channels"] = []  # checked without them, so nothing is looked up for a bad save
    _valid(data)
    if links is not None:
        known = current.footage.channels if current else []
        data["footage"]["channels"] = [c.model_dump() for c in _channels(engine, links, known)]
    budget = data["budget_left"]
    if budget is None:
        data["budget_entered_on"] = None
    elif current is None or budget != current.budget_left:
        data["budget_entered_on"] = today(engine)
    return _valid(data)


def _tidy(name: str, value: Any) -> Any:
    """``value`` with control characters and turnaround marks gone from its text, for field ``name``."""
    if name in ("notes", "rules_text"):
        return (clean_lines(value) or "").strip() if isinstance(value, str) else value
    if name == "hand_rules":
        return parse.lines([v for v in value if isinstance(v, str)]) if isinstance(value, list) else value
    if name == "required" and isinstance(value, dict):
        tidy = dict(value)
        for key, clean in (("hashtags", clean_hashtags), ("mentions", parse.mentions), ("text", parse.lines)):
            if isinstance(tidy.get(key), list):
                tidy[key] = clean([v for v in tidy[key] if isinstance(v, str)])
        return tidy
    if not isinstance(value, str):
        return value
    text = (clean_text(value) or "").strip()
    if not text and name in ("campaign_url", "submit_url", "ends_on"):
        return None
    return text


def _videos(values: Any) -> Any:
    """The footage videos by their ids, from ids or YouTube video links, each once."""
    if not isinstance(values, list):
        return values
    ids: list[str] = []
    for value in values:
        text = (clean_text(value) or "").strip() if isinstance(value, str) else ""
        if not text:
            continue
        video = text if VIDEO_ID.fullmatch(text) else youtube_id(text)
        if video is None:
            raise Refused("footage.videos", NOT_VIDEO.format(link=text))
        if video not in ids:
            ids.append(video)
    return ids


def _valid(data: dict[str, Any]) -> Campaign:
    try:
        return Campaign.model_validate(data)
    except ValidationError as err:
        first = err.errors()[0]
        names = [p for p in first["loc"] if isinstance(p, str)][:2]
        field = ".".join(names) or "min_s"  # the one check of the whole campaign is of its lengths
        if field == "footage.channels":
            field = "footage_links"
        if first["type"] == "value_error":
            message = str(first["ctx"]["error"])
        else:
            message = PROBLEMS.get(field) or PROBLEMS.get(names[0] if names else "", OTHER)
        raise Refused(field, message) from None


def _channels(engine: Engine, links: Any, known: list[FootageChannel]) -> list[FootageChannel]:
    """The YouTube channel of each footage link, once each: one the campaign already has is kept as it
    is, and each other one is looked up once. Every link is checked before anything is looked up."""
    if not isinstance(links, list):
        raise Refused("footage_links", OTHER)
    wanted: dict[str, str] = {}  # each channel's videos tab, and the link as it was given
    for link in links:
        text = (clean_text(link) or "").strip() if isinstance(link, str) else ""
        if not text:
            continue
        tab = channel_link(text)
        if tab is None:
            raise Refused("footage_links", NOT_CHANNEL.format(link=text))
        wanted.setdefault(tab, text)
    if len(wanted) > parse.CHANNELS_MAX:
        raise Refused("footage_links", PROBLEMS["footage_links"])
    have = {c.url: c for c in known}
    found: dict[str, FootageChannel] = {}
    for tab, text in wanted.items():
        channel = have.get(tab) or _look_up(engine, tab, text)
        found.setdefault(channel.yt_channel_id, channel)
    return list(found.values())


def _look_up(engine: Engine, tab: str, link: str) -> FootageChannel:
    try:
        listing = engine.youtube.list_channel(tab, 1)
    except ClipForgeError as err:
        raise Refused("footage_links", NOT_FOUND.format(link=link, why=err.message)) from None
    if not UC_ID.fullmatch(listing.channel_id):
        raise Refused("footage_links", NOT_FOUND.format(link=link, why="YouTube gave no channel for it."))
    return FootageChannel(
        yt_channel_id=listing.channel_id,
        title=(clean_text(listing.title) or "").strip()[:TITLE_MAX],
        url=f"https://www.youtube.com/channel/{listing.channel_id}/videos",
    )


# Linking


def footage_allows(campaign: Campaign, project: Project) -> str | None:
    """None when the campaign's footage rule allows the project's video, else the sentence saying why not.
    A YouTube project's channel comes from its channel link. Before its video is read no one knows its
    channel, so a rule of channels lets it through then, and QA checks the channel on each of its drafts."""
    footage = campaign.footage
    if project.source_kind in ("file", "drive"):  # a Drive link is footage the brief or you supplied
        return None if footage.any or footage.files else NO_FILES.format(name=campaign.name)
    if footage.allows(project.video_id, _channel_of(project.channel_url)):
        return None
    if footage.channels and project.channel_url is None and project.duration_s is None:
        return None
    return NOT_ALLOWED.format(name=campaign.name)


def _channel_of(url: str | None) -> str | None:
    segments = [s for s in _split_url(url).path.split("/") if s] if url else []
    if len(segments) >= 2 and segments[0] == "channel" and UC_ID.fullmatch(segments[1]):
        return segments[1]
    return None


def link_channel(engine: Engine, channel_id: str, campaign_id: str | None) -> ClipChannel:
    """Links the clipping channel to the campaign, or unlinks it for None. Linked, it watches each footage
    channel the campaign names and has its footage videos waiting to be clipped, and the projects it
    starts from then on are the campaign's. Unlinked, its rights basis is Not cleared again and the
    campaign's videos it hadn't started are dropped. LookupError for a missing channel or campaign."""
    conn = engine.db()
    with engine.lock, transaction(conn):
        channel = watch_store.get_channel(conn, channel_id)
        if channel is None:
            raise LookupError(channel_id)
        campaign = store.get_campaign(conn, campaign_id) if campaign_id else None
        if campaign_id and campaign is None:
            raise LookupError(campaign_id)
        if channel.campaign_id and channel.campaign_id != campaign_id:
            _unlink(conn, channel.id, channel.campaign_id)
        if campaign is not None:
            watch_store.update_channel(
                conn, channel.id, campaign_id=campaign.id, rights_basis="clipping_programme"
            )
            _sync(engine, channel.id, campaign)
    engine.events.publish("clip_channel", {"id": channel_id})
    linked = watch_store.get_channel(conn, channel_id)
    assert linked is not None
    return linked


def link_project(engine: Engine, project_id: str, campaign_id: str | None) -> Project:
    """Links the project to the campaign when its footage rule allows the project's video, or unlinks it
    for None, which makes its rights basis Not cleared again. ClipForgeError with the sentence when the
    rule doesn't allow it; LookupError for a missing project or campaign."""
    project = engine.project(project_id)
    if project is None:
        raise LookupError(project_id)
    if campaign_id is None:
        if project.campaign_id is not None:
            engine.update_project(project_id, campaign_id=None, rights_basis="not_cleared")
    else:
        campaign = store.get_campaign(engine.db(), campaign_id)
        if campaign is None:
            raise LookupError(campaign_id)
        refusal = footage_allows(campaign, project)
        if refusal is not None:
            raise ClipForgeError("not_allowed", refusal)
        engine.update_project(project_id, campaign_id=campaign.id, rights_basis="clipping_programme")
    linked = engine.project(project_id)
    assert linked is not None
    return linked


def sync_footage(engine: Engine, campaign: Campaign) -> None:
    """Brings each clipping channel linked to the campaign up to date with its footage rule."""
    conn = engine.db()
    with engine.lock, transaction(conn):
        channels = store.linked_channels(conn, campaign.id)
        for channel_id in channels:
            _sync(engine, channel_id, campaign)
    for channel_id in channels:
        engine.events.publish("clip_channel", {"id": channel_id})


def _sync(engine: Engine, channel_id: str, campaign: Campaign) -> None:
    """Inside a transaction: a source, from now on, for each footage channel the clipping channel doesn't
    watch yet; the footage videos it hasn't seen as new items of the campaign's hidden source, the first
    listed sorting first; and the ones no longer listed, not yet started, skipped."""
    conn = engine.db()
    now = engine.now().timestamp()
    watched = watch_store.list_sources(conn, channel_id)
    have = {s.yt_channel_id for s in watched}
    for channel in campaign.footage.channels:
        if channel.yt_channel_id not in have:
            watch_store.add_source(
                conn,
                WatchedSource(
                    id=repo.new_id("ws"),
                    clip_channel_id=channel_id,
                    yt_channel_id=channel.yt_channel_id,
                    title=channel.title or channel.yt_channel_id,
                    url=channel.url,
                    start_published_at=now,
                    created_at=now,
                ),
            )
    key = footage_key(campaign.id)
    source = next((s for s in watched if s.yt_channel_id == key), None)
    videos = campaign.footage.videos
    if source is None and videos:
        source = watch_store.add_source(
            conn,
            WatchedSource(
                id=repo.new_id("ws"),
                clip_channel_id=channel_id,
                yt_channel_id=key,
                title=campaign.name,
                url="",
                start_published_at=now,
                created_at=now,
            ),
        )
    if source is None:
        return
    for video_id in reversed(videos):
        item = watch_store.item_for_video(conn, channel_id, video_id)
        if item is None:
            watch_store.add_item(
                conn,
                WatchItem(
                    id=repo.new_id("wi"),
                    source_id=source.id,
                    clip_channel_id=channel_id,
                    video_id=video_id,
                    status="new",
                    first_seen_at=now,
                    updated_at=now,
                ),
            )
        elif item.source_id == source.id and item.reason == "not_allowed":  # listed again
            watch_store.update_item(conn, item.id, status="new", reason=None, check_id=None)
    for item in watch_store.items_for(conn, channel_id, status=("new", "waiting")):
        if item.source_id == source.id and item.video_id not in videos:
            watch_store.update_item(conn, item.id, status="skipped", reason="not_allowed")


def _unlink(conn: sqlite3.Connection, channel_id: str, campaign_id: str) -> None:
    """Inside a transaction: the clipping channel unlinked from the campaign, its rights basis Not
    cleared, and the campaign's footage videos it hadn't started dropped. Their hidden source goes too
    once nothing it held is kept. The YouTube channels it watches stay."""
    watch_store.update_channel(conn, channel_id, campaign_id=None, rights_basis="not_cleared")
    key = footage_key(campaign_id)
    source = next((s for s in watch_store.list_sources(conn, channel_id) if s.yt_channel_id == key), None)
    if source is None:
        return
    kept = 0
    for item in watch_store.items_for(conn, channel_id):
        if item.source_id != source.id:
            continue
        if item.status in ("new", "waiting") or item.reason == "not_allowed":
            watch_store.delete_item(conn, item.id)
        else:
            kept += 1
    if not kept:
        watch_store.delete_source(conn, source.id)
