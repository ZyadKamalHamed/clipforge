from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from clipforge.errors import YouTubeRateLimited
from clipforge.watch import feed
from clipforge.watch.feed import FeedClient, FeedRateLimited, FeedUnreadable, parse_feed

FEEDS = Path(__file__).parent.parent / "fixtures" / "feeds"
UC = "UCabcdefghijklmnopqrstuv"


def answer(body: bytes, status: int = 200, seen: list | None = None):
    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, content=body, headers={"content-type": "text/xml; charset=UTF-8"})

    return httpx.MockTransport(handle)


def test_parses_videos_and_marks_shorts():
    entries = parse_feed((FEEDS / "channel.xml").read_bytes())
    assert [e.video_id for e in entries] == ["ddddddddd-4", "ccccccccc3_", "bbbbbbbbbb2", "aaaaaaaaaa1"]
    assert [e.is_short for e in entries] == [True, False, False, False]
    assert all(len(e.video_id) == 11 for e in entries)
    # published, never updated, which moves with the view count
    assert entries[2].published_at == datetime(2026, 10, 26, 9, tzinfo=UTC).timestamp()
    assert entries[2].title == "Episode 2 & friends"


def test_titles_are_cleaned():
    title = parse_feed((FEEDS / "channel.xml").read_bytes())[3].title
    assert title == "Episode 1  with a mark"
    assert "\u202e" not in title and "\u009b" not in title


def test_long_titles_are_cut():
    body = (FEEDS / "channel.xml").read_bytes().replace(b"Episode 2 &amp; friends", b"x" * 5000)
    assert len(parse_feed(body)[2].title) == 300


def test_feed_channel_id_without_uc_is_ignored():
    # the feed's own yt:channelId leaves out the UC prefix (SPEC 10.9); entries don't depend on it
    assert b"<yt:channelId>abcdefghijklmnopqrstuv" in (FEEDS / "channel.xml").read_bytes()
    assert len(parse_feed((FEEDS / "channel.xml").read_bytes())) == 4


def test_entries_without_a_good_id_or_date_are_dropped():
    body = (FEEDS / "channel.xml").read_bytes()
    body = body.replace(b"<yt:videoId>bbbbbbbbbb2</yt:videoId>", b"<yt:videoId>bad id</yt:videoId>")
    body = body.replace(b"<published>2026-10-27T09:00:00+00:00</published>", b"<published>soon</published>")
    assert [e.video_id for e in parse_feed(body)] == ["ddddddddd-4", "aaaaaaaaaa1"]


def test_empty_feed_is_no_entries():
    assert parse_feed((FEEDS / "empty.xml").read_bytes()) == []


def test_doctype_feed_is_refused():
    with pytest.raises(FeedUnreadable):
        parse_feed((FEEDS / "doctype.xml").read_bytes())


def test_html_answer_is_unreadable():
    with pytest.raises(FeedUnreadable):
        parse_feed(b"<!doctype html><html><body>Before you continue to YouTube</body></html>")
    with pytest.raises(FeedUnreadable):
        parse_feed(b"<html><body><p>Not a feed</p></body></html>")
    with pytest.raises(FeedUnreadable):
        parse_feed(b"")


def test_oversized_feed_is_refused():
    big = (
        (FEEDS / "empty.xml")
        .read_bytes()
        .replace(b"</feed>", b"<!--" + b"x" * (3 * 1024 * 1024) + b"--></feed>")
    )
    client = FeedClient(answer(big))
    with pytest.raises(FeedUnreadable):
        client.entries(UC)
    with pytest.raises(FeedUnreadable):
        parse_feed(big)


def test_429_is_rate_limited():
    client = FeedClient(answer(b"", status=429))
    with pytest.raises(FeedRateLimited) as err:
        client.entries(UC)
    assert isinstance(err.value, YouTubeRateLimited)
    assert err.value.code == "rate_limited"


@pytest.mark.parametrize("status", [404, 500, 503, 302])
def test_other_answers_are_unreadable(status):
    client = FeedClient(answer(b"<html></html>", status=status))
    with pytest.raises(FeedUnreadable) as err:
        client.entries(UC)
    assert err.value.message == feed.UNREADABLE


def test_network_trouble_is_unreadable():
    def down(request):
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(FeedUnreadable):
        FeedClient(httpx.MockTransport(down)).entries(UC)


def test_request_has_no_cookie_and_the_uc_id():
    seen: list[httpx.Request] = []
    entries = FeedClient(answer((FEEDS / "channel.xml").read_bytes(), seen=seen)).entries(UC)
    assert len(entries) == 4
    (request,) = seen
    assert str(request.url) == f"https://www.youtube.com/feeds/videos.xml?channel_id={UC}"
    assert request.method == "GET"
    assert "cookie" not in request.headers
    assert request.headers["user-agent"].startswith("ClipForge/")


def test_a_bad_channel_id_never_reaches_youtube():
    seen: list[httpx.Request] = []
    with pytest.raises(ValueError):
        FeedClient(answer(b"", seen=seen)).entries("UC&x=1")
    assert seen == []
