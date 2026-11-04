from datetime import date, datetime
from pathlib import Path

import pytest

from clipforge import repo
from clipforge.errors import ClipForgeError
from clipforge.jobs import pipeline
from clipforge.post import lifecycle, queue, store
from clipforge.post.errors import Fatal, LifecycleError, NeedsUser, PlanChanged, Retryable
from clipforge.post.models import PublishResult
from clipforge.post.plan import at_text
from tests.fakes import Clock, FakePublisher, fake_engine
from tests.posting import (
    SYD,
    add_account,
    add_campaign,
    add_clip,
    add_render,
    at,
    claim,
    confirm,
    custom,
    jobs,
    linked_clip,
    post,
    request,
    run,
    sydney,
)
from tests.server import api_client
from tests.unit.test_audio import edl_for

NOW = at(6)  # Tue 3 Nov 2026, 6:00 am, before the first window
STOPPED = (
    "ClipForge stopped while this post was uploading. "
    "Check TikTok Studio, then press Retry or Mark as posted."
)


@pytest.fixture
def clock():
    return Clock(NOW)


@pytest.fixture
def tiktok():
    return FakePublisher()


@pytest.fixture
def instagram():
    return FakePublisher(platform="instagram", method="api")


def open_engine(clock, tiktok, instagram):
    return fake_engine(now=clock, publishers={"tiktok:browser": tiktok, "instagram:api": instagram})


@pytest.fixture
def engine(clock, tiktok, instagram):
    eng = open_engine(clock, tiktok, instagram)
    yield eng
    eng.close()


def drain(sub, *topics):
    events = []
    while (event := sub.get(timeout=0)) is not None:
        if event.topic in topics:
            events.append((event.topic, event.data))
    return events


# Confirm and post


def test_confirm_writes_one_batch_and_one_job_per_post(engine):
    clips = [add_clip(engine, 1), add_clip(engine, 2)]
    accounts = [add_account(engine), add_account(engine, "instagram")]
    batch, posts = confirm(engine, clips, accounts)
    assert len(posts) == 4
    assert {p.batch_id for p in posts} == {batch}
    assert engine.db().execute("SELECT COUNT(*) FROM batches").fetchone()[0] == 1
    by_clip = {c.id: c for c in clips}
    for p in posts:
        c = by_clip[p.clip_id]
        project = repo.get_project(engine.db(), c.project_id)
        assert p.status == "scheduled"
        assert p.clip_title == "The deadline"
        assert p.render_id == c.current_render_id
        assert (p.video_key, p.start_s, p.end_s) == (project.video_id, c.start_s, c.end_s)
        assert p.method == ("api" if p.platform == "instagram" else "browser")
        (job,) = jobs(engine, p.id)
        assert job.kind == ("post.api" if p.platform == "instagram" else "post.browser")
        assert job.not_before == p.scheduled_at


def test_confirm_queues_a_render_for_a_clip_without_one(engine):
    clip = add_clip(engine, rendered=False)
    _, (p,) = confirm(engine, [clip], [add_account(engine)])
    assert p.render_id is None
    assert [j.kind for j in jobs(engine, clip.id)] == ["clip.source"]
    assert len(jobs(engine, p.id)) == 1


def test_confirm_returns_the_same_batch_for_a_used_plan_id(engine):
    clip, account = add_clip(engine), add_account(engine)
    r = request([clip], [account])
    plan = engine.posting.plan(r)
    first = engine.posting.confirm(r, plan.plan_id)
    again = engine.posting.confirm(r, plan.plan_id)
    assert again[0] == first[0]
    assert [p.id for p in again[1]] == [p.id for p in first[1]]
    assert len(store.list_posts(engine.db())) == 1
    assert len(jobs(engine, first[1][0].id)) == 1


def test_confirm_refuses_a_changed_plan(engine):
    a, b = add_clip(engine, 1), add_clip(engine, 2)
    account = add_account(engine)
    shown = engine.posting.plan(request([a], [account]))
    confirm(engine, [b], [account])  # takes that slot first
    with pytest.raises(PlanChanged) as err:
        engine.posting.confirm(request([a], [account]), shown.plan_id)
    fresh = err.value.plan
    assert fresh.posts[0].scheduled_at != shown.posts[0].scheduled_at
    assert [p.clip_id for p in store.list_posts(engine.db())] == [b.id]
    # The new plan, once shown, goes through.
    _, (p,) = engine.posting.confirm(request([a], [account]), shown.plan_id)
    assert p.scheduled_at == fresh.posts[0].scheduled_at

    c = add_clip(engine, 3)
    r = request([c], [account])
    plan = engine.posting.plan(r)
    store.update_account(engine.db(), account.id, status="needs_login")
    with pytest.raises(PlanChanged) as err:
        engine.posting.confirm(r, plan.plan_id)
    assert err.value.plan.blocked
    store.update_account(engine.db(), account.id, status="connected")
    with pytest.raises(PlanChanged):
        engine.posting.confirm(r, "plan-never-shown")
    assert len(store.list_posts(engine.db())) == 2


# Keeping one job per post


