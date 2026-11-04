import sqlite3
import time

import pytest

from clipforge import repo
from clipforge.db import connect, migrate
from clipforge.models import ProjectOptions
from clipforge.watch import store
from clipforge.watch.models import ChannelOptions, ClipChannel, WatchCheck, WatchedSource, WatchItem


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    migrate(c)
    yield c
    c.close()


def channel(name="Podcasts", **kw):
    now = time.time()
    return ClipChannel(id=repo.new_id("cc"), name=name, created_at=now, updated_at=now, **kw)


def source(channel_id, yt="UCabcdefghijklmnopqrstuv", **kw):
    return WatchedSource(
        id=repo.new_id("ws"),
        clip_channel_id=channel_id,
        yt_channel_id=yt,
        title="A Podcast",
        url=f"https://www.youtube.com/channel/{yt}/videos",
        start_published_at=time.time(),
        created_at=time.time(),
        **kw,
    )


def item(src, video_id="arj7oStGLkU", **kw):
    now = time.time()
    return WatchItem(
        id=repo.new_id("wi"),
        source_id=src.id,
        clip_channel_id=src.clip_channel_id,
        video_id=video_id,
        title="Episode 1",
        status=kw.pop("status", "new"),
        first_seen_at=now,
        updated_at=now,
        **kw,
    )


def check(channel_id, started_at=None, **kw):
    return WatchCheck(
        id=repo.new_id("wc"),
        clip_channel_id=channel_id,
        check_on="2026-10-28",
        manual=kw.pop("manual", False),
        started_at=started_at or time.time(),
        **kw,
    )


def test_channel_round_trips(conn):
    made = channel(
        account_ids=["a1", "a2"],
        options=ChannelOptions(project=ProjectOptions(clip_count=4, layout="stacked"), hashtags=["podcast"]),
        rights_basis="permission",
        check_time="07:30",
        daily_video_limit=5,
        clips_per_day=8,
        on_limit="economy",
        paused=True,
        last_check_on="2026-10-27",
    )
    store.add_channel(conn, made)
    back = store.get_channel(conn, made.id)
    assert back == made
    assert back.options.project.clip_count == 4
    store.update_channel(conn, made.id, name="Gaming", paused=False)
    again = store.get_channel(conn, made.id)
    assert (again.name, again.paused) == ("Gaming", False)
    assert again.updated_at >= made.updated_at
    assert [c.id for c in store.list_channels(conn)] == [made.id]


def test_deleting_a_channel_removes_its_sources_items_and_checks(conn):
    c = store.add_channel(conn, channel())
    s = store.add_source(conn, source(c.id))
    store.add_item(conn, item(s))
    store.add_check(conn, check(c.id))
    project = repo.create_project(
        conn,
        source_kind="youtube",
        source_url="https://www.youtube.com/watch?v=arj7oStGLkU",
        video_id="arj7oStGLkU",
        options=ProjectOptions(),
        focus_s=None,
        prompt=None,
    )
    repo.update_project(conn, project.id, clip_channel_id=c.id)
    assert repo.get_project(conn, project.id).clip_channel_id == c.id
    store.delete_channel(conn, c.id)
    assert store.get_channel(conn, c.id) is None
    assert store.list_sources(conn, c.id) == []
    assert store.items_for(conn, c.id) == []
    assert store.checks_for(conn, c.id) == []
    assert repo.get_project(conn, project.id).clip_channel_id is None


def test_one_item_per_video_per_channel(conn):
    first = store.add_channel(conn, channel())
    second = store.add_channel(conn, channel("Gaming"))
    a = store.add_source(conn, source(first.id))
    b = store.add_source(conn, source(second.id))
    store.add_item(conn, item(a))
    with pytest.raises(sqlite3.IntegrityError):
        store.add_item(conn, item(a))
    store.add_item(conn, item(b))
    assert store.item_for_video(conn, first.id, "arj7oStGLkU").source_id == a.id
    assert store.item_for_video(conn, second.id, "arj7oStGLkU").source_id == b.id
    assert store.item_for_video(conn, first.id, "dQw4w9WgXcQ") is None


def test_items_by_status_and_update(conn):
    c = store.add_channel(conn, channel())
    s = store.add_source(conn, source(c.id))
    one = store.add_item(conn, item(s, "arj7oStGLkU"))
    store.add_item(conn, item(s, "dQw4w9WgXcQ", status="skipped", reason="short"))
    assert [i.id for i in store.items_for(conn, c.id, status=("new",))] == [one.id]
    store.update_item(conn, one.id, status="clipping", project_id=None, check_id="wc1")
    assert store.get_item(conn, one.id).status == "clipping"
    assert store.get_item(conn, one.id).check_id == "wc1"


def test_checks_newest_first_capped_at_30(conn):
    c = store.add_channel(conn, channel())
    base = time.time()
    made = [store.add_check(conn, check(c.id, started_at=base + i)) for i in range(35)]
    listed = store.checks_for(conn, c.id)
    assert len(listed) == 30
    assert listed[0].id == made[-1].id
    assert store.checks_for(conn, c.id, limit=3)[2].id == made[-3].id


def test_open_checks_are_the_undrafted(conn):
    c = store.add_channel(conn, channel())
    open_one = store.add_check(conn, check(c.id))
    done = store.add_check(conn, check(c.id))
    store.update_check(conn, done.id, drafted_at=time.time(), drafts=0, finished_at=time.time())
    assert [k.id for k in store.open_checks(conn)] == [open_one.id]
    assert store.get_check(conn, done.id).drafts == 0


def test_source_update_and_delete(conn):
    c = store.add_channel(conn, channel())
    s = store.add_source(conn, source(c.id))
    store.update_source(conn, s.id, newest_video_id="arj7oStGLkU", newest_title="New", error=None)
    assert store.get_source(conn, s.id).newest_video_id == "arj7oStGLkU"
    store.add_item(conn, item(s))
    store.delete_source(conn, s.id)
    assert store.get_source(conn, s.id) is None
    assert store.items_for(conn, c.id) == []


def test_channel_field_bounds():
    with pytest.raises(ValueError):
        channel(name="")
    with pytest.raises(ValueError):
        channel(check_time="25:00")
    with pytest.raises(ValueError):
        channel(daily_video_limit=11)
    with pytest.raises(ValueError):
        channel(clips_per_day=0)
    assert channel(name="  Podcasts  ").name == "Podcasts"
