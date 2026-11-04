import time

import pytest

from clipforge import repo
from clipforge.errors import ClipForgeError, YouTubeRateLimited
from clipforge.ingest.ytdlp import RATE_LIMITED
from clipforge.watch import sources, store
from clipforge.watch.models import ChannelListing, ClipChannel
from clipforge.watch.sources import channel_link
from tests.fakes import FakeYouTube, fake_engine

UC = "UCabcdefghijklmnopqrstuv"
LISTING = ChannelListing(
    channel_id=UC,
    title="A Podcast",
    url=f"https://www.youtube.com/channel/{UC}/videos",
    entries=[("bbbbbbbbbb2", "Episode 2"), ("aaaaaaaaaa1", "Episode 1"), ("zzzzzzzzzz0", "Episode 0")],
)


@pytest.fixture
def youtube():
    yt = FakeYouTube(channel_id=UC, channel="A Podcast", channel_url=f"https://www.youtube.com/channel/{UC}")
    yt.listing = LISTING
    return yt


@pytest.fixture
def engine(youtube):
    eng = fake_engine(youtube=youtube)
    yield eng
    eng.close()


@pytest.fixture
def channel(engine):
    now = time.time()
    made = ClipChannel(id=repo.new_id("cc"), name="Podcasts", created_at=now, updated_at=now)
    return store.add_channel(engine.db(), made)


@pytest.mark.parametrize(
    "text, link",
    [
        ("https://www.youtube.com/@lexfridman", "https://www.youtube.com/@lexfridman/videos"),
        ("youtube.com/@lexfridman/featured", "https://www.youtube.com/@lexfridman/videos"),
        (
            "https://m.youtube.com/@Lex.Fridman_1/videos?view=0",
            "https://www.youtube.com/@Lex.Fridman_1/videos",
        ),
        ("@lexfridman", "https://www.youtube.com/@lexfridman/videos"),
        (f"https://www.youtube.com/channel/{UC}", f"https://www.youtube.com/channel/{UC}/videos"),
        (f"  https://youtube.com/channel/{UC}/streams  ", f"https://www.youtube.com/channel/{UC}/videos"),
        ("https://www.youtube.com/c/TED", "https://www.youtube.com/c/TED/videos"),
        (
            "https://www.youtube.com/user/TEDtalksDirector",
            "https://www.youtube.com/user/TEDtalksDirector/videos",
        ),
    ],
)
def test_channel_link_shapes(text, link):
    assert channel_link(text) == link


@pytest.mark.parametrize(
    "text",
    [
        "https://www.youtube.com/watch?v=arj7oStGLkU",
        "https://youtu.be/arj7oStGLkU",
        "https://www.youtube.com/playlist?list=PL123",
        "https://vimeo.com/@someone",
        "https://www.youtube.com/channel/UCshort",
        "https://www.youtube.com/@",
        "not a link",
        "",
    ],
)
def test_not_channel_links(text):
    assert channel_link(text) is None


def test_add_channel_link_records_the_starting_point(engine, youtube, channel):
    before = engine.now().timestamp()
    source = sources.add_source(engine, channel.id, "https://www.youtube.com/@apodcast")
    assert youtube.called("list_channel") == [("list_channel", "https://www.youtube.com/@apodcast/videos", 1)]
    assert youtube.called("fetch_info") == []
    assert (source.yt_channel_id, source.title, source.url) == (UC, "A Podcast", LISTING.url)
    assert (source.newest_video_id, source.newest_title) == ("bbbbbbbbbb2", "Episode 2")
    assert source.start_published_at >= before
    assert store.items_for(engine.db(), channel.id) == []
    assert store.get_source(engine.db(), source.id) == source