def test_sync_is_idempotent(engine):
    _, posts = confirm(engine, [add_clip(engine)], [add_account(engine), add_account(engine, "instagram")])
    before = {p.id: [j.id for j in jobs(engine, p.id)] for p in posts}
    assert all(len(ids) == 1 for ids in before.values())
    queue.sync_jobs(engine)
    queue.sync_jobs(engine)
    assert {p.id: [j.id for j in jobs(engine, p.id)] for p in posts} == before

    lost, drifted = posts
    engine.runner.cancel(before[lost.id][0])
    engine.db().execute("UPDATE jobs SET not_before = 0 WHERE id = ?", (before[drifted.id][0],))
    stray = engine.runner.enqueue("post.browser", "t-not-a-post", rerun=True)
    queue.sync_jobs(engine)
    for p in posts:
        (job,) = jobs(engine, p.id)
        assert job.not_before == p.scheduled_at
    assert engine.runner.get(before[drifted.id][0]).status == "cancelled"
    assert engine.runner.get(stray).status == "cancelled"


def test_cancel_and_reschedule_move_the_job(engine):
    clip, account = add_clip(engine), add_account(engine)
    p = custom(engine, clip, account, at(7, 10))
    (old,) = jobs(engine, p.id)
    moved = engine.posting.reschedule(p.id, at(19).timestamp())
    assert moved.status == "scheduled"
    assert moved.scheduled_at == at(19).timestamp()
    (job,) = jobs(engine, p.id)
    assert job.id != old.id
    assert job.not_before == at(19).timestamp()
    assert engine.runner.get(old.id).status == "cancelled"

    engine.posting.reschedule(p.id, at(5).timestamp())  # a time already gone is now
    assert post(engine, p.id).scheduled_at == NOW.timestamp()

    gone = engine.posting.cancel(p.id)
    assert gone.status == "cancelled"
    assert not jobs(engine, p.id)
    with pytest.raises(LifecycleError):
        engine.posting.reschedule(p.id, at(20).timestamp())


def test_reschedule_keeps_the_accounts_rules(engine):
    """A time picked in the Queue keeps the same rules as one picked in the Post dialog: the account's
    windows, daily cap and spacing. One that breaks them is refused with the sentence and the next free
    time, and the post stays where it was."""
    account = add_account(engine)
    first = custom(engine, add_clip(engine), account, at(19))
    second = custom(engine, add_clip(engine), account, at(7, 10))
    with pytest.raises(ClipForgeError) as err:
        engine.posting.reschedule(second.id, at(20).timestamp())
    assert err.value.code == "spacing"
    assert err.value.message == (
        f"@{account.handle} has another post less than 3 hours from {at_text(at(20), SYD)}. "
        f"The next free time is {at_text(at(7, day=4), SYD)}."
    )
    with pytest.raises(ClipForgeError) as err:
        engine.posting.reschedule(second.id, at(3, day=4).timestamp())
    assert err.value.code == "outside"
    assert post(engine, second.id).scheduled_at == at(7, 10).timestamp()
    custom(engine, add_clip(engine), account, at(8, day=4))
    custom(engine, add_clip(engine), account, at(19, day=4))
    with pytest.raises(ClipForgeError) as err:
        engine.posting.reschedule(second.id, at(12, day=4).timestamp())
    assert err.value.code == "cap"
    assert engine.posting.reschedule(first.id, at(12, 30).timestamp()).scheduled_at == at(12, 30).timestamp()


def test_edit_cleans_the_words_and_refuses_what_cant_post(engine):
    p = custom(engine, add_clip(engine), add_account(engine, "instagram"), at(7, 10))
    edited = engine.posting.edit(p.id, caption="  New\twords\u202e ", hashtags=["#Money", "money", "big win"])
    assert (edited.caption, edited.hashtags) == ("New words", ["Money", "bigwin"])
    with pytest.raises(ClipForgeError) as err:
        engine.posting.edit(p.id, hashtags=[f"tag{n}" for n in range(31)])
    assert err.value.code == "too_many_hashtags"
    assert post(engine, p.id).hashtags == ["Money", "bigwin"]
    engine.posting.cancel(p.id)
    with pytest.raises(LifecycleError):
        engine.posting.edit(p.id, caption="Too late")


def test_edit_only_while_scheduled(engine):
    p = custom(engine, add_clip(engine), add_account(engine, "instagram"), at(7, 10))
    lifecycle.move(engine.db(), p.id, "uploading")
    lifecycle.move(engine.db(), p.id, "needs_you", error="Check it.")
    with pytest.raises(LifecycleError):
        engine.posting.edit(p.id, caption="New words")
    assert post(engine, p.id).caption != "New words"


def test_a_campaign_post_keeps_its_words_when_edited(engine):
    """A scheduled post of a campaign's clip takes no edit that drops a hashtag or mention the campaign
    needs, and says which."""
    campaign = add_campaign(engine, required={"hashtags": ["Beast"], "mentions": ["@MrBeast"]})
    p = custom(engine, linked_clip(engine, campaign), add_account(engine, "instagram"), at(7, 10))
    assert "Beast" in p.hashtags and "@MrBeast" in p.caption
    with pytest.raises(ClipForgeError) as err:
        engine.posting.edit(p.id, hashtags=["money"])
    assert (err.value.code, err.value.message) == (
        "rule_broken",
        "Beast clips: #Beast in the hashtags. Add #Beast back to the hashtags.",
    )
    with pytest.raises(ClipForgeError) as err:
        engine.posting.edit(p.id, caption="New words")
    assert err.value.message == "Beast clips: @MrBeast in the caption. Add @MrBeast back to the caption."
    assert (post(engine, p.id).caption, post(engine, p.id).hashtags) == (p.caption, p.hashtags)
    kept = engine.posting.edit(p.id, caption="New words @MrBeast", hashtags=["money", "Beast"])
    assert (kept.caption, kept.hashtags) == ("New words @MrBeast", ["money", "Beast"])


