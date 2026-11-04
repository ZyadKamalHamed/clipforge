"""A check's drafts (SPEC 10.9): once every project the check started has finished and its clips have
rendered, the best clips that pass QA are planned to the clipping channel's accounts, up to what they can
post in a day, and saved as drafts that take no slot and have no job."""

import time
from collections import Counter

import pytest

from clipforge import repo
from clipforge.campaigns import store as campaigns
from clipforge.campaigns.models import Campaign, Required
from clipforge.jobs import pipeline
from clipforge.post import store
from clipforge.post.models import PlanRequest
from clipforge.post.plan import build_plan
from clipforge.post.slots import rules_for
from clipforge.today import store as today_store
from clipforge.today.models import Day
from clipforge.watch import drafts
from clipforge.watch import store as watch_store
from tests.fakes import Clock, fake_engine
from tests.posting import PACK, add_account, add_render, at, jobs
from tests.watching import add_channel, add_check, watched


@pytest.fixture
def engine():
    eng = fake_engine(now=Clock(at(7, 10)))
    yield eng
    eng.close()


def drafted(engine) -> list:
    return store.list_posts(engine.db(), status=["draft"])


def accounts(engine, tiktok: int = 1, instagram: int = 1, *, tiktok_cap: int = 1, instagram_cap: int = 1):
    return [
        *(add_account(engine, "tiktok", f"t{n}", daily_cap=tiktok_cap) for n in range(tiktok)),
        *(add_account(engine, "instagram", f"i{n}", daily_cap=instagram_cap) for n in range(instagram)),
    ]


def test_two_tiktok_two_instagram_make_four_clips_as_six_posts(engine):
    mine = accounts(engine, 2, 2, tiktok_cap=2, instagram_cap=1)
    resting = add_account(engine, "tiktok", "resting", daily_cap=2)
    store.update_account(engine.db(), resting.id, paused=True)
    gone = add_account(engine, "instagram", "gone")
    store.delete_account(engine.db(), gone.id)
    ch = add_channel(engine, [*mine, resting, gone])
    check = add_check(engine, ch)
    _, first = watched(engine, ch, check, [90, 60, 40])
    pid, second = watched(engine, ch, check, [80, 70])
    assert drafts.day_capacity(engine, ch) == {"tiktok": 4, "instagram": 2}

    drafts.settle(engine, pid)
    posts = drafted(engine)
    assert len(posts) == 6
    tiktok = [p.clip_id for p in posts if p.platform == "tiktok"]
    assert set(tiktok) == {first[0].id, second[0].id, second[1].id, first[1].id}  # 90, 80, 70 and 60
    assert {p.clip_id for p in posts if p.platform == "instagram"} == {first[0].id, second[0].id}
    assert Counter(p.account_id for p in posts) == {a.id: 2 if a.platform == "tiktok" else 1 for a in mine}
    assert {p.batch_id for p in posts} == {store.batch_for_plan(engine.db(), f"draft:{check.id}")}
    assert all(jobs(engine, p.id) == [] for p in posts)  # a draft never posts on its own

    done = watch_store.get_check(engine.db(), check.id)
    assert done.drafts == 6
    assert done.drafted_at == engine.now().timestamp()
    assert {i.status for i in watch_store.items_of_check(engine.db(), check.id)} == {"drafted"}


def test_clips_per_day_lowers_it(engine):
    mine = accounts(engine, 2, 1, tiktok_cap=2)
    ch = add_channel(engine, mine, clips_per_day=1)
    check = add_check(engine, ch)
    pid, clips = watched(engine, ch, check, [50, 90, 70])
    drafts.settle(engine, pid)
    assert sorted((p.platform, p.clip_id) for p in drafted(engine)) == [
        ("instagram", clips[1].id),
        ("tiktok", clips[1].id),
    ]


def test_qa_failing_clip_skips_that_platform(engine):
    ch = add_channel(engine, accounts(engine))
    check = add_check(engine, ch)
    pid, clips = watched(engine, ch, check, [90, 60], fails={0: "instagram"})
    drafts.settle(engine, pid)
    assert sorted((p.platform, p.clip_id) for p in drafted(engine)) == [
        ("instagram", clips[1].id),
        ("tiktok", clips[0].id),
    ]


def test_waits_for_renders(engine):
    ch = add_channel(engine, accounts(engine))
    check = add_check(engine, ch)
    pid, clips = watched(engine, ch, check, [90, 60], rendered=False)
    for clip in clips:
        engine.runner.enqueue("clip.render", clip.id, {"project": pid})
    drafts.settle(engine, pid)
    assert drafted(engine) == []
    assert watch_store.get_check(engine.db(), check.id).drafted_at is None

    for n, clip in enumerate(clips):
        add_render(engine, clip)
        engine.db().execute("UPDATE jobs SET status = 'done' WHERE ref_id = ?", (clip.id,))
        if n == 0:
            drafts.settle(engine, pid)
            assert drafted(engine) == []  # the other one is still rendering
    drafts.settle(engine, pid)
    assert len(drafted(engine)) == 2


