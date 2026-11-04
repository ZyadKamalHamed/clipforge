import sqlite3
import time

import pytest

from clipforge import repo
from clipforge.campaigns import store
from clipforge.campaigns.models import (
    Campaign,
    Footage,
    FootageChannel,
    Metric,
    Required,
    Submission,
    web_url,
)
from clipforge.db import connect, migrate
from clipforge.models import ProjectOptions
from clipforge.watch import store as watch_store
from clipforge.watch.models import ClipChannel

UC = "UCabcdefghijklmnopqrstuv"


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    migrate(c)
    yield c
    c.close()


def campaign(**kw) -> Campaign:
    now = time.time()
    return Campaign(
        id=repo.new_id("cp"),
        name=kw.pop("name", "Podcast clips"),
        marketplace=kw.pop("marketplace", "Whop"),
        created_at=now,
        updated_at=now,
        **kw,
    )


def a_post(conn) -> str:
    """A bare post row for the submissions and metrics to point at."""
    from tests.posting import bare_post

    return bare_post(conn)


def test_campaign_round_trips(conn):
    made = campaign(
        campaign_url="https://whop.com/podcast-clips",
        submit_url="https://whop.com/podcast-clips/submit",
        currency="AUD",
        rate_per_1000=1.5,
        min_views=10_000,
        max_payout=300.0,
        platforms=["tiktok"],
        footage=Footage(
            channels=[
                FootageChannel(
                    yt_channel_id=UC, title="A Podcast", url=f"https://www.youtube.com/channel/{UC}"
                )
            ],
            videos=["arj7oStGLkU"],
            files=True,
        ),
        required=Required(hashtags=["podcast"], mentions=["@apodcast"], text=["Link in bio"]),
        banned=["gameplay", "emoji"],
        hand_rules=["No reaction content"],
        min_s=20.0,
        max_s=60.0,
        ends_on="2026-11-30",
        budget_left=1200.0,
        budget_entered_on="2026-10-28",
        notes="Paid weekly.",
        rules_text="Clip the podcast.",
    )
    store.add_campaign(conn, made)
    back = store.get_campaign(conn, made.id)
    assert back == made
    row = conn.execute("SELECT banned_json, required_json FROM campaigns").fetchone()
    assert '"elements"' in row["banned_json"] and '"text"' in row["banned_json"]
    assert store.list_campaigns(conn) == [made]
    changed = store.update_campaign(conn, made.id, budget_left=0.0, banned=["sfx"])
    assert (changed.budget_left, changed.banned, changed.hand_rules) == (
        0.0,
        ["sfx"],
        ["No reaction content"],
    )


def test_one_submission_per_post(conn):
    made = store.add_campaign(conn, campaign())
    post_id = a_post(conn)
    now = time.time()
    first = Submission(
        id=repo.new_id("sb"),
        post_id=post_id,
        campaign_id=made.id,
        status="to_submit",
        created_at=now,
        updated_at=now,
    )
    store.add_submission(conn, first)
    assert store.submission_of_post(conn, post_id) == first
    with pytest.raises(sqlite3.IntegrityError):
        store.add_submission(conn, first.model_copy(update={"id": repo.new_id("sb")}))
    moved = store.update_submission(conn, first.id, status="submitted", submitted_at=now)
    assert moved.status == "submitted"
    assert store.list_submissions(conn, campaign_id=made.id, status=["submitted"]) == [moved]
    assert store.list_submissions(conn, status=["paid"]) == []


def test_deleting_a_campaign_unlinks_projects_and_channels(conn):
    made = store.add_campaign(conn, campaign())
    project = repo.create_project(
        conn,
        source_kind="youtube",
        source_url=None,
        video_id=None,
        options=ProjectOptions(),
        focus_s=None,
        prompt=None,
    )
    repo.update_project(conn, project.id, campaign_id=made.id)
    now = time.time()
    channel = watch_store.add_channel(
        conn,
        ClipChannel(
            id=repo.new_id("cc"), name="Podcasts", campaign_id=made.id, created_at=now, updated_at=now
        ),
    )
    assert repo.get_project(conn, project.id).campaign_id == made.id
    store.delete_campaign(conn, made.id)
    assert store.get_campaign(conn, made.id) is None
    assert repo.get_project(conn, project.id).campaign_id is None
    assert watch_store.get_channel(conn, channel.id).campaign_id is None


def test_metrics_newest_last(conn):
    post_id = a_post(conn)
    store.add_metric(conn, Metric(post_id=post_id, captured_at=200.0, source="hand", views=800))
    store.add_metric(conn, Metric(post_id=post_id, captured_at=100.0, source="insights", views=1000, likes=5))
    assert [m.views for m in store.metrics_of(conn, post_id)] == [1000, 800]
    assert store.latest_metric(conn, post_id).views == 800
    assert store.latest_metric(conn, post_id, source="insights").likes == 5
    assert store.latest_metric(conn, "nope") is None


@pytest.mark.parametrize(
    "bad",
    [
        "javascript:alert(1)",
        "file:///etc/passwd",
        "https://u:p@whop.com/x",
        "-a https://whop.com",
        "https://whop.com/x\nmore",
        "https://",
        "https://whop.com/" + "x" * 2000,
    ],
)
def test_web_url_takes_only_web_links(bad):
    assert web_url("https://whop.com/x") == "https://whop.com/x"
    assert web_url("  http://vyro.com/c/1 ") == "http://vyro.com/c/1"
    with pytest.raises(ValueError):
        web_url(bad)


def test_campaign_fields_are_checked():
    with pytest.raises(ValueError):
        campaign(submit_url="javascript:alert(1)")
    with pytest.raises(ValueError):
        campaign(min_s=60.0, max_s=30.0)
    with pytest.raises(ValueError):
        campaign(platforms=[])
    with pytest.raises(ValueError):
        campaign(currency="usd dollars")
    with pytest.raises(ValueError):
        campaign(banned=["fireworks"])
    with pytest.raises(ValueError):
        Required(mentions=["@" + "x" * 31])
    assert campaign(currency="aud").currency == "AUD"
    assert campaign().platforms == ["tiktok", "instagram"]
