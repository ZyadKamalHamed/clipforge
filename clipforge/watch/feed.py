"""A YouTube channel's public feed of its latest uploads (SPEC 10.9): one GET, no login, no key, no cookies,
read under a 2 MB cap with the standard library's XML parser. Everything in it is untrusted text (17)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime

import httpx

from clipforge import __version__
from clipforge.errors import ClipForgeError, YouTubeRateLimited
from clipforge.ingest.ytdlp import RATE_LIMITED
from clipforge.untrusted import clean_text
from clipforge.watch.models import TITLE_MAX, FeedEntry

FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
MAX_FEED_BYTES = 2 * 1024 * 1024
TIMEOUT = 20.0
USER_AGENT = f"ClipForge/{__version__}"
UNREADABLE = "YouTube's feed for this channel couldn't be read, so this check used the channel page instead."

_ATOM = "{http://www.w3.org/2005/Atom}"
_YT = "{http://www.youtube.com/xml/schemas/2015}"
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_CHANNEL_ID = re.compile(r"UC[A-Za-z0-9_-]{22}")
_DECLARATIONS = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)


class FeedUnreadable(ClipForgeError):
    """The feed answered with something other than a feed: an error, a page, too much, or nothing."""

    def __init__(self, details: str | None = None):
        super().__init__("feed_unreadable", UNREADABLE, details=details)


class FeedRateLimited(YouTubeRateLimited):
    def __init__(self) -> None:
        super().__init__("rate_limited", RATE_LIMITED)


def parse_feed(data: bytes) -> list[FeedEntry]:
    """The feed's uploads, newest first by ``published``. Raises FeedUnreadable for anything but an Atom
    feed under the cap, and for any DOCTYPE or ENTITY, which no YouTube feed has."""
    if len(data) > MAX_FEED_BYTES:
        raise FeedUnreadable(f"{len(data)} bytes")
    if _DECLARATIONS.search(data):
        raise FeedUnreadable("a DOCTYPE or ENTITY declaration")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as err:
        raise FeedUnreadable(str(err)) from None
    if root.tag != f"{_ATOM}feed":
        raise FeedUnreadable(f"root {root.tag[:80]}")
    entries = []
    for entry in root.iterfind(f"{_ATOM}entry"):
        video_id = (entry.findtext(f"{_YT}videoId") or "").strip()
        published = _when(entry.findtext(f"{_ATOM}published"))
        if not _VIDEO_ID.fullmatch(video_id) or published is None:
            continue
        link = entry.find(f"{_ATOM}link[@rel='alternate']")
        href = link.get("href", "") if link is not None else ""
        title = (clean_text(entry.findtext(f"{_ATOM}title") or "") or "").strip()[:TITLE_MAX]
        entries.append(
            FeedEntry(video_id=video_id, title=title, published_at=published, is_short="/shorts/" in href)
        )
    return sorted(entries, key=lambda e: e.published_at, reverse=True)


def _when(text: str | None) -> float | None:
    try:
        return datetime.fromisoformat((text or "").strip()).timestamp()
    except ValueError:
        return None


class FeedClient:
    """Reads one channel's feed. ``transport`` is httpx's, so tests answer without the network."""

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self.transport = transport

    def entries(self, channel_id: str) -> list[FeedEntry]:
        """Raises FeedRateLimited on a 429 and FeedUnreadable on anything else that isn't a feed."""
        if not _CHANNEL_ID.fullmatch(channel_id):
            raise ValueError(f"not a channel id: {channel_id[:40]!r}")
        headers = {"User-Agent": USER_AGENT, "Accept": "application/atom+xml, text/xml"}
        try:
            with (
                httpx.Client(
                    transport=self.transport, timeout=TIMEOUT, follow_redirects=False, headers=headers
                ) as client,
                client.stream("GET", FEED_URL.format(channel_id)) as answer,
            ):
                if answer.status_code == 429:
                    raise FeedRateLimited()
                if answer.status_code != 200:
                    raise FeedUnreadable(f"HTTP {answer.status_code}")
                body = bytearray()
                for chunk in answer.iter_bytes():
                    body += chunk
                    if len(body) > MAX_FEED_BYTES:
                        raise FeedUnreadable(f"over {MAX_FEED_BYTES} bytes")
        except httpx.HTTPError as err:
            raise FeedUnreadable(f"{type(err).__name__}: {err}") from None
        return parse_feed(bytes(body))
