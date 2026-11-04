"""Instagram's trending sounds (SPEC 13): read once a day for each account linked through Facebook, both
lists, the text cleaned and the links checked as untrusted data, yesterday's list kept when a read fails,
and the rows going with their account."""

import httpx
import pytest
from keyring.errors import KeyringError

from clipforge.post import keychain
from clipforge.post import store as post_store
from clipforge.trends import instagram, recommend, store
from tests.fakes import Clock, fake_engine
from tests.fakes_graph import PAGE_TOKEN, FakeGraph, audio_row, error
from tests.posting import add_account, at
from tests.unit.test_instagram_api import ig_account

NOW = at(7, 10)
RLO, PDF = "\u202e", "\u202c"


@pytest.fixture
def clock():
    return Clock(NOW)


@pytest.fixture
def graph(clock):
    return FakeGraph(now=clock)


@pytest.fixture
def engine(clock, graph):
    eng = fake_engine(now=clock)
    eng.graph_transport = graph.transport()
    yield eng
    eng.close()


def listed(engine, account):
    return [s.external_id for s in store.sounds(engine.db(), source="ig_trending", account_id=account.id)]


def test_trending_rows_are_cleaned_and_links_checked(engine, graph):
    a = ig_account(engine)
    graph.audio["music"] = [
        audio_row("a1", title=f"Night {RLO}Drive{PDF}", display_artist="\u0641\u064a\u0631\u0648\u0632"),
        audio_row(
            "a2",
            title="x" * 500,
            display_artist=None,
            ig_username="dj_one",
            on_platform_audio_preview_link="http://www.instagram.com/reels/audio/a2/",
        ),
        audio_row(
            "a3",
            title="  ",
            display_artist=None,
            ig_username=None,
            on_platform_audio_preview_link="https://instagram.com.evil.example/reels/audio/a3/",
        ),
        audio_row(None),
        audio_row(
            "a4",
            title="Fire \U0001f525",
            on_platform_audio_preview_link="https://instagram.com/reels/audio/a4/",
        ),
        audio_row("a5", on_platform_audio_preview_link="https://www.instagram.com:8443/reels/audio/a5/"),
    ]
    read = instagram.read_account(engine, a.id)
    assert (read.id, read.result, read.error, read.read_at) == (f"ig:{a.id}", "ok", None, NOW.timestamp())
    assert store.read_of(engine.db(), f"ig:{a.id}") == read
    rows = store.sounds(engine.db(), source="ig_trending", account_id=a.id)
    assert [(s.external_id, s.title, s.artist, s.url) for s in rows] == [
        ("a1", "Night Drive", "\u0641\u064a\u0631\u0648\u0632", "https://www.instagram.com/reels/audio/a1/"),
        ("a2", "x" * instagram.TITLE_CHARS, "dj_one", None),
        ("a3", instagram.UNTITLED, None, None),
        ("a4", "Fire \U0001f525", "The Artist", "https://instagram.com/reels/audio/a4/"),
        ("a5", "Song a5", "The Artist", None),
    ]
    assert instagram.TITLE_CHARS == 200
    assert [(s.rank, s.audio_type, s.duration_s, s.platform) for s in rows] == [
        (n, "music", 30.5, "instagram") for n in range(5)
    ]
    assert all(s.fetched_at == NOW.timestamp() and s.account_id == a.id for s in rows)
    assert "cdn.example" not in repr(rows) and PAGE_TOKEN not in repr(read)
    # an empty list is kept as empty, not an error
    graph.audio["music"] = []
    assert instagram.read_account(engine, a.id).result == "ok"
    assert listed(engine, a) == []


def test_read_once_a_day_per_account(engine, graph, clock):
    a, b = ig_account(engine), ig_account(engine)
    ig_account(engine, mode="instagram")  # linked through Instagram Login: Instagram lists no sounds for it
    add_account(engine, "instagram", method="browser")  # posted through Chrome
    add_account(engine, "tiktok")
    tokenless = add_account(engine, "instagram")
    post_store.update_account(engine.db(), tokenless.id, ig_mode="facebook", ig_user_id="ig9")
    graph.audio["music"] = [audio_row("a1")]
    clock.set(at(5, 59))
    assert instagram.tick(engine) == 0  # before the morning's read time
    clock.set(at(6, 0))
    assert instagram.tick(engine) == 2
    clock.set(at(23, 59))
    assert instagram.tick(engine) == 0
    assert [r.url.params["audio_type"] for r in graph.of("audio")] == ["music", "original_sound"] * 2
    assert listed(engine, a) == listed(engine, b) == ["a1"]
    clock.set(at(0, 30, day=4))
    assert instagram.tick(engine) == 0  # a new day, but not yet its read time
    clock.set(at(6, 5, day=4))
    assert instagram.tick(engine) == 2
    assert len(graph.of("audio")) == 8
    assert not instagram.due(engine, post_store.get_account(engine.db(), a.id), clock())


