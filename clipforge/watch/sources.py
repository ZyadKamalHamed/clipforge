"""Adding the YouTube channels a clipping channel watches (SPEC 10.9). A source starts from the moment it is
added: only uploads after that are new, except the few recent ones a backfill asks for."""

from __future__ import annotations

import re
import sqlite3
from typing import TYPE_CHECKING

from clipforge import repo
from clipforge.campaigns import store as campaigns
from clipforge.db import transaction
from clipforge.errors import ClipForgeError
from clipforge.ingest.urls import _split_url, youtube_id
from clipforge.ingest.ytdlp import NO_CHANNEL
from clipforge.untrusted import clean_text
from clipforge.watch import store
from clipforge.watch.models import SKIP_SENTENCES, TITLE_MAX, ChannelListing, WatchedSource, WatchItem

if TYPE_CHECKING:
    from clipforge.app import Engine

BACKFILL_MAX = 5
NOT_A_CHANNEL = (
    "That isn't a YouTube channel or video link. Paste a link like youtube.com/@name or one of its videos."
)
BACKFILL = f"Clip from 0 to {BACKFILL_MAX} of the channel's latest videos."
ALREADY = "This clipping channel already watches {title}."

_HANDLE = re.compile(r"@[A-Za-z0-9._-]{3,100}")
_NAME = re.compile(r"[A-Za-z0-9._-]{1,100}")
_CHANNEL_ID = re.compile(r"UC[A-Za-z0-9_-]{22}")
_HOSTS = ("youtube.com", "www.youtube.com", "m.youtube.com")


def channel_link(text: str) -> str | None:
    """The channel's videos tab for an @handle, a /channel/UC..., /c/ or /user/ link, or a bare @handle;
    None for anything else, video links included."""
    text = text.strip()
    if _HANDLE.fullmatch(text):
        return f"https://www.youtube.com/{text}/videos"
    if not text:
        return None
    parts = _split_url(text)
    if parts.scheme not in ("http", "https") or (parts.hostname or "") not in _HOSTS:
        return None
    segments = [s for s in parts.path.split("/") if s]
    if segments and _HANDLE.fullmatch(segments[0]):
        return f"https://www.youtube.com/{segments[0]}/videos"
    if len(segments) >= 2 and segments[0] == "channel" and _CHANNEL_ID.fullmatch(segments[1]):
        return f"https://www.youtube.com/channel/{segments[1]}/videos"
    if len(segments) >= 2 and segments[0] in ("c", "user") and _NAME.fullmatch(segments[1]):
        return f"https://www.youtube.com/{segments[0]}/{segments[1]}/videos"
    return None


def _videos_tab(yt_channel_id: str) -> str:
    return f"https://www.youtube.com/channel/{yt_channel_id}/videos"


def _title(text: object) -> str:
    return (clean_text(text) if isinstance(text, str) else "").strip()[:TITLE_MAX]


def _channel_of_video(info: dict) -> tuple[str, str]:
    """The UC id and name of the channel a video's info names, from channel_id or else channel_url."""
    channel_id = info.get("channel_id") or ""
    if not _CHANNEL_ID.fullmatch(channel_id):
        url = info.get("channel_url") or ""
        segments = [s for s in _split_url(url).path.split("/") if s] if url else []
        channel_id = segments[1] if len(segments) >= 2 and segments[0] == "channel" else ""
    if not _CHANNEL_ID.fullmatch(channel_id):
        raise ClipForgeError("unavailable", NO_CHANNEL)
    return channel_id, _title(info.get("channel") or info.get("uploader"))


def add_source(engine: Engine, channel_id: str, link: str, backfill: int = 0) -> WatchedSource:
    """Starts watching the YouTube channel ``link`` names (a channel link, or one of its videos). A channel
    link costs one flat listing; a video link one info lookup, plus a listing only for a backfill. The
    ``backfill`` newest uploads become new items, clipped by the next check. Raises LookupError when the
    clipping channel is gone, and ClipForgeError with a sentence for a bad link, a failed lookup or a
    channel the clipping channel's campaign doesn't allow footage from."""
    clip = store.get_channel(engine.db(), channel_id)
    if clip is None:
        raise LookupError(channel_id)
    if not 0 <= backfill <= BACKFILL_MAX:
        raise ClipForgeError("bad_backfill", BACKFILL)
    url = channel_link(link)
    if url is None and youtube_id(link) is None:
        raise ClipForgeError("not_a_channel", NOT_A_CHANNEL)
    listing: ChannelListing | None = None
    if url is not None:
        listing = engine.youtube.list_channel(url, max(backfill, 1))
        yt_channel_id, title = listing.channel_id, _title(listing.title)
    else:
        yt_channel_id, title = _channel_of_video(engine.youtube.fetch_info(link.strip()))
        if backfill:
            listing = engine.youtube.list_channel(_videos_tab(yt_channel_id), backfill)
    title = title or yt_channel_id
    _refuse_twice(engine.db(), channel_id, yt_channel_id, title)
    campaign = campaigns.get_campaign(engine.db(), clip.campaign_id) if clip.campaign_id else None
    if campaign is not None and not campaign.footage.allows(None, yt_channel_id):
        raise ClipForgeError("not_allowed", SKIP_SENTENCES["not_allowed"].format(name=campaign.name))
    now = engine.now().timestamp()
    newest = listing.entries[0] if listing and listing.entries else None
    source = WatchedSource(
        id=repo.new_id("ws"),
        clip_channel_id=channel_id,
        yt_channel_id=yt_channel_id,
        title=title,
        url=_videos_tab(yt_channel_id),
        start_published_at=now,
        newest_video_id=newest[0] if newest else None,
        newest_title=_title(newest[1]) if newest else None,
        created_at=now,
    )
    with engine.lock, transaction(engine.db()):
        if store.get_channel(engine.db(), channel_id) is None:
            raise LookupError(channel_id)
        _refuse_twice(engine.db(), channel_id, yt_channel_id, title)
        store.add_source(engine.db(), source)
        backfilled = listing.entries[:backfill] if listing else []
        for video_id, video_title in reversed(backfilled):  # oldest first, so the newest sorts first
            if store.item_for_video(engine.db(), channel_id, video_id) is None:
                store.add_item(
                    engine.db(),
                    WatchItem(
                        id=repo.new_id("wi"),
                        source_id=source.id,
                        clip_channel_id=channel_id,
                        video_id=video_id,
                        title=_title(video_title),
                        status="new",
                        first_seen_at=now,
                        updated_at=now,
                    ),
                )
    engine.events.publish("clip_channel", {"id": channel_id})
    return source


def _refuse_twice(conn: sqlite3.Connection, channel_id: str, yt_channel_id: str, title: str) -> None:
    for source in store.list_sources(conn, channel_id):
        if source.yt_channel_id == yt_channel_id:
            raise ClipForgeError("already_watched", ALREADY.format(title=source.title or title))