# Pause posting and paused accounts


def test_pause_posting_cancels_queued_jobs_and_resume_restores_them(engine):
    clip, account = add_clip(engine), add_account(engine)
    p = custom(engine, clip, account, at(7, 10))
    engine.posting.pause()
    assert engine.settings.posting.paused
    assert repo.load_settings(engine.db()).posting.paused
    assert not jobs(engine, p.id)
    assert post(engine, p.id).status == "scheduled"
    engine.posting.resume()
    assert not engine.settings.posting.paused
    (job,) = jobs(engine, p.id)
    assert job.not_before == p.scheduled_at
    assert post(engine, p.id).note is None


def test_resume_moves_posts_that_missed_their_window(engine, clock):
    clip, account = add_clip(engine), add_account(engine)
    p = custom(engine, clip, account, at(7, 10))
    engine.posting.pause()
    clock.set(at(9, 30))
    engine.posting.resume()
    moved = post(engine, p.id)
    assert at(12) <= sydney(moved.scheduled_at) <= at(12, 10)
    new = at_text(sydney(moved.scheduled_at), SYD)
    assert moved.note == f"Posting was paused at Tue 3 Nov, 7:10 am, so it moved to {new}."
    (job,) = jobs(engine, p.id)
    assert job.not_before == moved.scheduled_at


def test_pause_stops_before_the_final_step(engine, clock, tiktok):
    clip, account = add_clip(engine), add_account(engine)
    p = custom(engine, clip, account, at(7, 10))
    clock.set(at(7, 10))

    def pause_then_finish(post, report):
        report.step("caption", words=post.caption)
        engine.posting.pause()
        report.final_check()
        raise AssertionError("it went on while posting was paused")

    tiktok.outcomes = [pause_then_finish]
    job, after = run(engine, p.id)
    assert job.status == "done"
    assert after.status == "scheduled"
    assert after.note == "Paused before posting."
    assert not jobs(engine, p.id)
    steps = Path(after.log_path).read_text().splitlines()
    assert any('"step": "caption"' in line for line in steps)
    engine.posting.resume()
    (job,) = jobs(engine, p.id)
    assert job.not_before == p.scheduled_at


def test_paused_account_holds_its_posts(engine, clock, tiktok):
    a1, a2 = add_account(engine), add_account(engine)
    p1 = custom(engine, add_clip(engine, 1), a1, at(7, 10))
    p2 = custom(engine, add_clip(engine, 2), a2, at(7, 30))
    engine.posting.pause_account(a1.id, True)
    assert store.get_account(engine.db(), a1.id).paused
    assert not jobs(engine, p1.id)
    assert len(jobs(engine, p2.id)) == 1

    # A job that was already on its way stops before it posts.
    engine.runner.enqueue("post.browser", p1.id, rerun=True)
    clock.set(at(7, 10))
    _, after = run(engine, p1.id)
    assert after.status == "scheduled"
    assert not tiktok.published

    def pause_account_then_finish(post, report):
        engine.posting.pause_account(a2.id, True)
        report.final_check()

    tiktok.outcomes = [pause_account_then_finish]
    clock.set(at(7, 30))
    _, after = run(engine, p2.id)
    assert after.status == "scheduled"
    assert after.note == "Paused before posting."

    engine.posting.pause_account(a1.id, False)
    (job,) = jobs(engine, p1.id)
    assert job.not_before == p1.scheduled_at
    assert not jobs(engine, p2.id)


# Restarts


def test_restart_mid_browser_upload_needs_you(engine, clock, tiktok, instagram):
    p = custom(engine, add_clip(engine), add_account(engine), at(7, 10))
    clock.set(at(7, 10))
    claim(engine, p.id)
    lifecycle.move(engine.db(), p.id, "uploading")
    engine.close()
    again = open_engine(clock, tiktok, instagram)
    try:
        after = post(again, p.id)
        assert after.status == "needs_you"
        assert after.error == STOPPED
        assert not jobs(again, p.id)
        assert not jobs(again, p.id, "running")
        assert not tiktok.published
    finally:
        again.close()


def test_restart_mid_api_post_resumes(engine, clock, tiktok, instagram):
    p = custom(engine, add_clip(engine), add_account(engine, "instagram"), at(7, 10))
    clock.set(at(7, 10))
    claim(engine, p.id)
    lifecycle.move(engine.db(), p.id, "uploading", state={"container": "c1"})
    engine.close()
    again = open_engine(clock, tiktok, instagram)
    try:
        after = post(again, p.id)
        assert after.status == "uploading"
        (job,) = jobs(again, p.id)
        assert job.kind == "post.api"
        _, done = run(again, p.id)
        assert done.status == "posted"
        assert instagram.published[-1].state == {"container": "c1"}
    finally:
        again.close()


# Late posts


