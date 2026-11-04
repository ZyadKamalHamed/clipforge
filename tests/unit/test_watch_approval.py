"""Approving a clipping channel's drafts (SPEC 10.9): the review step plans exactly the drafts ticked, keeps
each one's slot while it is still free, and Confirm turns them into scheduled posts in one batch. Then
Discard and its Undo, and drafts whose account or clip went away."""

import itertools
from collections import Counter
from datetime import UTC, timedelta

import pytest

from clipforge import repo
from clipforge.post import queue, store
from clipforge.post.errors import PlanChanged
from clipforge.post.models import PlanRequest, PostEdit, PostOptions
from clipforge.post.plan import at_text, who
from clipforge.post.slots import next_free, rules_for, sydney_day
from clipforge.watch import drafts
from clipforge.watch import store as watch_store
from tests.fakes import Clock, FakePublisher, fake_engine
from tests.posting import add_account, add_clip, add_render, at, custom, jobs, run, sydney
from tests.watching import add_channel, add_check, watched

GONE = "This draft was already approved or removed."


@pytest.fixture
def clock():
    return Clock(at(7, 10))


@pytest.fixture
def tiktok():
    return FakePublisher()


@pytest.fixture
def instagram():
    return FakePublisher(platform="instagram", method="api")


@pytest.fixture
def engine(clock, tiktok, instagram):
    eng = fake_engine(now=clock, publishers={"tiktok:browser": tiktok, "instagram:api": instagram})
    yield eng
    eng.close()


def two(engine):
    return [
        add_account(engine, "tiktok", "t0", daily_cap=1),
        add_account(engine, "instagram", "i0", daily_cap=1),
    ]


def pair(engine):
    """A TikTok and an Instagram account, each with a draft of the better of two clips: the drafts (TikTok's
    first), the clips, the accounts and the project."""
    accounts = two(engine)
    ch = add_channel(engine, accounts)
    pid, clips = watched(engine, ch, add_check(engine, ch), [90, 60])
    drafts.settle(engine, pid)
    made = sorted(drafts.live_drafts(engine), key=lambda p: p.platform != "tiktok")
    return made, clips, accounts, pid


def review(*posts, **req) -> PlanRequest:
    return PlanRequest(draft_ids=[p.id for p in posts], **req)


def approve(engine, *posts, **req):
    """The review step on ``posts``, then Confirm, as Approve and post does: the plan, the batch and the
    posts."""
    r = review(*posts, **req)
    plan = engine.posting.plan(r)
    assert not plan.blocked, [w.sentence for w in plan.warnings if w.blocking]
    bid, approved = engine.posting.confirm(r, plan.plan_id)
    return plan, bid, approved


def key(post) -> str:
    return f"{post.clip_id}:{post.account_id}"


def moved(plan):
    return [w for w in plan.warnings if w.code == "slot_moved"]


def test_approve_makes_scheduled_posts_in_one_batch(engine):
    made, _, _, _ = pair(engine)
    before = {p.id: p for p in made}
    plan, bid, approved = approve(engine, *made)
    assert {p.id for p in approved} == set(before)  # the drafts themselves, not copies
    assert {p.status for p in approved} == {"scheduled"}
    assert {p.batch_id for p in approved} == {bid} != {made[0].batch_id}
    assert all(p.scheduled_at == before[p.id].scheduled_at for p in approved)  # still free, so kept
    assert moved(plan) == []
    assert all(len(jobs(engine, p.id)) == 1 for p in approved)
    assert len(store.list_posts(engine.db())) == 2
    assert drafts.live_drafts(engine) == []


def test_approve_twice_makes_one_batch(engine):
    made, _, _, _ = pair(engine)
    r = review(*made)
    first, second = engine.posting.plan(r), engine.posting.plan(r)  # two windows
    bid, approved = engine.posting.confirm(r, first.plan_id)
    assert engine.posting.confirm(r, first.plan_id) == (bid, approved)
    with pytest.raises(LookupError) as err:
        engine.posting.confirm(r, second.plan_id)
    assert str(err.value) == GONE
    assert store.batch_for_plan(engine.db(), second.plan_id) is None
    assert len(store.list_posts(engine.db())) == 2


def test_approving_some_elsewhere_changes_the_plan(engine):
    made, _, _, _ = pair(engine)
    shown = engine.posting.plan(review(*made))
    approve(engine, made[0])  # from the other window
    with pytest.raises(PlanChanged) as err:
        engine.posting.confirm(review(*made), shown.plan_id)
    assert [p.key for p in err.value.plan.posts] == [key(made[1])]
    assert store.get_post(engine.db(), made[1].id).status == "draft"


