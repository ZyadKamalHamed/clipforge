import json
import time

import pytest

from clipforge import repo
from clipforge.db import connect, migrate
from clipforge.post import lifecycle, store
from clipforge.post.errors import LifecycleError
from clipforge.post.lifecycle import StepLog
from clipforge.post.models import FINAL, POST_STATUSES, Account, Post, PostOptions

ALLOWED = {
    "scheduled": {"rendering", "uploading", "needs_you", "failed", "cancelled", "exported", "scheduled"},
    "rendering": {"scheduled", "uploading", "needs_you", "failed", "cancelled"},
    "uploading": {"processing", "posted", "scheduled", "scheduled_on_tiktok", "needs_you", "failed"},
    "processing": {"posted", "needs_you", "failed"},
    # TikTok Studio holds it to put up at its slot; ClipForge only reads its link, or stops following it
    "scheduled_on_tiktok": {"posted", "needs_you", "cancelled"},
    "needs_you": {"scheduled", "posted", "cancelled", "exported", "failed"},
    "failed": {"scheduled", "cancelled", "exported"},
    "posted": set(),
    "cancelled": set(),
    "exported": {"posted"},  # you posted it yourself and marked it so
    "draft": {"scheduled", "discarded"},  # a clipping channel's draft waits for approval (SPEC 10.9)
    "discarded": {"draft"},  # Undo
}


def test_lifecycle_allows_only_listed_moves():
    assert set(POST_STATUSES) == set(ALLOWED)
    for old in POST_STATUSES:
        for new in POST_STATUSES:
            assert lifecycle.can_move(old, new) == (new in ALLOWED[old]), (old, new)


def test_final_statuses_never_move():
    """The engine never moves a final post; an exported one only becomes posted when you mark it so."""
    assert set(FINAL) == {"posted", "cancelled", "exported"}
    for old in FINAL:
        moves = {new for new in POST_STATUSES if lifecycle.can_move(old, new)}
        assert moves == ({"posted"} if old == "exported" else set()), old


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    migrate(c)
    yield c
    c.close()


def test_move_writes_fields_and_refuses_bad_moves(conn):
    now = time.time()
    a = store.add_account(conn, Account(id="a1", platform="tiktok", method="browser", created_at=now))
    bid = store.add_batch(conn, plan_id="p", mode="spread", timing="now", acks={})
    p = Post(
        id=repo.new_id("t"),
        batch_id=bid,
        clip_id=None,
        clip_title="x",
        render_id=None,
        account_id=a.id,
        platform="tiktok",
        method="browser",
        caption="c",
        hashtags=[],
        options=PostOptions(),
        scheduled_at=now,
        status="scheduled",
        video_key="v",
        start_s=0,
        end_s=10,
        created_at=now,
        updated_at=now,
    )
    store.add_posts(conn, [p])
    moved = lifecycle.move(conn, p.id, "uploading", attempts=1)
    assert (moved.status, moved.attempts) == ("uploading", 1)
    with pytest.raises(LifecycleError):
        lifecycle.move(conn, p.id, "cancelled")
    assert store.get_post(conn, p.id).status == "uploading"


def test_step_log_redacts_tokens(tmp_path):
    log = StepLog(tmp_path / "logs" / "steps.jsonl")
    log.write("container", url="https://graph.facebook.com/x?access_token=EAAsecretvalue123", n=3)
    log.write("upload", headers={"Authorization": "OAuth EAAanothersecret456"}, note="ok")
    text = (tmp_path / "logs" / "steps.jsonl").read_text()
    assert "EAAsecretvalue123" not in text and "EAAanothersecret456" not in text
    lines = [json.loads(line) for line in text.splitlines()]
    assert [line["step"] for line in lines] == ["container", "upload"]
    assert lines[0]["n"] == 3 and lines[1]["note"] == "ok"
    assert all(isinstance(line["at"], float) for line in lines)


def test_drafts_take_no_slot_and_have_no_job():
    from clipforge.post.models import ACTIVE
    from clipforge.post.plan import SHOWN, TAKEN

    for status in ("draft", "discarded"):
        assert status not in ACTIVE and status not in FINAL
        assert status not in TAKEN and status not in SHOWN