def test_late_post_inside_a_window_goes_now(engine, clock, tiktok):
    p = custom(engine, add_clip(engine), add_account(engine), at(7, 10))
    clock.set(at(7, 40))
    _, after = run(engine, p.id)
    assert after.status == "posted"
    assert after.scheduled_at == at(7, 10).timestamp()
    assert after.posted_at == at(7, 40).timestamp()
    assert after.note is None
    assert [x.id for x in tiktok.published] == [p.id]


def test_late_post_outside_moves_to_next_slot_with_a_note(engine, clock, tiktok):
    p = custom(engine, add_clip(engine), add_account(engine), at(7, 10))
    clock.set(at(9, 30))
    job, after = run(engine, p.id)
    assert after.status == "scheduled"
    assert not tiktok.published
    assert at(12) <= sydney(after.scheduled_at) <= at(12, 10)
    new = at_text(sydney(after.scheduled_at), SYD)
    assert after.note == f"This Mac was asleep at Tue 3 Nov, 7:10 am, so it moved to {new}."
    assert job.status == "queued"
    assert job.not_before == after.scheduled_at


def test_late_post_on_another_day_moves_too(engine, clock, tiktok):
    p = custom(engine, add_clip(engine), add_account(engine), at(7, 10))
    clock.set(at(7, 40, day=4))
    _, after = run(engine, p.id)
    assert after.status == "scheduled"
    assert sydney(after.scheduled_at) >= at(7, 40, day=4)
    assert not tiktok.published


def tick_through(engine, clock, until):
    """The posting ticks, two minutes apart, up to ``until``: ClipForge awake the whole time."""
    while clock() < until:
        clock.advance(minutes=2)
        queue.tick(engine)


def test_a_post_late_from_clipforges_own_queue_goes_now(engine, clock, tiktok):
    """Awake all along but held up behind other posts, a login or a render, it only waited its turn, so it
    goes as soon as it can even with its window closed."""
    p = custom(engine, add_clip(engine), add_account(engine), at(8, 55))
    engine.start_posting()
    tick_through(engine, clock, at(9, 30))
    _, after = run(engine, p.id)
    assert after.status == "posted" and after.note is None
    assert [x.id for x in tiktok.published] == [p.id]


def test_a_post_due_while_the_mac_slept_moves_with_the_sleep_sentence(engine, clock, tiktok):
    first = custom(engine, add_clip(engine, 1), add_account(engine), at(8, 55))
    second = custom(engine, add_clip(engine, 2), add_account(engine), at(8, 35))
    engine.start_posting()
    tick_through(engine, clock, at(8, 30))
    clock.set(at(9, 30))  # asleep from 8:30 to 9:30, so no ticks in between
    _, woke = run(engine, first.id)  # before a tick has seen the gap
    queue.tick(engine)
    _, later = run(engine, second.id)
    assert not tiktok.published
    for after, old in ((woke, "8:55 am"), (later, "8:35 am")):
        new = at_text(sydney(after.scheduled_at), SYD)
        assert after.status == "scheduled"
        assert after.note == f"This Mac was asleep at Tue 3 Nov, {old}, so it moved to {new}."


def test_a_post_due_before_clipforge_opened_says_so(engine, clock, tiktok):
    p = custom(engine, add_clip(engine), add_account(engine), at(7, 10))
    clock.set(at(9, 30))
    engine.start_posting()
    _, after = run(engine, p.id)
    assert after.status == "scheduled" and not tiktok.published
    new = at_text(sydney(after.scheduled_at), SYD)
    assert after.note == f"ClipForge wasn't open at Tue 3 Nov, 7:10 am, so it moved to {new}."


# What gets posted


def test_waits_for_a_running_render(engine, clock, tiktok):
    clip = add_clip(engine)
    p = custom(engine, clip, add_account(engine), at(7, 10))
    clock.set(at(7, 10))
    pipeline.queue_render(engine.runner, engine.db(), clip.id)
    job, after = run(engine, p.id)
    assert after.status == "rendering"
    assert job.status == "queued"
    assert job.not_before == at(7, 10).timestamp() + 30
    assert job.message == "Waiting for the clip to finish rendering."
    assert not tiktok.published

    engine.db().execute("UPDATE jobs SET status = 'done' WHERE ref_id = ?", (clip.id,))
    new = add_render(engine, clip, 2)
    _, after = run(engine, p.id)
    assert after.status == "posted"
    assert after.render_id == new.id


def test_no_render_fails_with_a_sentence(engine, clock, tiktok):
    clip = add_clip(engine)
    p = custom(engine, clip, add_account(engine), at(7, 10))
    Path(repo.get_render(engine.db(), clip.current_render_id).path).unlink()
    clock.set(at(7, 10))
    _, after = run(engine, p.id)
    assert after.status == "failed"
    assert after.error == "This clip has no video yet. Render it, then press Retry."
    assert after.error_code == "no_render"
    assert not tiktok.published


def test_deleted_clip_fails_its_post_with_a_sentence(engine, clock, tiktok):
    clip = add_clip(engine)
    p = custom(engine, clip, add_account(engine), at(7, 10))
    engine.delete_project(clip.project_id)
    assert post(engine, p.id).clip_id is None
    clock.set(at(7, 10))
    _, after = run(engine, p.id)
    assert after.status == "failed"
    assert after.error == "The clip was deleted, so this post can't go out."
    assert after.error_code == "clip_deleted"
    assert not tiktok.published