def test_add_video_link_uses_its_channel(engine, youtube, channel):
    source = sources.add_source(engine, channel.id, "https://www.youtube.com/watch?v=arj7oStGLkU")
    assert len(youtube.called("fetch_info")) == 1
    assert youtube.called("list_channel") == []
    assert (source.yt_channel_id, source.title) == (UC, "A Podcast")
    assert source.url == f"https://www.youtube.com/channel/{UC}/videos"
    assert source.newest_video_id is None


def test_backfill_makes_new_items(engine, youtube, channel):
    sources.add_source(engine, channel.id, f"https://www.youtube.com/channel/{UC}", backfill=2)
    assert youtube.called("list_channel")[0][2] == 2
    items = store.items_for(engine.db(), channel.id)
    # the listing has no dates, so they keep its order, newest first
    assert [(i.video_id, i.status, i.published_at, i.title) for i in items] == [
        ("bbbbbbbbbb2", "new", None, "Episode 2"),
        ("aaaaaaaaaa1", "new", None, "Episode 1"),
    ]


def test_backfill_from_a_video_link_lists_the_channel(engine, youtube, channel):
    sources.add_source(engine, channel.id, "https://youtu.be/arj7oStGLkU", backfill=1)
    assert len(youtube.called("fetch_info")) == 1
    assert youtube.called("list_channel") == [("list_channel", LISTING.url, 1)]
    assert [i.video_id for i in store.items_for(engine.db(), channel.id)] == ["bbbbbbbbbb2"]


@pytest.mark.parametrize("backfill", [-1, 6])
def test_backfill_over_5_is_refused(engine, youtube, channel, backfill):
    with pytest.raises(ClipForgeError) as err:
        sources.add_source(engine, channel.id, "@apodcast", backfill=backfill)
    assert err.value.message == sources.BACKFILL
    assert youtube.calls == []


def test_same_channel_twice_is_refused(engine, channel):
    sources.add_source(engine, channel.id, "@apodcast")
    with pytest.raises(ClipForgeError) as err:
        sources.add_source(engine, channel.id, "https://youtu.be/arj7oStGLkU")
    assert err.value.message == "This clipping channel already watches A Podcast."
    assert len(store.list_sources(engine.db(), channel.id)) == 1


def test_another_clipping_channel_may_watch_it_too(engine, channel):
    now = time.time()
    other = store.add_channel(
        engine.db(), ClipChannel(id=repo.new_id("cc"), name="Gaming", created_at=now, updated_at=now)
    )
    sources.add_source(engine, channel.id, "@apodcast")
    sources.add_source(engine, other.id, "@apodcast")
    assert len(store.list_sources(engine.db(), other.id)) == 1


def test_not_a_link_says_what_works(engine, youtube, channel):
    with pytest.raises(ClipForgeError) as err:
        sources.add_source(engine, channel.id, "https://vimeo.com/@someone")
    assert err.value.message == sources.NOT_A_CHANNEL
    assert youtube.calls == []


def test_lookup_failure_is_a_sentence(engine, youtube, channel):
    youtube.listing = YouTubeRateLimited("rate_limited", RATE_LIMITED)
    with pytest.raises(ClipForgeError) as err:
        sources.add_source(engine, channel.id, "@apodcast")
    assert err.value.message == RATE_LIMITED
    assert store.list_sources(engine.db(), channel.id) == []


def test_a_video_without_a_channel_is_refused(engine, youtube, channel):
    youtube.info.pop("channel_id", None)
    youtube.info["channel_url"] = None
    with pytest.raises(ClipForgeError) as err:
        sources.add_source(engine, channel.id, "https://youtu.be/arj7oStGLkU")
    assert err.value.message == sources.NO_CHANNEL


def test_titles_from_youtube_are_cleaned(engine, youtube, channel):
    youtube.listing = LISTING.model_copy(update={"title": "A\u202e Podcast\x00"})
    source = sources.add_source(engine, channel.id, "@apodcast")
    assert source.title == "A Podcast"


def test_unknown_clipping_channel(engine):
    with pytest.raises(LookupError):
        sources.add_source(engine, "ccnothere", "@apodcast")
