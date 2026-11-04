"""Sound suggestions for a clip (SPEC 13): your tracks and shortlisted sounds by how well their moods and
energy match the clip's, an Instagram account's trending list by its place in the list, and the one track
"auto" music picks."""

import time

import pytest

from clipforge import repo
from clipforge.models import LibraryItem
from clipforge.post import store as post_store
from clipforge.trends import recommend, store
from tests.fakes import fake_engine
from tests.unit.test_clip_jobs import add_clips, project
from tests.unit.test_trends_store import ig_account, shortlist, trending

DAY = 86400.0


@pytest.fixture
def engine():
    eng = fake_engine()
    yield eng
    eng.close()


def track(engine, name, moods, energy=None):
    item = LibraryItem(
        id=repo.new_id("l"), kind="music", name=name, path=f"/music/{name}.m4a", tags=moods, energy=energy
    )
    return repo.add_library_item(engine.db(), item)


def clip(engine, mood="hype", energy=4):
    (cid,) = add_clips(engine, project(engine))
    repo.update_clip(engine.db(), cid, packaging={"mood": mood, "energy": energy})
    return repo.get_clip(engine.db(), cid)


def listed(found):
    return [(s.kind, s.title, s.why, s.stale) for s in found]


def test_match_scores_mood_then_energy():
    assert recommend.match("hype", 4, ["hype"], 4) == 3.0
    assert recommend.match("hype", 4, ["funny"], 4) == 2.0  # funny is near hype
    assert recommend.match("hype", 4, ["dark"], None) == 0
    assert recommend.match("hype", 4, ["dark"], 2) == 0.5  # the energy alone
    assert recommend.match(None, None, ["hype"], 3) == 0
    assert set(recommend.NEAR) == {
        "hype",
        "funny",
        "inspiring",
        "serious",
        "emotional",
        "calm",
        "dark",
        "curious",
    }
    assert recommend.NEAR["curious"] == ("serious", "funny", "calm")


def test_suggestions_put_mood_matches_first(engine):
    now = time.time()
    track(engine, "Night Drive", ["hype"], 4)
    track(engine, "Laugh Track", ["funny"], 2)
    track(engine, "Low Hum", ["dark"], 1)  # no mood near hype, its energy 3 away: not suggested
    track(engine, "Steady", ["serious"], 5)  # its energy alone is close
    conn = engine.db()
    store.put_shortlist(conn, shortlist("1", title="Fresh hype", mood_tags=["hype"], created_at=now - DAY))
    store.put_shortlist(conn, shortlist("2", title="Old hype", mood_tags=["hype"], created_at=now - 15 * DAY))
    c = clip(engine)
    assert listed(recommend.suggestions(engine, c, limit=10)) == [
        ("music", "Night Drive", "Matches its hype mood", False),
        ("shortlist", "Fresh hype", "Matches its hype mood", False),
        ("shortlist", "Old hype", "Matches its hype mood", True),  # stale below fresh at the same score
        ("music", "Laugh Track", "Close to its hype mood", False),
        ("music", "Steady", "Its energy is close", False),
    ]
    found = recommend.suggestions(engine, c, platform="tiktok", limit=2)  # two of each kind
    assert [s.title for s in found] == ["Night Drive", "Fresh hype", "Old hype", "Laugh Track"]
    assert (found[0].artist, found[0].score) == (None, 3.0)
    # Instagram can't use a TikTok sound, so none is offered there.
    assert [s.kind for s in recommend.suggestions(engine, c, platform="instagram", limit=10)] == ["music"] * 3


def test_tracks_never_take_the_sounds_places(engine):
    # Five tracks that match outright must not push out a sound that matches less well: the Post dialog
    # and the editor show only the sounds, so the limit is per kind.
    for n in range(5):
        track(engine, f"Track {n}", ["hype"], 4)
    conn = engine.db()
    store.put_shortlist(conn, shortlist("1", title="Hype sound", mood_tags=["hype"], energy=3))
    a = ig_account(conn)
    store.replace_trending(conn, a.id, "music", [trending(a.id, "m0", 0)], time.time())
    c = clip(engine)
    found = recommend.suggestions(engine, c, platform="tiktok")
    assert [s.kind for s in found] == ["music"] * 5 + ["shortlist"]
    assert found[-1].title == "Hype sound"
    found = recommend.suggestions(engine, c, platform="instagram", account_id=a.id)
    assert [s.kind for s in found] == ["music"] * 5 + ["ig_trending"]
    assert len(recommend.suggestions(engine, c, platform="tiktok", limit=2)) == 3  # two tracks, one sound


def test_instagram_suggestions_follow_the_trending_rank(engine):
    conn = engine.db()
    a, other = ig_account(conn, "one"), ig_account(conn, "two")
    store.replace_trending(
        conn,
        a.id,
        "music",
        [trending(a.id, "m2", 2), trending(a.id, "m0", 0), trending(a.id, "m1", 1)],
        time.time(),
    )
    store.replace_trending(conn, other.id, "music", [trending(other.id, "x0", 0)], time.time())
    store.put_shortlist(conn, shortlist("9", title="TikTok only", mood_tags=["hype"]))
    track(engine, "Night Drive", ["hype"], 4)
    c = clip(engine)
    found = recommend.suggestions(engine, c, platform="instagram", account_id=a.id)
    assert listed(found) == [
        ("music", "Night Drive", "Matches its hype mood", False),
        ("ig_trending", "Song m0", "Trending on Instagram today", False),
        ("ig_trending", "Song m1", "Trending on Instagram today", False),
        ("ig_trending", "Song m2", "Trending on Instagram today", False),
    ]
    assert [s.score for s in found[1:]] == [1.0, 0.5, pytest.approx(1 / 3)]
    post_store.delete_account(conn, a.id)  # a deleted account's list goes with it
    assert [s.kind for s in recommend.suggestions(engine, c, platform="instagram", account_id=a.id)] == [
        "music"
    ]


def test_old_trending_lists_say_when(engine):
    conn = engine.db()
    a = ig_account(conn)
    store.replace_trending(conn, a.id, "music", [trending(a.id, "m0", 0)], time.time() - 3 * DAY)
    (found,) = recommend.suggestions(engine, clip(engine), platform="instagram", account_id=a.id)
    assert found.why == "Trending on Instagram when last checked"


def test_best_music_needs_a_match(engine):
    dark = track(engine, "Low Hum", ["dark"], 1)
    assert recommend.best_music([dark], "hype", 4) is None
    near = track(engine, "Laugh Track", ["funny"], 5)
    assert recommend.best_music([dark, near], "hype", 4) == near  # 1 + 0.75
    exact = track(engine, "Night Drive", ["hype"])
    assert recommend.best_music([dark, near, exact], "hype", 4) == exact  # 2
    tie = track(engine, "Anthem", ["hype"])
    assert recommend.best_music([dark, near, exact, tie], "hype", 4) == tie  # ties by title
    steady = track(engine, "Steady", ["calm"], 4)
    assert recommend.best_music([dark, steady], "hype", 4) == steady  # its energy alone is close
    assert recommend.best_music([], "hype", 4) is None
    assert recommend.best_music([exact], None, None) is None


def test_suggestions_skip_sounds_with_no_mood_or_energy(engine):
    conn = engine.db()
    store.put_shortlist(conn, shortlist("5", title="Untagged"))
    track(engine, "Plain", [])
    assert recommend.suggestions(engine, clip(engine)) == []
    unpackaged = clip(engine)
    repo.update_clip(conn, unpackaged.id, packaging=None)
    assert recommend.suggestions(engine, repo.get_clip(conn, unpackaged.id)) == []