def test_rerendered_clip_posts_the_current_render(engine, clock, tiktok):
    clip = add_clip(engine)
    p = custom(engine, clip, add_account(engine), at(7, 10))
    new = add_render(engine, clip, 2)
    seen = []

    def look(post, report):
        seen.append(report.video.read_bytes())
        return PublishResult(external_id="v2", url="https://www.tiktok.com/@me/video/2")

    tiktok.outcomes = [look]
    clock.set(at(7, 10))
    _, after = run(engine, p.id)
    assert seen == [b"video 2"]
    assert after.render_id == new.id
    assert tiktok.published[0].render_id == new.id
    assert (after.external_id, after.url) == ("v2", "https://www.tiktok.com/@me/video/2")


def test_a_campaign_post_rendered_again_is_checked_before_it_goes(engine, clock, tiktok):
    """A campaign post whose clip was rendered again since it was approved goes to you, naming the rule,
    when the new render breaks one, and posts once a render keeps them."""
    campaign = add_campaign(engine, max_s=45.0)
    clip = linked_clip(engine, campaign)
    p = custom(engine, clip, add_account(engine), at(7, 10))
    add_render(engine, clip, 2, length=60.0)
    clock.set(at(7, 10))
    _, after = run(engine, p.id)
    assert after.status == "needs_you"
    assert after.error == (
        "Beast clips: At most 45 seconds long. The clip is 60 seconds long."
        " Change its length and render it again."
    )
    assert not tiktok.published
    fixed = add_render(engine, clip, 3, length=40.0)
    engine.posting.reschedule(p.id, at(7, 10).timestamp())
    _, after = run(engine, p.id)
    assert (after.status, after.render_id) == ("posted", fixed.id)


def test_a_post_rendered_again_carries_the_new_music_credit(engine, clock, tiktok):
    """The music credit in a post's caption follows the render that goes up: a new track's credit takes the
    old one's place, and a render with no music takes the credit out."""
    music = {"item_id": "l1", "title": "Night Drive", "artist": "Lena Hart", "db": -12.0}

    def with_credit(credit):
        return edl_for([(10.0, 40.0)]).model_copy(update={"music": {**music, "credit": credit}})

    old, new = "Music: Night Drive by Lena Hart", "Music: Sunrise by Lena Hart"
    first = add_clip(engine, rendered=False)
    add_render(engine, first, 1, edl=with_credit(old))
    second = add_clip(engine, rendered=False)
    add_render(engine, second, 1, edl=with_credit(old))
    p = custom(engine, repo.get_clip(engine.db(), first.id), add_account(engine), at(7, 10))
    q = custom(engine, repo.get_clip(engine.db(), second.id), add_account(engine), at(7, 40))
    assert p.caption == q.caption == f"TikTok words\n\n{old}"
    add_render(engine, first, 2, edl=with_credit(new))
    add_render(engine, second, 2, edl=edl_for([(10.0, 40.0)]))  # music taken off
    clock.set(at(7, 10))
    _, after = run(engine, p.id)
    assert (after.status, after.caption) == ("posted", f"TikTok words\n\n{new}")
    assert tiktok.published[0].caption == f"TikTok words\n\n{new}"
    clock.set(at(7, 40))
    _, after = run(engine, q.id)
    assert (after.status, after.caption) == ("posted", "TikTok words")


# What a publisher says


def test_api_retryable_backs_off_three_times_then_fails(engine, clock, instagram):
    p = custom(engine, add_clip(engine), add_account(engine, "instagram"), at(7, 10))
    clock.set(at(7, 10))
    instagram.outcomes = [Retryable("Instagram is busy.")] * 4
    for wait in (60, 300, 900):
        job, after = run(engine, p.id)
        assert job.status == "queued"
        assert job.not_before == at(7, 10).timestamp() + wait
        assert after.status == "uploading"
        assert after.note == "Instagram is busy."
    job, after = run(engine, p.id)
    assert after.status == "failed"
    assert after.error == "Instagram is busy."
    assert after.error_code == "retries_used"
    assert job.status == "done"
    assert len(instagram.published) == 4


def test_browser_retryable_goes_to_needs_you(engine, clock, tiktok, instagram):
    p = custom(engine, add_clip(engine, 1), add_account(engine), at(7, 10))
    q = custom(engine, add_clip(engine, 2), add_account(engine, "instagram"), at(7, 30))
    tiktok.outcomes = [Retryable("TikTok didn't answer.", details="timeout")]
    instagram.outcomes = [Retryable("Instagram may already have this post.", safe=False)]
    clock.set(at(7, 30))
    job, after = run(engine, p.id)
    assert job.status == "done"
    assert after.status == "needs_you"
    assert (after.error, after.details) == ("TikTok didn't answer.", "timeout")
    _, after = run(engine, q.id)
    assert after.status == "needs_you"
    assert after.error == "Instagram may already have this post."


def test_fatal_fails_with_its_code(engine, clock, tiktok):
    p = custom(engine, add_clip(engine, 1), add_account(engine), at(7, 10))
    tiktok.outcomes = [Fatal("TikTok turned this video down.", "rejected")]
    clock.set(at(7, 10))
    _, after = run(engine, p.id)
    assert (after.status, after.error, after.error_code) == (
        "failed",
        "TikTok turned this video down.",
        "rejected",
    )

    browser = add_account(engine, "instagram", method="browser")
    q = custom(engine, add_clip(engine, 2), browser, at(7, 30))
    clock.set(at(7, 30))
    _, after = run(engine, q.id)
    assert after.status == "failed"
    assert (
        after.error == "ClipForge can't post to Instagram this way yet. Export this post and post it by hand."
    )
    assert after.error_code == "no_route"


