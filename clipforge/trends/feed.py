"""The weekly "new list out" ping (SPEC 13). Later, Buffer and SocialPilot each post a list of the sounds
trending on TikTok and Instagram, and the Trends page links to those four lists for you to open in your
browser. ClipForge never reads them: once a week it reads SocialPilot's public RSS feed, as Polite reads a
campaign site (one GET, robots.txt obeyed, no cookies, the honest user agent), keeps only the date of its
newest post, and shows a badge when that is newer than the last time you opened a list. A read robots.txt
refuses, or that fails, waits the week like any other; there is no retry in between."""

from __future__ import annotations

from datetime import UTC
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING
from xml.etree import ElementTree

from clipforge.briefs import fetch
from clipforge.trends import store
from clipforge.trends.models import TrendRead

if TYPE_CHECKING:
    from clipforge.app import Engine

FEED = "https://www.socialpilot.co/feed"
FEED_TYPES = ("application/rss+xml", "application/xml", "text/xml")
LISTS = {
    "later": "https://later.com/blog/instagram-reels-trends/",
    "buffer": "https://buffer.com/resources/trending-audio-instagram/",
    "socialpilot_tiktok": "https://www.socialpilot.co/blog/tiktok-trends",
    "socialpilot_instagram": "https://www.socialpilot.co/blog/instagram-reels-trends",
}
LABELS = {  # how the Trends page names each list
    "later": "Later: Instagram Reels trends",
    "buffer": "Buffer: trending audio on Instagram",
    "socialpilot_tiktok": "SocialPilot: TikTok trends",
    "socialpilot_instagram": "SocialPilot: Instagram Reels trends",
}
READ_ID = "feed:socialpilot"
WEEK_S = 7 * 86400.0


def newest_date(data: bytes | str) -> float | None:
    """The publication date of the feed's newest post, as epoch seconds, or None when it has no post with
    a date that reads. Only each item's own ``pubDate`` counts. A feed that declares a DOCTYPE or entities
    is refused before it is parsed, since a feed has no need of either."""
    text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data
    text = text.lstrip("﻿")
    if "<!doctype" in text.casefold() or "<!entity" in text.casefold():
        return None
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return None
    newest: float | None = None
    for item in root.iter("item"):
        stamp = item.findtext("pubDate")
        if not stamp:
            continue
        try:
            when = parsedate_to_datetime(stamp.strip())
        except (TypeError, ValueError, IndexError, OverflowError):
            continue
        at = (when if when.tzinfo is not None else when.replace(tzinfo=UTC)).timestamp()
        newest = at if newest is None else max(newest, at)
    return newest


def tick(engine: Engine) -> bool:
    """The timer's look at the clock: reads the feed when the ping is on and the last read is a week old or
    more. Whether it tried, whatever robots.txt or the site said; the date found before is kept when the
    read finds none."""
    if not engine.settings.trends.list_ping:
        return False
    conn = engine.db()
    last = store.read_of(conn, READ_ID) or TrendRead(id=READ_ID)
    if last.read_at is not None and engine.now().timestamp() - last.read_at < WEEK_S:
        return False
    try:
        fetched = fetch.Polite(engine, FEED).get(FEED, page=False, accept=FEED_TYPES)
    except fetch.Stop as stop:
        result = "blocked" if stop.result == "blocked" else "error"
        read = last.model_copy(update={"result": result, "error": stop.message})
    else:
        newest = newest_date(fetched.text)
        found = {"newest_at": newest} if newest is not None else {}
        read = last.model_copy(update={"result": "ok", "error": None} | found)
    read.read_at = engine.now().timestamp()  # after the waits between the site's requests
    store.save_read(conn, read)
    engine.events.publish("trends", {"list": read.result})
    return True


def badge(engine: Engine) -> bool:
    """Whether a list is newer than your last look: you never opened one, or the feed's newest post came
    after you last did."""
    read = store.read_of(engine.db(), READ_ID)
    if read is None or read.seen_at is None:
        return True
    return read.newest_at is not None and read.newest_at > read.seen_at


def mark_seen(engine: Engine) -> None:
    """Notes that you opened the lists, which covers every post the feed has shown so far, one dated ahead
    of the clock too."""
    conn = engine.db()
    read = store.read_of(conn, READ_ID) or TrendRead(id=READ_ID)
    now = engine.now().timestamp()
    read.seen_at = max(now, read.newest_at) if read.newest_at is not None else now
    store.save_read(conn, read)