def test_passed_slot_moves_with_a_note(engine, clock):
    made, _, accounts, _ = pair(engine)
    draft, account = made[0], accounts[0]
    clock.set(at(7, 10, day=5))
    rules = rules_for(engine.settings.posting, account)
    new = next_free(rules, after=clock().astimezone(UTC), mine=[], platform=[])
    plan, _, (approved,) = approve(engine, draft)
    (note,) = moved(plan)
    old = at_text(sydney(draft.scheduled_at), rules.tz)
    assert (
        note.sentence
        == f"{who(account)}'s slot at {old} has passed, so it moves to {at_text(new, rules.tz)}."
    )
    assert (note.key, note.blocking) == (key(draft), False)
    assert approved.scheduled_at == new.timestamp()


def test_taken_slot_moves_too(engine):
    made, _, accounts, _ = pair(engine)
    draft, account = made[1], accounts[1]  # Instagram's, one a day
    taken = custom(engine, add_clip(engine), account, sydney(draft.scheduled_at) + timedelta(minutes=5))
    rules = rules_for(engine.settings.posting, account)
    after = engine.now().astimezone(UTC)
    new = next_free(rules, after=after, mine=[sydney(taken.scheduled_at)], platform=[])
    plan, _, (approved,) = approve(engine, draft)
    (note,) = moved(plan)
    old = at_text(sydney(draft.scheduled_at), rules.tz)
    assert note.sentence == (
        f"{who(account)}'s slot at {old} is no longer free, so it moves to {at_text(new, rules.tz)}."
    )
    assert approved.scheduled_at == new.timestamp()


def test_edited_caption_in_review_wins(engine):
    made, _, _, _ = pair(engine)
    tt, ig = made
    assert engine.posting.edit(tt.id, caption="My words").status == "draft"
    engine.posting.edit(ig.id, hashtags=["#mine"])
    _, _, approved = approve(engine, tt, ig, edits={key(ig): PostEdit(caption="Review words")})
    by = {p.platform: p for p in approved}
    assert (by["tiktok"].caption, by["tiktok"].hashtags) == ("My words", tt.hashtags)
    assert (by["instagram"].caption, by["instagram"].hashtags) == ("Review words", ["mine"])


def test_review_switches_reach_the_drafts(engine):
    made, _, _, _ = pair(engine)
    _, _, approved = approve(engine, *made, platform_options={"tiktok": PostOptions(disclosure=True)})
    by = {p.platform: p for p in approved}
    assert (by["tiktok"].options.disclosure, by["instagram"].options.disclosure) == (True, False)


def test_approval_keeps_the_rule_state(engine):
    made, _, _, _ = pair(engine)
    tt, ig = made
    for post in made:
        state = {"rule_ticks": ["hand:0"], "off_campaign": True, "other": 1}
        store.update_post(engine.db(), post.id, state=state)
    _, _, approved = approve(engine, tt, ig, edits={key(tt): PostEdit(off_campaign=False)})
    by = {p.platform: p for p in approved}
    assert by["tiktok"].state == {"other": 1, "rule_ticks": ["hand:0"]}  # linked again, its tick kept
    assert by["instagram"].state == {"other": 1, "rule_ticks": ["hand:0"], "off_campaign": True}


def test_assisted_comes_from_the_account_at_approval(engine):
    made, _, accounts, _ = pair(engine)
    assert made[0].options.assisted is False
    settings = accounts[0].settings.model_copy(update={"assisted": True})
    store.update_account(engine.db(), accounts[0].id, settings=settings)
    _, _, (approved,) = approve(engine, made[0])
    assert approved.options.assisted is True


def test_pile_of_drafts_spreads_over_days(engine, clock):
    accounts = [
        *(add_account(engine, "tiktok", f"t{n}", daily_cap=2) for n in range(6)),
        *(add_account(engine, "instagram", f"i{n}", daily_cap=1) for n in range(6)),
    ]
    ch = add_channel(engine, accounts)
    for day in range(1, 5):
        clock.set(at(7, 10, day=day))
        pid, _ = watched(engine, ch, add_check(engine, ch), [90, 80, 70, 60, 50])
        drafts.settle(engine, pid)
    pile = drafts.live_drafts(engine)
    assert len(pile) == 40

    clock.set(at(7, 10, day=5))
    now = clock().timestamp()
    plan, _, approved = approve(engine, *pile)
    assert len(approved) == 40
    assert all(p.scheduled_at >= now for p in approved)
    passed = {key(p) for p in pile if p.scheduled_at < now}
    assert passed and passed <= {w.key for w in moved(plan)}
    for account in accounts:
        rules = rules_for(engine.settings.posting, account)
        times = sorted(p.scheduled_at for p in approved if p.account_id == account.id)
        days = Counter(sydney_day(sydney(t), rules.tz) for t in times)
        assert max(days.values(), default=0) <= rules.cap, (account.handle, days)
        assert all(b - a >= rules.spacing.total_seconds() for a, b in itertools.pairwise(times)), (
            account.handle
        )