def test_unexpected_error_needs_you(engine, clock, tiktok):
    p = custom(engine, add_clip(engine), add_account(engine), at(7, 10))
    tiktok.outcomes = [RuntimeError("the page changed")]
    clock.set(at(7, 10))
    _, after = run(engine, p.id)
    assert after.status == "needs_you"
    assert after.error == (
        "Something went wrong while posting. Check TikTok Studio, then press Retry or Mark as posted."
    )
    assert "RuntimeError: the page changed" in after.details


def test_mark_posted_and_retry_from_needs_you(engine, clock, tiktok):
    clip, account = add_clip(engine), add_account(engine)
    p = custom(engine, clip, account, at(7, 10))
    with pytest.raises(LifecycleError):
        engine.posting.mark_posted(p.id)
    login = NeedsUser("Log in to TikTok again.", screenshot="/shots/01-login.png", actions=("open_window",))
    tiktok.outcomes = [login, login]
    clock.set(at(7, 10))
    _, after = run(engine, p.id)
    assert after.status == "needs_you"
    assert after.error == "Log in to TikTok again."
    assert after.state["actions"] == ["open_window"]
    assert after.state["screenshot"] == "/shots/01-login.png"
    assert not jobs(engine, p.id)

    clock.set(at(7, 20))
    again = engine.posting.retry(p.id)
    assert again.status == "scheduled"
    assert again.error is None
    assert "actions" not in again.state
    assert at(7, 20) <= sydney(again.scheduled_at) <= at(7, 35)
    (job,) = jobs(engine, p.id)
    assert job.not_before == again.scheduled_at

    clock.set(sydney(again.scheduled_at))
    _, after = run(engine, p.id)
    assert after.status == "needs_you"
    done = engine.posting.mark_posted(p.id, url="https://www.tiktok.com/@me/video/1")
    assert done.status == "posted"
    assert done.url == "https://www.tiktok.com/@me/video/1"
    assert done.posted_at == sydney(again.scheduled_at).timestamp()
    assert done.error is None
    with pytest.raises(LifecycleError):
        engine.posting.retry(p.id)


def test_post_and_account_events_published(engine, clock):
    clip, account = add_clip(engine), add_account(engine)
    sub = engine.events.subscribe()
    p = custom(engine, clip, account, at(7, 10))
    engine.posting.pause_account(account.id, True)
    engine.posting.pause_account(account.id, False)
    clock.set(at(7, 10))
    run(engine, p.id)
    seen = drain(sub, "post", "account")
    assert seen[0] == (
        "post",
        {
            "id": p.id,
            "status": "scheduled",
            "account_id": account.id,
            "batch_id": p.batch_id,
            "scheduled_at": p.scheduled_at,
            "error": None,
        },
    )
    assert ("account", {"id": account.id, "status": "connected", "paused": True}) in seen
    assert ("account", {"id": account.id, "status": "connected", "paused": False}) in seen
    assert [d["status"] for t, d in seen if t == "post"][-2:] == ["uploading", "posted"]


def test_due_post_runs_on_the_post_lane(tiktok, instagram):
    engine = fake_engine(publishers={"tiktok:browser": tiktok, "instagram:api": instagram})
    try:
        _, (p,) = confirm(engine, [add_clip(engine)], [add_account(engine)], timing="now")
        assert engine.wait(timeout_s=5)  # waiting for projects leaves posts to the posting lanes
        engine.runner.start()
        assert not engine.runner.run_until_idle(timeout_s=0.5, refs=[p.id])  # held until posting starts
        engine.start_posting()
        assert engine.runner.run_until_idle(timeout_s=10, refs=[p.id])
        assert post(engine, p.id).status == "posted"
        assert [x.id for x in tiktok.published] == [p.id]
    finally:
        engine.close()


# TikTok Studio's own schedule (SPEC 10.3)

NO_ROW = (
    "TikTok's posts list didn't show this post yet. "
    "Check TikTok Studio, then mark it as posted with its link."
)
HELD = PublishResult(external_id=None, url=None, scheduled=True)


def studio_slot(p):
    return datetime.fromisoformat(p.state["studio"]["slot"])


def held_on_tiktok(engine, clock, tiktok, account=None):
    """An assisted TikTok post confirmed at 9:00 for 6:00 pm, uploaded with TikTok Studio's schedule set
    and Schedule pressed."""
    clock.set(at(9))
    p = custom(engine, add_clip(engine), account or add_account(engine, assisted=True), at(18))
    tiktok.outcomes = [HELD]
    _, after = run(engine, p.id)
    assert after.status == "scheduled_on_tiktok"
    return after