def test_failed_read_keeps_yesterdays_list(engine, graph, clock):
    a = ig_account(engine)
    graph.audio["music"] = [audio_row("a1"), audio_row("a2")]
    graph.audio["original_sound"] = [audio_row("o1", audio_type="original_sound")]
    assert instagram.tick(engine) == 1
    clock.set(at(8, 0, day=4))  # past a day since the lists came, so they are no longer today's
    graph.audio["music"] = [audio_row("b1")]
    graph.script["audio"] = [None, error(1, None, f"Try later {PAGE_TOKEN}-1", status=500)]  # music, then not
    assert instagram.tick(engine) == 1
    assert listed(engine, a) == ["a1", "a2", "o1"]  # neither list changes when one of them fails
    read = store.read_of(engine.db(), f"ig:{a.id}")
    assert (read.result, read.error, read.read_at) == ("error", instagram.READ_FAILED, clock().timestamp())
    assert (
        instagram.READ_FAILED
        == "Instagram's trending sounds couldn't be read. ClipForge tries again tomorrow."
    )
    found = recommend.suggestions(engine, _clip(engine), platform="instagram", account_id=a.id, limit=10)
    assert [s.why for s in found] == ["Trending on Instagram when last checked"] * 3
    clock.advance(hours=4)
    assert instagram.tick(engine) == 0  # it waits for tomorrow, or Check now
    clock.set(at(7, 10, day=5))
    assert instagram.tick(engine) == 1
    assert listed(engine, a) == ["b1", "o1"]
    assert store.read_of(engine.db(), f"ig:{a.id}").result == "ok"


def test_one_bad_answer_stops_only_its_own_read(engine, graph, clock):
    a, b = ig_account(engine, ig_user_id="igA"), ig_account(engine, ig_user_id="igB")
    graph.audio["music"] = [audio_row("b1")]

    def not_a_list(request):  # a's lists come back as a number
        return httpx.Response(200, json={"data": 5}) if request.url.params["user_id"] == "igA" else None

    graph.script["audio"] = [not_a_list] * 4
    assert instagram.tick(engine) == 2
    assert (listed(engine, a), listed(engine, b)) == ([], ["b1"])
    read = store.read_of(engine.db(), f"ig:{a.id}")
    assert (read.result, read.error) == ("error", instagram.READ_FAILED)
    asked = len(graph.of("audio"))
    clock.advance(minutes=5)
    assert instagram.tick(engine) == 0  # it waits for tomorrow, not the next tick
    assert len(graph.of("audio")) == asked


def test_a_keychain_error_stops_only_its_own_read(engine, graph, clock, monkeypatch):
    a, b = ig_account(engine, ig_user_id="igA"), ig_account(engine, ig_user_id="igB")
    graph.audio["music"] = [audio_row("b1")]
    read_token = keychain.get_token

    def get_token(ref):
        if ref == a.token_ref:
            raise KeyringError("the Keychain is locked")
        return read_token(ref)

    monkeypatch.setattr(keychain, "get_token", get_token)
    assert instagram.tick(engine) == 1
    assert listed(engine, b) == ["b1"]
    read = store.read_of(engine.db(), f"ig:{a.id}")
    assert (read.result, read.error) == ("error", instagram.READ_FAILED)
    clock.advance(minutes=5)
    assert instagram.tick(engine) == 0  # it waits for tomorrow, not the next tick


def _clip(engine):
    from clipforge import repo
    from tests.unit.test_clip_jobs import add_clips, project

    (cid,) = add_clips(engine, project(engine))
    return repo.get_clip(engine.db(), cid)


def test_trending_rows_follow_their_account(engine, graph, clock):
    a, b = ig_account(engine), ig_account(engine)
    graph.audio["music"] = [audio_row("a1")]
    assert instagram.tick(engine) == 2
    conn = engine.db()
    (row,) = store.sounds(conn, source="ig_trending", account_id=a.id)
    # relinked through Instagram Login: the Post dialog offers none of its sounds
    post_store.update_account(conn, a.id, ig_mode="instagram", fb_page_id=None)
    assert listed(engine, a) == [] and store.get_sound(conn, row.id) is None
    assert not instagram.can_read(post_store.get_account(conn, a.id))
    assert recommend.suggestions(engine, _clip(engine), platform="instagram", account_id=a.id) == []
    post_store.delete_account(conn, b.id)
    assert listed(engine, b) == [] and store.sounds(conn, source="ig_trending") == []
    clock.set(at(7, 10, day=4))
    assert instagram.tick(engine) == 0
    assert len(graph.of("audio")) == 4