def test_draft_of_deleted_account_is_discarded(engine):
    made, _, _, _ = pair(engine)
    gone, kept = made
    store.delete_account(engine.db(), gone.account_id)
    plan = engine.posting.plan(review(*made))
    assert [p.key for p in plan.posts] == [key(kept)]  # the review step leaves it out
    assert [p.id for p in drafts.live_drafts(engine)] == [kept.id]
    left = store.get_post(engine.db(), gone.id)
    assert (left.status, left.note) == ("discarded", "Its account was removed, so the draft was discarded.")


def test_draft_of_deleted_project_is_discarded(engine):
    _, _, _, pid = pair(engine)
    engine.delete_project(pid)
    assert drafts.live_drafts(engine) == []
    assert {(p.status, p.note) for p in store.list_posts(engine.db())} == {
        ("discarded", "Its clip was deleted, so the draft was discarded.")
    }


def test_discarded_clip_hides_its_draft(engine):
    _, clips, _, _ = pair(engine)
    engine.discard_clip(clips[0].id)
    assert drafts.live_drafts(engine) == []
    assert {p.status for p in store.list_posts(engine.db())} == {"draft"}  # the clip's Undo brings them back
    engine.restore_clip(clips[0].id)
    assert len(drafts.live_drafts(engine)) == 2


def test_rerendered_clip_updates_the_draft(engine, clock, tiktok):
    made, clips, _, _ = pair(engine)
    again = add_render(engine, repo.get_clip(engine.db(), clips[0].id), version=2)
    assert {p.render_id for p in drafts.live_drafts(engine)} == {again.id}
    _, _, (approved,) = approve(engine, made[0])
    assert approved.render_id == again.id
    clock.set(sydney(approved.scheduled_at))
    _, done = run(engine, approved.id)
    assert done.status == "posted"
    assert [p.render_id for p in tiktok.published] == [again.id]


def test_restore_after_approve_refuses(engine):
    made, _, _, _ = pair(engine)
    ids = [p.id for p in made]
    assert {p.status for p in drafts.discard(engine, ids)} == {"discarded"}
    assert drafts.live_drafts(engine) == []
    assert {p.status for p in drafts.restore(engine, ids)} == {"draft"}
    assert [p.id for p in drafts.restore(engine, ids)] == ids  # Undo twice is still one Undo
    approve(engine, made[0])
    with pytest.raises(LookupError) as err:
        drafts.restore(engine, [made[0].id])
    assert str(err.value) == GONE
    with pytest.raises(LookupError) as err:
        drafts.discard(engine, ids)
    assert str(err.value) == GONE
    assert store.get_post(engine.db(), made[1].id).status == "draft"  # all or nothing


def test_scheduler_never_uploads_a_draft(engine, clock, tiktok, instagram):
    made, _, _, _ = pair(engine)
    clock.set(sydney(max(p.scheduled_at for p in made)) + timedelta(days=2))
    engine.start_posting()
    for _ in range(3):
        queue.tick(engine)
        assert engine.runner.run_until_idle(timeout_s=5)
    assert (tiktok.published, instagram.published) == ([], [])
    assert {p.status for p in store.list_posts(engine.db())} == {"draft"}
    assert all(engine.runner.jobs_for(p.id) == [] for p in made)


def test_deleting_the_project_it_waits_for_makes_the_drafts(engine):
    ch = add_channel(engine, two(engine))
    check = add_check(engine, ch)
    ready, _ = watched(engine, ch, check, [90])
    running, _ = watched(engine, ch, check, [80], status="running")
    drafts.settle(engine, ready)
    assert drafts.live_drafts(engine) == []
    engine.delete_project(running)
    assert len(drafts.live_drafts(engine)) == 2
    assert sorted(i.status for i in watch_store.items_of_check(engine.db(), check.id)) == [
        "drafted",
        "failed",
    ]