def test_assisted_tiktok_uploads_ahead(engine, clock, tiktok):
    clock.set(at(9))
    p = custom(engine, add_clip(engine), add_account(engine, assisted=True), at(18, 2))
    assert studio_slot(p) == at(18, 2)
    assert p.scheduled_at == at(18, 2).timestamp()  # the Queue and the caps still see its slot
    (job,) = jobs(engine, p.id)
    assert job.not_before <= at(9).timestamp()  # due at once
    assert queue.next_due(engine) <= at(9)

    tiktok.outcomes = [HELD]
    _, after = run(engine, p.id)
    (sent,) = tiktok.published
    assert studio_slot(sent) == at(18, 2)  # the run sets TikTok's schedule from it, rounded to 5 minutes
    assert after.status == "scheduled_on_tiktok"
    assert after.scheduled_at == at(18).timestamp()  # as TikTok Studio holds it
    assert after.posted_at is None
    assert after.error is None
    assert queue.next_due(engine) is None  # TikTok puts it up, so the Mac needn't stay awake for it


def test_slot_too_close_waits_as_before(engine, clock, tiktok):
    clock.set(at(7))
    p = custom(engine, add_clip(engine), add_account(engine, assisted=True), at(7, 15))  # under 20 minutes
    assert "studio" not in p.state
    (job,) = jobs(engine, p.id)
    assert job.not_before == p.scheduled_at
    clock.set(at(7, 15))
    _, after = run(engine, p.id)
    assert after.status == "posted"
    assert [x.id for x in tiktok.published] == [p.id]


def test_only_assisted_tiktok_posts_upload_ahead_and_only_with_the_setting_on(engine, clock):
    clock.set(at(9))
    clip = add_clip(engine)
    for account in (add_account(engine), add_account(engine, "instagram")):
        p = custom(engine, clip, account, at(19))
        assert "studio" not in p.state
        (job,) = jobs(engine, p.id)
        assert job.not_before == p.scheduled_at
    settings = engine.settings
    posting = settings.posting.model_copy(update={"tiktok_schedule": False})
    engine.update_settings(settings.model_copy(update={"posting": posting}))
    p = custom(engine, clip, add_account(engine, assisted=True), at(20))
    assert "studio" not in p.state
    (job,) = jobs(engine, p.id)
    assert job.not_before == p.scheduled_at


def test_a_post_whose_turn_comes_too_late_for_tiktok_studio_waits_for_its_slot(engine, clock, tiktok):
    """Posts go out one at a time, so one can wait its turn until its slot is under 20 minutes away: it
    then goes up at its time as any other post does, never to you."""
    clock.set(at(9))
    p = custom(engine, add_clip(engine), add_account(engine, assisted=True), at(18))
    clock.set(at(17, 45))
    job, after = run(engine, p.id)
    assert after.status == "scheduled" and "studio" not in after.state
    assert job.status == "queued" and job.not_before == at(18).timestamp()
    assert not tiktok.published
    clock.set(at(18))
    _, after = run(engine, p.id)
    assert after.status == "posted"
    (sent,) = tiktok.published
    assert "studio" not in sent.state


def test_assisted_posting_turned_on_gives_queued_posts_tiktoks_own_schedule(engine, clock):
    """Turned on after its posts were confirmed, the account's switch uploads them ahead as if it had been
    on then, so they wait for your Schedule, not for you at their slots; turned off, they wait for their
    slots again."""
    clock.set(at(9))
    account = add_account(engine)
    p = custom(engine, add_clip(engine), account, at(18))
    assert "studio" not in p.state
    client = api_client(engine)
    answer = client.patch(f"/api/accounts/{account.id}", json={"settings": {"assisted": True}})
    assert answer.status_code == 200
    assert studio_slot(post(engine, p.id)) == at(18)
    (job,) = jobs(engine, p.id)
    assert job.not_before <= at(9).timestamp()
    answer = client.patch(f"/api/accounts/{account.id}", json={"settings": {"assisted": False}})
    assert answer.status_code == 200
    assert "studio" not in post(engine, p.id).state
    (job,) = jobs(engine, p.id)
    assert job.not_before == p.scheduled_at


def test_assisted_posting_turned_off_takes_tiktoks_own_schedule_from_queued_posts(engine, clock):
    """Confirmed while it was on, the post took the switch with it; turned off, it posts at its slot."""
    clock.set(at(9))
    account = add_account(engine, assisted=True)
    p = custom(engine, add_clip(engine), account, at(18))
    assert studio_slot(p) == at(18)
    answer = api_client(engine).patch(f"/api/accounts/{account.id}", json={"settings": {"assisted": False}})
    assert answer.status_code == 200
    after = post(engine, p.id)
    assert "studio" not in after.state and not after.options.assisted
    (job,) = jobs(engine, p.id)
    assert job.not_before == p.scheduled_at


@pytest.mark.parametrize("status", ["uploading", "processing"])
def test_assisted_posting_turned_off_reaches_a_post_already_filling_in(engine, clock, status):
    """Its window still waits for you, as its run began that way, but once that run ends its Retry
    posts on its own."""
    clock.set(at(9))
    account = add_account(engine, assisted=True)
    p = custom(engine, add_clip(engine), account, at(18))
    store.update_post(engine.db(), p.id, status=status)
    answer = api_client(engine).patch(f"/api/accounts/{account.id}", json={"settings": {"assisted": False}})
    assert answer.status_code == 200
    assert not post(engine, p.id).options.assisted


