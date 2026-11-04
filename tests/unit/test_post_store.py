import sqlite3
import time

import pytest

from clipforge import repo
from clipforge.db import connect, migrate
from clipforge.models import ClipRow, ProjectOptions
from clipforge.post import store
from clipforge.post.models import Account, AccountSettings, Post, PostOptions


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    migrate(c)
    yield c
    c.close()


def account(platform="tiktok", handle="alpha", **kw):
    return Account(
        id=repo.new_id("a"),
        platform=platform,
        method=kw.pop("method", "browser"),
        handle=handle,
        created_at=time.time(),
        **kw,
    )


def make_clip(conn, start=10.0):
    project = repo.create_project(
        conn,
        source_kind="youtube",
        source_url="https://www.youtube.com/watch?v=arj7oStGLkU",
        video_id="arj7oStGLkU",
        options=ProjectOptions(),
        focus_s=None,
        prompt=None,
    )
    row = ClipRow(
        id=repo.new_id("c"),
        project_id=project.id,
        rank=1,
        start_s=start,
        end_s=start + 30,
        sentence_ids=["S1"],
        topic_tags=["money", "startups"],
    )
    repo.replace_ideas(conn, project.id, [row])
    return project, row


def post(account_id, batch_id, clip_id=None, at=None, status="scheduled", **kw):
    now = time.time()
    return Post(
        id=repo.new_id("t"),
        batch_id=batch_id,
        clip_id=clip_id,
        clip_title=kw.pop("clip_title", "A clip"),
        render_id=None,
        account_id=account_id,
        platform=kw.pop("platform", "tiktok"),
        method=kw.pop("method", "browser"),
        caption="Hello",
        hashtags=["one"],
        options=PostOptions(),
        scheduled_at=at if at is not None else now,
        status=status,
        video_key="arj7oStGLkU",
        start_s=10.0,
        end_s=40.0,
        created_at=now,
        updated_at=now,
        **kw,
    )


def test_migration_006_adds_posting_tables(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"accounts", "batches", "posts"} <= tables
    clip_columns = {r[1] for r in conn.execute("PRAGMA table_info(clips)")}
    assert "topic_tags_json" in clip_columns
    post_columns = {r[1] for r in conn.execute("PRAGMA table_info(posts)")}
    assert {"state_json", "video_key", "start_s", "end_s", "error_code", "details", "note"} <= post_columns


def test_account_round_trip_never_stores_tokens(conn):
    a = account(settings=AccountSettings(niche_tags=["money"], pair_id="a123"), token_ref="account:x")
    store.add_account(conn, a)
    back = store.get_account(conn, a.id)
    assert back == a
    columns = {r[1] for r in conn.execute("PRAGMA table_info(accounts)")}
    assert not {"token", "access_token", "password"} & columns


def test_handle_unique_among_live_accounts(conn):
    first = store.add_account(conn, account(handle="same"))
    with pytest.raises(sqlite3.IntegrityError):
        store.add_account(conn, account(handle="same"))
    store.add_account(conn, account(platform="instagram", handle="same", method="api"))
    store.delete_account(conn, first.id)
    store.add_account(conn, account(handle="same"))
    assert store.get_account(conn, first.id) is None
    assert store.get_account(conn, first.id, deleted=True).deleted_at is not None
    assert [a.handle for a in store.list_accounts(conn)] == ["same", "same"]


def test_update_account_fields(conn):
    a = store.add_account(conn, account())
    back = store.update_account(
        conn, a.id, status="connected", paused=True, settings=AccountSettings(cta="Follow")
    )
    assert (back.status, back.paused, back.settings.cta) == ("connected", True, "Follow")
    with pytest.raises(ValueError):
        store.update_account(conn, a.id, nonsense=1)


def test_batch_plan_id_is_unique(conn):
    bid = store.add_batch(conn, plan_id="p1", mode="spread", timing="smart", acks={})
    assert store.batch_for_plan(conn, "p1") == bid
    assert store.batch_for_plan(conn, "p2") is None
    with pytest.raises(sqlite3.IntegrityError):
        store.add_batch(conn, plan_id="p1", mode="spread", timing="now", acks={})


def test_posts_between_counts_active_and_posted_only(conn):
    a = store.add_account(conn, account())
    bid = store.add_batch(conn, plan_id="p1", mode="spread", timing="smart", acks={})
    t = 1_800_000_000.0
    store.add_posts(
        conn,
        [
            post(a.id, bid, at=t, status="scheduled"),
            post(a.id, bid, at=t + 60, status="posted"),
            post(a.id, bid, at=t + 120, status="uploading"),
            post(a.id, bid, at=t + 180, status="cancelled"),
            post(a.id, bid, at=t + 240, status="failed"),
            post(a.id, bid, at=t + 300, status="needs_you"),
            post(a.id, bid, at=t + 10_000, status="scheduled"),
        ],
    )
    found = store.posts_between(conn, a.id, t, t + 1000)
    assert [p.status for p in found] == ["scheduled", "posted", "uploading"]


def test_post_round_trip_and_update(conn):
    a = store.add_account(conn, account())
    bid = store.add_batch(conn, plan_id="p1", mode="spread", timing="smart", acks={})
    p = post(a.id, bid, state={"container": "17"})
    store.add_posts(conn, [p])
    assert store.get_post(conn, p.id) == p
    back = store.update_post(conn, p.id, status="uploading", state={"container": "18"}, attempts=1)
    assert (back.status, back.state, back.attempts) == ("uploading", {"container": "18"}, 1)
    assert back.updated_at >= p.updated_at
    assert [x.id for x in store.list_posts(conn, status=("uploading",))] == [p.id]
    assert store.list_posts(conn, account_id="nobody") == []


def test_deleting_a_clip_keeps_its_posts(conn):
    project, row = make_clip(conn)
    a = store.add_account(conn, account())
    bid = store.add_batch(conn, plan_id="p1", mode="spread", timing="smart", acks={})
    p = post(a.id, bid, clip_id=row.id, clip_title="Kept title")
    store.add_posts(conn, [p])
    assert [x.id for x in store.posts_for_clip(conn, row.id)] == [p.id]
    repo.delete_project(conn, project.id)
    back = store.get_post(conn, p.id)
    assert back.clip_id is None and back.clip_title == "Kept title"


def test_clip_rows_keep_topic_tags(conn):
    _, row = make_clip(conn)
    assert repo.get_clip(conn, row.id).topic_tags == ["money", "startups"]