def test_an_open_check_waits(engine):
    ch = add_channel(engine, accounts(engine))
    check = add_check(engine, ch, finished=False)
    pid, _ = watched(engine, ch, check, [90])
    drafts.settle(engine, pid)
    assert drafted(engine) == []  # it may still start another project
    watch_store.update_check(engine.db(), check.id, finished_at=engine.now().timestamp())
    drafts.settle_check(engine, check.id)
    assert len(drafted(engine)) == 2


def test_settle_twice_drafts_once(engine):
    ch = add_channel(engine, accounts(engine))
    check = add_check(engine, ch)
    pid, _ = watched(engine, ch, check, [90, 60])
    drafts.settle(engine, pid)
    first = drafted(engine)
    drafts.settle(engine, pid)
    assert [p.id for p in drafts.make_drafts(engine, check.id)] == [p.id for p in first]
    assert drafted(engine) == first


def test_failed_projects_make_no_drafts_and_close_the_check(engine):
    ch = add_channel(engine, accounts(engine))
    check = add_check(engine, ch)
    watched(engine, ch, check, [90], status="failed")
    pid, _ = watched(engine, ch, check, [80], status="refused", rendered=False)
    drafts.settle(engine, pid)
    assert drafted(engine) == []
    assert store.batch_for_plan(engine.db(), f"draft:{check.id}") is None
    done = watch_store.get_check(engine.db(), check.id)
    assert (done.drafts, done.drafted_at) == (0, engine.now().timestamp())
    assert {i.status for i in watch_store.items_of_check(engine.db(), check.id)} == {"failed"}


def test_a_running_project_holds_the_drafts(engine):
    ch = add_channel(engine, accounts(engine))
    check = add_check(engine, ch)
    ready, _ = watched(engine, ch, check, [90])
    running, _ = watched(engine, ch, check, [80], status="running")
    drafts.settle(engine, ready)
    assert drafted(engine) == []
    engine.update_project(running, status="ready")
    drafts.settle(engine, running)
    assert len(drafted(engine)) == 2


def test_channel_hashtags_come_first(engine):
    ch = add_channel(engine, accounts(engine), options={"hashtags": ["#podcast", "Money"]})
    check = add_check(engine, ch)
    many = PACK | {"instagram_hashtags": [f"tag{n}" for n in range(35)]}
    pid, _ = watched(engine, ch, check, [90], packaging=many)
    drafts.settle(engine, pid)
    by = {p.platform: p for p in drafted(engine)}
    assert by["tiktok"].hashtags == ["podcast", "Money"]  # the clip's "#money" is the same tag
    assert by["instagram"].hashtags == ["podcast", "Money", *(f"tag{n}" for n in range(28))]  # 30 at most


def a_campaign(engine, **rules) -> Campaign:
    now = time.time()
    return campaigns.add_campaign(
        engine.db(),
        Campaign(
            id=repo.new_id("cp"),
            name="Beast clips",
            marketplace="Whop",
            created_at=now,
            updated_at=now,
            **rules,
        ),
    )


def linked_watch(engine, ch, check, saved, **kw):
    pid, clips = watched(engine, ch, check, [90], **kw)
    repo.update_project(engine.db(), pid, campaign_id=saved.id, rights_basis="clipping_programme")
    return pid, clips


def test_drafts_go_only_to_allowed_platforms(engine):
    saved = a_campaign(engine, platforms=["tiktok"])
    mine = accounts(engine)
    ch = add_channel(engine, mine, campaign_id=saved.id)
    assert drafts.day_capacity(engine, ch) == {"tiktok": 1}
    pid, _ = linked_watch(engine, ch, add_check(engine, ch), saved)
    drafts.settle(engine, pid)
    assert [p.platform for p in drafted(engine)] == ["tiktok"]
    other = add_channel(engine, mine, name="Other")
    assert drafts.day_capacity(engine, other) == {"tiktok": 1, "instagram": 1}


def test_drafts_keep_required_hashtags_through_the_cut(engine):
    saved = a_campaign(engine, required=Required(hashtags=["Beast", "Clips", "Win"]))
    tags = [f"ch{n}" for n in range(10)]
    ch = add_channel(engine, accounts(engine), campaign_id=saved.id, options={"hashtags": tags})
    many = PACK | {"instagram_hashtags": [f"tag{n}" for n in range(35)]}
    pid, _ = linked_watch(engine, ch, add_check(engine, ch), saved, packaging=many)
    drafts.settle(engine, pid)
    by = {p.platform: p for p in drafted(engine)}
    assert by["instagram"].hashtags == ["Beast", "Clips", "Win", *tags, *(f"tag{n}" for n in range(17))]
    assert by["tiktok"].hashtags == ["Beast", "Clips", "Win", *tags, "money"]


