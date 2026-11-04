"""The weekly list ping (SPEC 13): one GET a week of SocialPilot's public feed with robots.txt obeyed, its
newest post's date only, so the Trends page can say a new list is out. No list page is ever read."""

from datetime import UTC, datetime

import pytest

from clipforge.briefs import fetch
from clipforge.config import TrendSettings
from clipforge.trends import feed, store
from tests.fakes import Clock, fake_engine
from tests.posting import at
from tests.unit.test_briefs_fetch import Site, page, pages_read, serve

ROBOTS = "https://www.socialpilot.co/robots.txt"
OPEN = page("User-agent: *\nAllow: /\n", kind="text/plain")
MON = "Mon, 02 Nov 2026 10:00:00 +0000"
TUE = "Tue, 03 Nov 2026 23:00:00 GMT"  # the newest: Wednesday's is 22:30 on Tuesday in UTC
WED = "Wed, 04 Nov 2026 09:30:00 +1100"
LATER = "Fri, 13 Nov 2026 08:00:00 +0000"


def rss(*dates: str, channel_date: str | None = None) -> bytes:
    items = "".join(f"<item><title>Trends</title><pubDate>{d}</pubDate></item>" for d in dates)
    own = f"<pubDate>{channel_date}</pubDate>" if channel_date else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Blog</title>'
        f"{own}{items}</channel></rss>"
    ).encode()


def feed_page(*dates: str, **kw):
    return page(rss(*dates, **kw), kind="application/rss+xml; charset=UTF-8")


def ts(text: str) -> float:
    return datetime.strptime(text.replace("GMT", "+0000"), "%a, %d %b %Y %H:%M:%S %z").timestamp()


@pytest.fixture
def clock():
    return Clock(at(7, 10))


@pytest.fixture
def engine(clock):
    eng = fake_engine(now=clock)
    yield eng
    eng.close()


def feed_reads(site) -> list:
    return [r for r in site.requests if r.url.path == "/feed"]


def test_newest_date_reads_only_dates():
    assert feed.newest_date(rss(MON, WED, TUE)) == datetime(2026, 11, 3, 23, 0, tzinfo=UTC).timestamp()
    # the channel's own date isn't a post, and items with no date or a broken one are passed over
    later = rss(MON, "not a date", channel_date=LATER).replace(b"<item>", b"<item><x/></item><item>", 1)
    assert feed.newest_date(later) == ts(MON)
    assert feed.newest_date(rss()) is None
    assert feed.newest_date(b"not xml") is None
    assert feed.newest_date(b"") is None
    # a feed that declares its own entities is refused before it is parsed
    bomb = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa">]><rss><channel><item><pubDate>&a;</pubDate>'
    assert feed.newest_date(bomb + b"</item></channel></rss>") is None
    assert feed.FEED == "https://www.socialpilot.co/feed"
    assert set(feed.LISTS) == {"later", "buffer", "socialpilot_tiktok", "socialpilot_instagram"}
    assert feed.LISTS["buffer"] == "https://buffer.com/resources/trending-audio-instagram/"


def test_feed_read_once_a_week(engine, clock):
    site = serve(engine, Site({ROBOTS: OPEN, feed.FEED: feed_page(MON, TUE)}))
    assert feed.tick(engine) is True
    read = store.read_of(engine.db(), feed.READ_ID)
    assert feed.READ_ID == "feed:socialpilot"
    assert (read.result, read.read_at, read.newest_at, read.error) == (
        "ok",
        clock().timestamp(),
        ts(TUE),
        None,
    )
    (asked,) = feed_reads(site)
    assert asked.method == "GET" and "cookie" not in asked.headers
    assert asked.headers["user-agent"] == fetch.USER_AGENT
    assert "application/rss+xml" in asked.headers["accept"]
    assert pages_read(engine, "socialpilot.co") == 0  # a ping, not one of the 40 brief pages
    clock.advance(days=3)
    assert feed.tick(engine) is False
    site.pages[feed.FEED] = feed_page(MON, TUE, LATER)
    clock.advance(days=5)
    assert feed.tick(engine) is True
    assert len(feed_reads(site)) == 2
    assert store.read_of(engine.db(), feed.READ_ID).newest_at == ts(LATER)
    # with the ping turned off nothing is read, however long it has been
    engine.update_settings(engine.settings.model_copy(update={"trends": TrendSettings(list_ping=False)}))
    clock.advance(days=30)
    assert feed.tick(engine) is False
    assert len(feed_reads(site)) == 2


def test_feed_obeys_robots(engine, clock):
    site = serve(engine, Site({ROBOTS: OPEN, feed.FEED: feed_page(TUE)}))
    feed.tick(engine)
    site = serve(engine, Site({ROBOTS: page("User-agent: *\nDisallow: /feed\n", kind="text/plain")}))
    clock.advance(days=7)
    assert feed.tick(engine) is True  # it looked, and robots.txt said no
    assert site.paths() == ["/robots.txt"]
    read = store.read_of(engine.db(), feed.READ_ID)
    assert (read.result, read.error, read.newest_at) == ("blocked", fetch.ROBOTS, ts(TUE))
    clock.advance(days=1)
    assert feed.tick(engine) is False  # no retry in between, and no way around it
    assert site.paths() == ["/robots.txt"]
    # a broken answer is an error that keeps the last date too
    for answer, sentence in (
        (page("", status=500), "The site had a problem (HTTP 500)."),
        (page("<p>Hello</p>"), fetch.NOT_A_PAGE),
    ):
        serve(engine, Site({ROBOTS: OPEN, feed.FEED: answer}))
        clock.advance(days=7)
        assert feed.tick(engine) is True
        read = store.read_of(engine.db(), feed.READ_ID)
        assert (read.result, read.error, read.newest_at) == ("error", sentence, ts(TUE))


def test_badge_until_seen(engine, clock):
    assert feed.badge(engine) is True  # the lists were never opened
    feed.mark_seen(engine)
    assert feed.badge(engine) is False
    assert store.read_of(engine.db(), feed.READ_ID).seen_at == clock().timestamp()
    serve(engine, Site({ROBOTS: OPEN, feed.FEED: feed_page(LATER)}))
    clock.set(at(7, 10, day=14))  # Friday's post is out by then
    feed.tick(engine)
    assert feed.badge(engine) is True  # a post newer than the last look
    feed.mark_seen(engine)
    assert feed.badge(engine) is False
    read = store.read_of(engine.db(), feed.READ_ID)
    assert (read.newest_at, read.result) == (ts(LATER), "ok")  # seeing keeps what the read found
    clock.advance(days=7)
    feed.tick(engine)  # the same newest post
    assert feed.badge(engine) is False
    # a post dated ahead of the clock doesn't keep the badge on once you have looked
    serve(engine, Site({ROBOTS: OPEN, feed.FEED: feed_page("Fri, 01 Jan 2100 00:00:00 +0000")}))
    clock.advance(days=7)
    feed.tick(engine)
    assert feed.badge(engine) is True
    feed.mark_seen(engine)
    assert feed.badge(engine) is False
