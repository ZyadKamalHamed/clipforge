import time

import pytest

from clipforge import db, repo
from clipforge.db import connect, migrate
from clipforge.post import store as post_store
from clipforge.post.models import Account
from clipforge.trends import store
from clipforge.trends.models import STALE_DAYS, HashtagSet, Sound, TrendRead

DAY = 86400.0


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    migrate(c)
    yield c
    c.close()


def ig_account(conn, handle="clips"):
    a = Account(
        id=repo.new_id("a"),
        platform="instagram",
        method="api",
        handle=handle,
        ig_mode="facebook",
        created_at=time.time(),
    )
    return post_store.add_account(conn, a)


def shortlist(external_id="7234567890123456789", **kw):
    return Sound(
        id=kw.pop("id", repo.new_id("snd")),
        platform="tiktok",
        source="shortlist",
        external_id=external_id,
        title=kw.pop("title", "Original Sound"),
        url=f"https://www.tiktok.com/music/Original-Sound-{external_id}",
        created_at=kw.pop("created_at", time.time()),
        **kw,
    )


def trending(account_id, external_id, rank, audio_type="music"):
    return Sound(
        id=repo.new_id("snd"),
        platform="instagram",
        source="ig_trending",
        external_id=external_id,
        title=f"Song {external_id}",
        audio_type=audio_type,
        rank=rank,
        account_id=account_id,
        created_at=time.time(),
    )


def test_migration_011_adds_the_sound_tables(tmp_path, monkeypatch):
    c = connect(tmp_path / "old.db")
    everything = db._migrations
    monkeypatch.setattr(db, "_migrations", lambda: [m for m in everything() if m[0] <= 10])
    assert migrate(c) == 10
    c.execute(
        "INSERT INTO library_items (id, kind, name, path, credit, license, created_at) "
        "VALUES ('l1', 'gameplay', 'Run', '/x.mp4', 'Me', 'Own footage', 1)"
    )
    c.commit()
    monkeypatch.setattr(db, "_migrations", everything)
    assert migrate(c) == 13
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"sounds", "hashtag_sets", "trend_reads"} <= tables
    assert {"credit_required", "energy"} <= {r["name"] for r in c.execute("PRAGMA table_info(library_items)")}
    row = c.execute("SELECT name, credit, license, credit_required, energy FROM library_items").fetchone()
    assert tuple(row) == ("Run", "Me", "Own footage", 0, None)
    c.close()


def test_shortlist_keeps_one_row_per_sound(conn):
    first = store.put_shortlist(conn, shortlist(title="Old name", created_at=1000.0))
    again = store.put_shortlist(conn, shortlist(title="New name", region="AU", mood_tags=["hype"]))
    rows = store.sounds(conn, source="shortlist")
    assert [(s.id, s.title, s.region, s.mood_tags, s.created_at) for s in rows] == [
        (first.id, "New name", "AU", ["hype"], 1000.0)
    ]
    assert again == rows[0] == store.get_sound(conn, first.id)
    assert store.delete_sound(conn, first.id) and not store.delete_sound(conn, first.id)
    assert store.get_sound(conn, first.id) is None


def test_replace_trending_swaps_one_list(conn):
    a, b = ig_account(conn, "one"), ig_account(conn, "two")
    store.replace_trending(conn, a.id, "music", [trending(a.id, "m1", 0), trending(a.id, "m2", 1)], now=1.0)
    store.replace_trending(conn, a.id, "original_sound", [trending(a.id, "o1", 0, "original_sound")], now=1.0)
    store.replace_trending(conn, b.id, "music", [trending(b.id, "m1", 0)], now=1.0)
    store.replace_trending(conn, a.id, "music", [trending(a.id, "m3", 0)], now=2.0)
    mine = store.sounds(conn, source="ig_trending", account_id=a.id)
    assert [(s.audio_type, s.external_id, s.rank, s.fetched_at) for s in mine] == [
        ("music", "m3", 0, 2.0),
        ("original_sound", "o1", 0, 1.0),
    ]
    assert [s.external_id for s in store.sounds(conn, account_id=b.id)] == ["m1"]


def test_trending_rows_go_with_their_account(conn):
    a, b = ig_account(conn, "one"), ig_account(conn, "two")
    store.replace_trending(conn, a.id, "music", [trending(a.id, "m1", 0)], now=1.0)
    store.replace_trending(conn, b.id, "music", [trending(b.id, "m2", 0)], now=1.0)
    post_store.delete_account(conn, a.id)  # deleting an account only marks it
    assert [s.external_id for s in store.sounds(conn, source="ig_trending")] == ["m2"]
    conn.execute("DELETE FROM accounts WHERE id = ?", (b.id,))
    assert conn.execute("SELECT COUNT(*) FROM sounds WHERE account_id = ?", (b.id,)).fetchone()[0] == 0


def test_stale_after_14_days():
    now = 100 * DAY
    assert STALE_DAYS == 14
    assert not shortlist(created_at=now - 14 * DAY).stale(now)
    assert shortlist(created_at=now - 14 * DAY - 1).stale(now)


def test_hashtag_sets_and_reads_are_kept(conn):
    kept = store.put_set(
        conn,
        HashtagSet(
            id="hs1", niche="Gaming", name="Clips", tags=["gaming", "fyp"], created_at=1.0, updated_at=1.0
        ),
    )
    assert store.get_set(conn, "hs1") == kept
    store.put_set(conn, kept.model_copy(update={"tags": ["gaming"], "updated_at": 2.0}))
    assert [(s.tags, s.created_at, s.updated_at) for s in store.sets(conn)] == [(["gaming"], 1.0, 2.0)]
    assert store.delete_set(conn, "hs1") and store.sets(conn) == []
    store.save_read(conn, TrendRead(id="feed:socialpilot", read_at=5.0, newest_at=4.0, result="ok"))
    store.save_read(
        conn, TrendRead(id="feed:socialpilot", read_at=6.0, newest_at=4.0, seen_at=6.0, result="ok")
    )
    read = store.read_of(conn, "feed:socialpilot")
    assert (read.read_at, read.newest_at, read.seen_at, read.error) == (6.0, 4.0, 6.0, None)
    assert store.read_of(conn, "ig:nope") is None