def test_linked_drafts_ignore_creator_rewards(engine):
    rewards = add_account(engine, "tiktok", "rewards", creator_rewards=True)
    plain = add_account(engine, "tiktok", "plain")
    ch = add_channel(engine, [rewards, plain])
    pid, _ = watched(engine, ch, add_check(engine, ch), [90])  # a 30 s clip
    drafts.settle(engine, pid)
    (first,) = drafted(engine)
    assert first.account_id == plain.id
    saved = a_campaign(engine, min_s=20, max_s=40)
    linked = add_channel(engine, [rewards, plain], name="Linked", campaign_id=saved.id)
    pid, _ = linked_watch(engine, linked, add_check(engine, linked), saved)
    drafts.settle(engine, pid)
    after = [p.account_id for p in drafted(engine) if p.id != first.id]
    assert after == [rewards.id]  # the campaign set the length


def test_drafts_do_not_take_slots(engine):
    (tiktok,) = accounts(engine, 1, 0)
    ch = add_channel(engine, [tiktok])
    check = add_check(engine, ch)
    pid, (clip, other) = watched(engine, ch, check, [90, 60])
    watch_store.update_channel(engine.db(), ch.id, clips_per_day=1)
    drafts.settle(engine, pid)
    (draft,) = drafted(engine)
    assert draft.clip_id == clip.id
    req = PlanRequest(
        clip_ids=[other.id],
        account_ids=[tiktok.id],
        timing="custom",
        custom_times={f"{other.id}:{tiktok.id}": draft.scheduled_at},
    )
    plan = build_plan(engine, req, plan_id="p1", now=engine.now())
    assert not plan.blocked, [w.sentence for w in plan.warnings]
    assert plan.posts[0].scheduled_at == draft.scheduled_at


def test_drafts_keep_clear_of_other_channels_drafts(engine):
    t0, t1 = accounts(engine, 2, 0)
    made = []
    for score, account in ((90, t0), (80, t1)):
        ch = add_channel(engine, [account], f"Channel {score}")
        pid, _ = watched(engine, ch, add_check(engine, ch), [score])
        drafts.settle(engine, pid)
        made.append(next(p for p in drafted(engine) if p.account_id == account.id))
    gap = rules_for(engine.settings.posting, t1).platform_gap.total_seconds()
    assert abs(made[0].scheduled_at - made[1].scheduled_at) >= gap  # the second channel saw the first's
    review = build_plan(engine, PlanRequest(draft_ids=[p.id for p in made]), plan_id="r", now=engine.now())
    assert [p.scheduled_at for p in review.posts] == [p.scheduled_at for p in made]
    assert not [w for w in review.warnings if w.code == "slot_moved"]


def test_the_last_render_finishing_makes_the_drafts(engine):
    ch = add_channel(engine, accounts(engine))
    check = add_check(engine, ch)
    pid, (clip,) = watched(engine, ch, check, [90])
    for stage in pipeline.CLIP_STAGES:
        engine.runner.enqueue(stage.kind, clip.id, {"project": pid})
    engine.db().execute(
        "UPDATE jobs SET status = 'done', finished_at = ? WHERE ref_id = ? AND kind != 'clip.render'",
        (time.time(), clip.id),
    )
    (render,) = [j for j in engine.runner.jobs_for(clip.id) if j.kind == "clip.render"]
    pipeline.on_finished(engine, render)
    assert drafted(engine) == []  # still queued
    engine.db().execute(
        "UPDATE jobs SET status = 'done', finished_at = ? WHERE id = ?", (time.time(), render.id)
    )
    pipeline.on_finished(engine, engine.runner.get(render.id))
    assert len(drafted(engine)) == 2


def test_trim_keeps_the_best_across_channels(engine):
    today_store.put_day(engine.db(), Day(day="2026-11-03", drafts_cap=3))
    saved = a_campaign(engine, rate_per_1000=2.0)
    paid = add_channel(engine, accounts(engine, 2, 0), name="Paid", campaign_id=saved.id)
    pid, _ = watched(engine, paid, add_check(engine, paid), [60, 50])
    repo.update_project(engine.db(), pid, campaign_id=saved.id, rights_basis="clipping_programme")
    drafts.settle(engine, pid)
    assert len(drafted(engine)) == 2  # under the cap: nothing trimmed

    plain = add_channel(engine, [add_account(engine, "tiktok", f"p{n}") for n in range(3)], name="Plain")
    pid, clips = watched(engine, plain, add_check(engine, plain), [90, 80, 70])
    drafts.settle(engine, pid)
    kept = drafted(engine)
    assert len(kept) == 3
    mine = {c.id for c in clips}
    assert [p.clip_id for p in kept if p.clip_id in mine] == [clips[0].id]  # US$2.00 each beats none
    trimmed = store.list_posts(engine.db(), status=["discarded"])
    assert sorted(p.clip_id for p in trimmed) == sorted([clips[1].id, clips[2].id])
    assert {p.note for p in trimmed} == {"Trimmed to keep today inside 15 minutes"}
    assert all(p.state["trimmed"] for p in trimmed)

    back = drafts.restore(engine, [p.id for p in trimmed])
    assert {p.status for p in back} == {"draft"}
    assert drafts.trim(engine, "2026-11-03") == []  # what you brought back stays
    assert len(drafted(engine)) == 5