def test_tiktoks_own_schedule_turned_off_or_on_reaches_queued_posts(engine, clock):
    """As ClipForge tells you to when TikTok moves its Schedule controls: turned off in Settings, the
    posts already waiting post at their slots; turned on again, TikTok Studio holds them again."""
    clock.set(at(9))
    p = custom(engine, add_clip(engine), add_account(engine, assisted=True), at(18))
    assert studio_slot(p) == at(18)
    client = api_client(engine)
    posting = engine.settings.posting.model_dump(mode="json")
    answer = client.put("/api/settings", json={"posting": posting | {"tiktok_schedule": False}})
    assert answer.status_code == 200
    assert "studio" not in post(engine, p.id).state
    (job,) = jobs(engine, p.id)
    assert job.not_before == p.scheduled_at
    answer = client.put("/api/settings", json={"posting": posting | {"tiktok_schedule": True}})
    assert answer.status_code == 200
    assert studio_slot(post(engine, p.id)) == at(18)
    (job,) = jobs(engine, p.id)
    assert job.not_before <= at(9).timestamp()


def test_moving_a_post_sets_its_studio_slot_again(engine, clock):
    clock.set(at(12))
    p = custom(engine, add_clip(engine), add_account(engine, assisted=True), at(18))
    moved = engine.posting.reschedule(p.id, at(20).timestamp())
    assert studio_slot(moved) == at(20)
    near = engine.posting.reschedule(p.id, at(12, 10).timestamp())  # too close for TikTok Studio now
    assert "studio" not in near.state
    (job,) = jobs(engine, p.id)
    assert job.not_before == near.scheduled_at


def test_link_is_read_once_after_the_slot(engine, clock, tiktok):
    p = held_on_tiktok(engine, clock, tiktok)
    (job,) = jobs(engine, p.id)
    assert job.not_before == at(18, 10).timestamp()

    clock.set(at(18, 9))
    job, after = run(engine, p.id)
    assert after.status == "scheduled_on_tiktok"
    assert job.status == "queued" and job.not_before == at(18, 10).timestamp()
    assert not tiktok.looked

    clock.set(at(18, 10))
    link = "https://www.tiktok.com/@me/video/7311111111111111111"
    tiktok.links = [PublishResult(external_id="7311111111111111111", url=link)]
    _, after = run(engine, p.id)
    assert tiktok.looked == [p.id]
    assert after.status == "posted"
    assert (after.external_id, after.url) == ("7311111111111111111", link)
    assert after.posted_at == at(18).timestamp()  # when TikTok put it up

    clock.advance(minutes=2)
    queue.tick(engine)
    assert not jobs(engine, p.id)
    assert tiktok.looked == [p.id]
    assert len(tiktok.published) == 1  # nothing uploaded again


def test_missing_row_needs_you_once(engine, clock, tiktok):
    p = held_on_tiktok(engine, clock, tiktok)
    clock.set(at(18, 10))
    _, after = run(engine, p.id)
    assert tiktok.looked == [p.id]
    assert after.status == "needs_you"
    assert after.error == NO_ROW
    clock.advance(minutes=2)
    queue.tick(engine)
    assert not jobs(engine, p.id)
    assert tiktok.looked == [p.id]

    for again in (engine.posting.retry, engine.posting.export):  # either would put it up a second time
        with pytest.raises(LifecycleError):
            again(p.id)
    with pytest.raises(LifecycleError):
        engine.posting.reschedule(p.id, at(20).timestamp())
    done = engine.posting.mark_posted(p.id, url="https://www.tiktok.com/@me/video/7")
    assert done.status == "posted"
    assert len(tiktok.published) == 1


def test_a_link_read_that_breaks_needs_you_with_the_same_sentence(engine, clock, tiktok):
    p = held_on_tiktok(engine, clock, tiktok)
    clock.set(at(18, 10))
    tiktok.links = [RuntimeError("the page went away")]
    _, after = run(engine, p.id)
    assert after.status == "needs_you"
    assert after.error == NO_ROW
    assert "RuntimeError" in after.details


def test_scheduled_on_tiktok_waits_while_posting_is_paused(engine, clock, tiktok):
    p = held_on_tiktok(engine, clock, tiktok)
    engine.posting.pause()
    assert not jobs(engine, p.id)
    clock.set(at(18, 30))
    engine.posting.resume()  # its slot passed while paused, but TikTok put it up: it isn't moved
    after = post(engine, p.id)
    assert after.status == "scheduled_on_tiktok"
    assert after.scheduled_at == at(18).timestamp()
    (job,) = jobs(engine, p.id)
    assert job.not_before == at(18, 10).timestamp()


def test_scheduled_on_tiktok_counts_toward_the_cap(engine, clock, tiktok):
    account = add_account(engine, assisted=True, daily_cap=1)
    p = held_on_tiktok(engine, clock, tiktok, account)
    day = (at(0).timestamp(), at(0, day=4).timestamp())
    assert [x.id for x in store.posts_between(engine.db(), account.id, *day)] == [p.id]
    plan = engine.posting.plan(request([add_clip(engine, 2)], [account]))
    (planned,) = plan.posts
    assert sydney(planned.scheduled_at).date() == date(2026, 11, 4)  # 3 Nov's one post is taken


def test_cancelling_a_post_scheduled_on_tiktok_stops_following_it(engine, clock, tiktok):
    p = held_on_tiktok(engine, clock, tiktok)
    gone = engine.posting.cancel(p.id)
    assert gone.status == "cancelled"
    assert not jobs(engine, p.id)
