import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from clipforge import repo
from clipforge.campaigns import store as campaigns
from clipforge.campaigns.models import Campaign, Required
from clipforge.campaigns.rules import hand_key
from clipforge.models import ClipRow, ProjectOptions, RenderRow
from clipforge.post import keychain, store
from clipforge.post.models import Account, AccountSettings, PlanRequest, Post, PostEdit, PostOptions
from clipforge.post.plan import EVERY_ACK, build_plan
from clipforge.post.service import post_from_plan
from clipforge.render.qa import Check, QAResult
from clipforge.trends.models import PostSound
from tests.fakes import fake_engine
from tests.unit.test_audio import edl_for

SYD = ZoneInfo("Australia/Sydney")
NOW = datetime(2026, 11, 3, 6, 0, tzinfo=SYD)  # a Tuesday, before the first window
PACK = {
    "tiktok_caption": "TikTok words",
    "tiktok_hashtags": ["#money"],
    "instagram_caption": "Instagram words",
    "instagram_hashtags": ["#money"],
    "credit": "Source: Lex Fridman",
}
GOOD = [Check(name="codec", ok=True, hard=True)]


@pytest.fixture
def engine():
    eng = fake_engine()
    yield eng
    eng.close()


def add_clip(
    engine,
    rank=1,
    *,
    length=30.0,
    rendered=True,
    checks=GOOD,
    rights="own",
    video_id=None,
    tags=(),
    edl=None,
):
    conn = engine.db()
    project = repo.create_project(
        conn,
        source_kind="youtube",
        source_url="https://www.youtube.com/watch?v=arj7oStGLkU",
        video_id=video_id or repo.new_id("v"),
        options=ProjectOptions(),
        focus_s=None,
        prompt=None,
        title="A talk",
    )
    repo.update_project(conn, project.id, rights_basis=rights)
    row = ClipRow(
        id=repo.new_id("c"),
        project_id=project.id,
        rank=rank,
        start_s=10.0,
        end_s=10.0 + length,
        sentence_ids=["S1"],
        topic_tags=list(tags),
        packaging=PACK,
        status="ready",
    )
    repo.replace_ideas(conn, project.id, [row])
    if rendered:
        render = RenderRow(
            id=repo.new_id("r"),
            clip_id=row.id,
            version=1,
            style_key="default",
            path=str(engine.paths.home / "clip.mp4"),
            duration_s=length,
            size_bytes=1_000_000,
            qa=QAResult(checks=list(checks)).model_dump(),
        )
        if edl is not None:  # the edit list the render was made from
            render.edl_path = str(engine.paths.home / f"{row.id}.edl.json")
            Path(render.edl_path).write_text(edl.model_dump_json())
        repo.add_render(conn, render)
        repo.update_clip(conn, row.id, current_render_id=render.id)
    return repo.get_clip(conn, row.id)


def add_account(engine, platform="tiktok", handle=None, *, status="connected", **settings):
    paused = settings.pop("paused", False)
    a = Account(
        id=repo.new_id("a"),
        platform=platform,
        method="browser" if platform == "tiktok" else "api",
        handle=handle or repo.new_id("h"),
        status=status,
        paused=paused,
        settings=AccountSettings(**settings),
        created_at=time.time(),
    )
    return store.add_account(engine.db(), a)


def add_post(engine, account, at, *, video_id="arj7oStGLkU", span=(10.0, 40.0), status="scheduled"):
    conn = engine.db()
    batch = store.add_batch(conn, repo.new_id("x"), "spread", "smart", {})
    p = Post(
        id=repo.new_id("t"),
        batch_id=batch,
        clip_id=None,
        clip_title="Earlier",
        render_id=None,
        account_id=account.id,
        platform=account.platform,
        method=account.method,
        caption="",
        hashtags=[],
        options=PostOptions(),
        scheduled_at=at.timestamp(),
        status=status,
        video_key=video_id,
        start_s=span[0],
        end_s=span[1],
        created_at=time.time(),
        updated_at=time.time(),
    )
    store.add_posts(conn, [p])
    return p


def plan_for(engine, clips, accounts, plan_id="plan1", **req):
    request = PlanRequest(clip_ids=[c.id for c in clips], account_ids=[a.id for a in accounts], **req)
    return build_plan(engine, request, plan_id=plan_id, now=NOW)


def codes(plan, key=None):
    return [w.code for w in plan.warnings if key is None or w.key == key]


def warning(plan, code):
    (w,) = [w for w in plan.warnings if w.code == code]
    return w


def sydney(t: float) -> datetime:
    return datetime.fromtimestamp(t, SYD)


def in_windows(t: float) -> bool:
    clock = sydney(t).time()
    return any(
        datetime.strptime(a, "%H:%M").time() <= clock < datetime.strptime(b, "%H:%M").time()
        for a, b in (("07:00", "09:00"), ("12:00", "13:00"), ("18:00", "22:00"))
    )


# Modes


def test_every_needs_the_acknowledgement(engine):
    assert EVERY_ACK == (
        "Posting the same clip to several accounts on one platform is what TikTok's spam rule describes"
        " and can get them restricted together"
    )
    c = add_clip(engine)
    accounts = [add_account(engine), add_account(engine)]
    plan = plan_for(engine, [c], accounts, mode="every")
    assert len(plan.posts) == 2 and plan.blocked
    w = warning(plan, "every_ack")
    assert EVERY_ACK in w.sentence and w.blocking and w.key is None
    ok = plan_for(engine, [c], accounts, mode="every", acknowledged_every=True)
    assert not ok.blocked
    assert not warning(ok, "every_ack").blocking  # still shown, no longer in the way
    one_each = plan_for(engine, [c], [accounts[0], add_account(engine, "instagram")], mode="every")
    assert "every_ack" not in codes(one_each) and not one_each.blocked


def test_spread_plans_one_post_per_platform_with_its_copy(engine):
    c = add_clip(engine)
    t = add_account(engine, cta="Follow for more", hashtags=["clips"])
    i = add_account(engine, "instagram")
    plan = plan_for(engine, [c], [t, i])
    by_platform = {p.platform: p for p in plan.posts}
    assert by_platform["tiktok"].caption == "TikTok words\n\nFollow for more"
    assert by_platform["tiktok"].hashtags == ["money", "clips"]
    assert by_platform["tiktok"].key == f"{c.id}:{t.id}"
    assert by_platform["instagram"].caption == "Instagram words"  # an own video gets no credit
    assert not plan.blocked and plan.renders == [] and plan.plan_id == "plan1"


def test_music_credit_from_the_render_ends_the_caption(engine):
    music = {"item_id": "l1", "title": "Night Drive", "artist": "Me", "db": -12.0}
    credited = edl_for([(10.0, 40.0)]).model_copy(
        update={"music": {**music, "credit": "Music: Night Drive by Me"}}
    )
    t = add_account(engine)
    (post,) = plan_for(engine, [add_clip(engine, edl=credited)], [t]).posts
    assert post.caption == "TikTok words\n\nMusic: Night Drive by Me"
    # a licence that asks for no credit, and a render with no music, add nothing
    quiet = edl_for([(10.0, 40.0)]).model_copy(update={"music": {**music, "credit": None}})
    for edl in (quiet, edl_for([(10.0, 40.0)])):
        (post,) = plan_for(engine, [add_clip(engine, edl=edl)], [t]).posts
        assert post.caption == "TikTok words"


# Already posted, rights and originality


def test_duplicate_warns_in_the_plan(engine):
    c = add_clip(engine, video_id="arj7oStGLkU")
    mine, other = add_account(engine, handle="mine"), add_account(engine, handle="other")
    add_post(engine, other, NOW - timedelta(days=3), status="posted", span=(12.0, 40.0))
    plan = plan_for(engine, [c], [mine])
    w = warning(plan, "duplicate")
    assert not w.blocking and "@other" in w.sentence and EVERY_ACK in w.sentence
    assert w.key == plan.posts[0].key and plan.posts[0].warnings == ["duplicate"]
    assert not plan.blocked


def test_the_same_moment_on_the_same_account_is_said(engine):
    """A moment already in the Queue for an account holds its plan; one already up there is said."""
    queued, posted = add_account(engine, handle="queued"), add_account(engine, handle="posted")
    add_post(engine, queued, NOW + timedelta(hours=5), span=(12.0, 40.0))
    add_post(engine, posted, NOW - timedelta(days=3), status="posted", span=(12.0, 40.0))
    plan = plan_for(engine, [add_clip(engine, video_id="arj7oStGLkU")], [queued])
    w = warning(plan, "repeat")
    assert w.blocking and plan.blocked and w.key == plan.posts[0].key
    assert (
        w.sentence
        == "This moment is already in the Queue for @queued. Change or cancel that post there instead."
    )
    plan = plan_for(engine, [add_clip(engine, video_id="arj7oStGLkU")], [posted])
    w = warning(plan, "repeat")
    assert not w.blocking and not plan.blocked
    assert (
        w.sentence
        == "This moment already went up on @posted. Posting it there again can look like repeated content."
    )
    other = plan_for(engine, [add_clip(engine, video_id="other_video")], [queued])
    assert "repeat" not in codes(other)


def test_rights_and_light_edits_warn_only(engine):
    light = Check(
        name="originality", ok=False, hard=False, message="Light on original edits: add a fact card."
    )
    c = add_clip(engine, rights="not_cleared", checks=[*GOOD, light])
    plan = plan_for(engine, [c], [add_account(engine)])
    assert set(codes(plan)) == {"rights_not_cleared", "light_edits"}
    assert warning(plan, "light_edits").sentence == "Light on original edits: add a fact card."
    assert "Not cleared" in warning(plan, "rights_not_cleared").sentence
    assert not plan.blocked and not any(w.blocking for w in plan.warnings)


def test_rights_warning_said_once_for_the_whole_plan(engine):
    """The rights warning is about the video, not one post, so the plan says it once."""
    clips = [add_clip(engine, 1, rights="not_cleared"), add_clip(engine, 2, rights="not_cleared")]
    plan = plan_for(engine, clips, [add_account(engine), add_account(engine, "instagram")])
    assert len(plan.posts) == 4  # each clip on each platform
    (w,) = [w for w in plan.warnings if w.code == "rights_not_cleared"]
    assert w.key is None and not w.blocking and not plan.blocked
    assert not any("rights_not_cleared" in p.warnings for p in plan.posts)
    cleared = plan_for(engine, [add_clip(engine, 3)], [add_account(engine)])
    assert "rights_not_cleared" not in codes(cleared)


def test_unrendered_clip_renders_first(engine):
    c = add_clip(engine, rendered=False)
    plan = plan_for(engine, [c], [add_account(engine), add_account(engine, "instagram")])
    assert plan.renders == [c.id]
    assert codes(plan) == ["renders_first", "renders_first"]
    assert not plan.blocked and len(plan.posts) == 2


def test_qa_failure_blocks_that_platform_only(engine):
    long = Check(
        name="file_size",
        ok=False,
        hard=True,
        platform="instagram",
        message="The video is 412 MB, over Instagram's 300 MB.",
    )
    c = add_clip(engine, checks=[*GOOD, long])
    t, i = add_account(engine), add_account(engine, "instagram")
    plan = plan_for(engine, [c], [t, i])
    w = warning(plan, "qa_failed")
    assert w.blocking and w.key == f"{c.id}:{i.id}" and "412 MB" in w.sentence
    assert codes(plan, f"{c.id}:{t.id}") == []
    assert plan.blocked


# Limits


def test_caption_limits_warn_and_block(engine):
    c = add_clip(engine)
    t, i = add_account(engine), add_account(engine, "instagram")
    tk, ik = f"{c.id}:{t.id}", f"{c.id}:{i.id}"

    def caption_plan(tiktok, instagram, ig_tags=()):
        edits = {
            tk: PostEdit(caption=tiktok, hashtags=[]),
            ik: PostEdit(caption=instagram, hashtags=list(ig_tags)),
        }
        return plan_for(engine, [c], [t, i], edits=edits)

    fits = caption_plan("a" * 4000, "a" * 2200)
    assert codes(fits) == [] and not fits.blocked
    over = caption_plan("a" * 4001, "a" * 2201)
    assert [(w.code, w.key, w.blocking) for w in over.warnings] == [
        ("caption_too_long", tk, True),
        ("caption_too_long", ik, True),
    ]
    assert "4001" in over.warnings[0].sentence and "2200" in over.warnings[1].sentence
    arabic = "مرحبا بكم"  # 9 characters, right to left
    assert codes(caption_plan("a", arabic * 244 + "abcd")) == []  # 2200
    assert codes(caption_plan("a", arabic * 244 + "abcde")) == ["caption_too_long"]
    assert codes(caption_plan("a", "😀" * 1100)) == []  # an emoji counts 2, as the apps count it
    assert codes(caption_plan("a", "😀" * 1100 + "a")) == ["caption_too_long"]
    tags30 = [f"tag{k}" for k in range(30)]
    assert codes(caption_plan("a", "hi", tags30)) == []
    many = caption_plan("a", "hi", [*tags30, "tag30"])
    assert codes(many) == ["too_many_hashtags"] and many.blocked
    assert codes(caption_plan("a", "hi #in a#word", tags30[:29])) == []  # a # inside a word isn't one
    assert codes(caption_plan("a", "hi #inside", tags30)) == ["too_many_hashtags"]
    cleaned = caption_plan("tab\there\x07 bell\x1b", "fine")
    assert next(p.caption for p in cleaned.posts if p.platform == "tiktok") == "tab here bell"


def test_clip_length_limits_block(engine):
    t, i = add_account(engine), add_account(engine, "instagram")
    short, long = add_clip(engine, length=2.5), add_clip(engine, length=601.0)
    plan = plan_for(engine, [short, long], [t, i], mode="every")
    assert [(w.code, w.key) for w in plan.warnings if w.blocking] == [
        ("too_short", f"{short.id}:{i.id}"),
        ("too_long", f"{long.id}:{t.id}"),
    ]
    huge = add_clip(engine, length=901.0)
    assert codes(plan_for(engine, [huge], [i])) == ["too_long"]


def test_creator_rewards_short_clip_warns(engine):
    rewards = add_account(engine, handle="money", creator_rewards=True)
    short, long = add_clip(engine, length=30.0), add_clip(engine, rank=2, length=75.0)
    w = warning(plan_for(engine, [short], [rewards]), "creator_rewards_short")
    assert not w.blocking and "@money" in w.sentence and "60 seconds" in w.sentence
    assert codes(plan_for(engine, [long], [rewards])) == []
    plain = add_account(engine)
    plan = plan_for(engine, [short, long], [rewards, plain])
    assert {(p.clip_id, p.account_id) for p in plan.posts} == {(short.id, plain.id), (long.id, rewards.id)}
    assert codes(plan) == []


def linked_clip(engine, *, length=30.0, **rules):
    now = time.time()
    saved = campaigns.add_campaign(
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
    clip = add_clip(engine, length=length, rights="clipping_programme")
    repo.update_project(engine.db(), clip.project_id, campaign_id=saved.id)
    return clip


def test_linked_clip_has_no_creator_rewards_warning(engine):
    rewards, plain = add_account(engine, handle="money", creator_rewards=True), add_account(engine)
    clip = linked_clip(engine, length=30.0)
    assert codes(plan_for(engine, [clip], [rewards])) == []
    (post,) = plan_for(engine, [clip], [rewards, plain]).posts
    assert post.account_id == rewards.id  # the campaign's lengths win over Creator Rewards


def test_linked_post_has_disclosure_on(engine):
    t = add_account(engine)
    clip = linked_clip(engine, required=Required(hashtags=["Beast"], text=["Link in bio"]))
    (post,) = plan_for(engine, [clip], [t]).posts
    assert post.options.disclosure
    assert post.hashtags == ["Beast", "money"] and post.caption.endswith("Link in bio")
    (plain,) = plan_for(engine, [add_clip(engine)], [t]).posts
    assert not plain.options.disclosure and plain.hashtags == ["money"]
    off = plan_for(engine, [clip], [t], platform_options={"tiktok": PostOptions(disclosure=False)})
    assert not off.posts[0].options.disclosure  # you turned it off yourself, which the rule checks


def draft_of(engine, clip, account):
    """The clip's draft for the account, as a clipping channel's check saves it."""
    conn = engine.db()
    (planned,) = plan_for(engine, [clip], [account], plan_id="draft1").posts
    post = post_from_plan(
        conn, store.add_batch(conn, "draft1", "spread", "smart", {}), planned, status="draft"
    )
    store.add_posts(conn, [post])
    return post


def approving(engine, draft, **req):
    return build_plan(engine, PlanRequest(draft_ids=[draft.id], **req), plan_id="plan2", now=NOW)


def test_broken_rule_blocks_naming_it(engine):
    t = add_account(engine)
    clip = linked_clip(engine, length=50.0, max_s=45)
    plan = plan_for(engine, [clip], [t])
    assert plan.blocked
    broken = warning(plan, "rule_broken")
    assert broken.blocking and broken.key == f"{clip.id}:{t.id}"
    assert "Beast clips" in broken.sentence and "At most 45 seconds" in broken.sentence
    assert "50 seconds" in broken.sentence
    assert plan.posts[0].warnings == ["rule_broken"]


def test_rules_are_checked_as_they_are_now(engine):
    t = add_account(engine)
    clip = linked_clip(engine, length=40.0, max_s=45)
    draft = draft_of(engine, clip, t)
    assert not approving(engine, draft).blocked
    campaign_id = repo.get_project(engine.db(), clip.project_id).campaign_id
    campaigns.update_campaign(engine.db(), campaign_id, max_s=35)
    later = approving(engine, draft)
    assert later.blocked and codes(later) == ["rule_broken"]


def test_off_campaign_draft_is_ordinary(engine):
    rewards = add_account(engine, handle="money", creator_rewards=True)
    clip = linked_clip(engine, length=50.0, max_s=45, hand_rules=["No reaction content"])
    draft = draft_of(engine, clip, rewards)
    key = f"{clip.id}:{rewards.id}"
    assert set(codes(approving(engine, draft))) == {"rule_broken", "rule_unchecked"}
    off = approving(engine, draft, edits={key: PostEdit(off_campaign=True)})
    assert not off.blocked
    assert codes(off) == ["creator_rewards_short"]  # an ordinary clip's note; the rights stay the project's
    assert off.posts[0].off_campaign
    store.update_post(engine.db(), draft.id, state={"off_campaign": True})  # kept on the draft
    assert codes(approving(engine, draft)) == ["creator_rewards_short"]
    back = approving(engine, draft, edits={key: PostEdit(off_campaign=False)})
    assert "rule_broken" in codes(back)


def test_tick_through_the_edit_unblocks(engine):
    t = add_account(engine)
    clip = linked_clip(engine, hand_rules=["No reaction content", "No watermark"])
    key = f"{clip.id}:{t.id}"
    first, second = hand_key("No reaction content"), hand_key("No watermark")
    plan = plan_for(engine, [clip], [t])
    assert plan.blocked and codes(plan) == ["rule_unchecked", "rule_unchecked"]
    assert "No reaction content" in plan.warnings[0].sentence and "No watermark" in plan.warnings[1].sentence
    half = plan_for(engine, [clip], [t], edits={key: PostEdit(rule_ticks=[first])})
    assert codes(half) == ["rule_unchecked"] and "No watermark" in half.warnings[0].sentence
    both = PostEdit(rule_ticks=[first, second])
    done = plan_for(engine, [clip], [t], edits={key: both})
    assert not done.blocked and done.posts[0].rule_ticks == [first, second]
    post = post_from_plan(engine.db(), "b1", done.posts[0])
    linked = engine.project(clip.project_id).campaign_id
    assert post.state == {"rule_ticks": [first, second], "campaign_id": linked}  # kept on the post
    draft = draft_of(engine, clip, t)
    store.update_post(engine.db(), draft.id, state={"rule_ticks": [first, second]})
    assert not approving(engine, draft).blocked  # the draft's own ticks


def test_accounts_needing_login_are_refused(engine):
    c = add_clip(engine)
    gone = add_account(engine, handle="gone", status="needs_login")
    broken = add_account(engine, "instagram", handle="broken", status="error")
    plan = plan_for(engine, [c], [gone, broken])
    assert [(w.code, w.blocking) for w in plan.warnings] == [("needs_login", True), ("needs_login", True)]
    assert "@gone" in plan.warnings[0].sentence and "log in" in plan.warnings[0].sentence
    assert plan.blocked


def test_paused_posting_and_accounts_warn(engine):
    c = add_clip(engine)
    resting = add_account(engine, handle="resting", paused=True)
    w = warning(plan_for(engine, [c], [resting]), "paused")
    assert not w.blocking and "@resting" in w.sentence
    engine.update_settings(
        engine.settings.model_copy(
            update={"posting": engine.settings.posting.model_copy(update={"paused": True})}
        )
    )
    plan = plan_for(engine, [c], [add_account(engine)])
    w = warning(plan, "paused")
    assert w.key is None and not w.blocking and not plan.blocked


def test_unknown_clips_and_accounts_are_refused(engine):
    c, a = add_clip(engine), add_account(engine)
    with pytest.raises(LookupError):
        plan_for(engine, [c.model_copy(update={"id": "cmissing"})], [a])
    store.delete_account(engine.db(), a.id)
    with pytest.raises(LookupError):
        plan_for(engine, [c], [a])


# Timing


def test_smart_slots_sit_in_windows_with_spacing(engine):
    clips = [add_clip(engine, rank=k) for k in range(1, 4)]
    a, b = add_account(engine), add_account(engine)
    plan = plan_for(engine, clips, [a, b])
    assert len(plan.posts) == 3 and not plan.blocked
    assert all(in_windows(p.scheduled_at) and p.scheduled_at >= NOW.timestamp() for p in plan.posts)
    first, second, third = plan.posts  # clips in rank order: a, b, a
    assert (first.account_id, second.account_id, third.account_id) == (a.id, b.id, a.id)
    assert abs(first.scheduled_at - second.scheduled_at) >= 15 * 60
    assert third.scheduled_at - first.scheduled_at >= 3 * 3600


def test_post_now_staggers_accounts_and_spaces_later_posts(engine):
    clips = [add_clip(engine, rank=k) for k in range(1, 4)]
    a, b = add_account(engine), add_account(engine)
    plan = plan_for(engine, clips, [a, b], timing="now")
    first, second, third = plan.posts
    assert first.scheduled_at == NOW.timestamp()
    assert 120 <= second.scheduled_at - first.scheduled_at <= 360
    assert third.account_id == a.id and third.scheduled_at - first.scheduled_at >= 3 * 3600
    assert in_windows(third.scheduled_at)
    assert not plan.blocked


def test_custom_start_and_interval(engine):
    clips = [add_clip(engine, rank=k) for k in range(1, 3)]
    t, i = add_account(engine), add_account(engine, "instagram")
    start = datetime(2026, 11, 3, 18, 0, tzinfo=SYD)
    plan = plan_for(
        engine, clips, [t, i], timing="custom", custom_start=start.timestamp(), custom_interval_min=30
    )
    assert [p.scheduled_at for p in plan.posts] == [start.timestamp() + k * 1800 for k in range(4)]
    assert [w.code for w in plan.warnings if w.blocking] == [
        "spacing",
        "cap",
    ]  # TikTok 3 h, Instagram 1 a day


def test_custom_slot_over_cap_offers_the_next_free_slot(engine):
    c = add_clip(engine)
    ig = add_account(engine, "instagram", handle="gram")
    add_post(engine, ig, datetime(2026, 11, 3, 7, 30, tzinfo=SYD))
    key = f"{c.id}:{ig.id}"
    at = datetime(2026, 11, 3, 19, 0, tzinfo=SYD).timestamp()
    plan = plan_for(engine, [c], [ig], timing="custom", custom_times={key: at})
    w = warning(plan, "cap")
    assert w.blocking and w.key == key and "@gram" in w.sentence
    assert w.move_to == datetime(2026, 11, 4, 7, 0, tzinfo=SYD).timestamp()
    assert plan.posts[0].scheduled_at == at and plan.blocked
    moved = plan_for(engine, [c], [ig], timing="custom", custom_times={key: w.move_to})
    assert codes(moved) == [] and not moved.blocked
    outside = datetime(2026, 11, 4, 3, 0, tzinfo=SYD).timestamp()
    w = warning(plan_for(engine, [c], [ig], timing="custom", custom_times={key: outside}), "outside")
    assert w.blocking and w.move_to == datetime(2026, 11, 4, 7, 0, tzinfo=SYD).timestamp()
    past = (NOW - timedelta(hours=1)).timestamp()
    w = warning(plan_for(engine, [c], [ig], timing="custom", custom_times={key: past}), "past")
    assert w.blocking and w.move_to == datetime(2026, 11, 4, 7, 0, tzinfo=SYD).timestamp()


def test_same_plan_id_same_slots(engine):
    clips = [add_clip(engine, rank=k) for k in range(1, 5)]
    accounts = [add_account(engine), add_account(engine), add_account(engine, "instagram")]
    times = [p.scheduled_at for p in plan_for(engine, clips, accounts, plan_id="same").posts]
    assert times == [p.scheduled_at for p in plan_for(engine, clips, accounts, plan_id="same").posts]
    assert times != [p.scheduled_at for p in plan_for(engine, clips, accounts, plan_id="other").posts]


def test_forty_clips_over_twelve_accounts_respect_caps(engine):
    clips = [add_clip(engine, rank=k) for k in range(1, 41)]
    tiktok = [add_account(engine) for _ in range(6)]
    instagram = [add_account(engine, "instagram") for _ in range(6)]
    plan = plan_for(engine, clips, [*tiktok, *instagram])
    assert len(plan.posts) == 80 and not plan.blocked and codes(plan) == []
    end = (NOW + timedelta(days=14)).timestamp()
    assert all(NOW.timestamp() <= p.scheduled_at < end and in_windows(p.scheduled_at) for p in plan.posts)
    caps = {"tiktok": 2, "instagram": 1}
    spacing = {"tiktok": 3 * 3600, "instagram": 6 * 3600}
    per_day = Counter((p.account_id, sydney(p.scheduled_at).date()) for p in plan.posts)
    platform_of = {p.account_id: p.platform for p in plan.posts}
    assert all(n <= caps[platform_of[aid]] for (aid, _), n in per_day.items())
    for platform in ("tiktok", "instagram"):
        mine = sorted((p.scheduled_at, p.account_id) for p in plan.posts if p.platform == platform)
        assert all(b[0] - a[0] >= 15 * 60 for a, b in zip(mine, mine[1:], strict=False))
        for aid in {aid for _, aid in mine}:
            times = [t for t, a in mine if a == aid]
            assert all(b - a >= spacing[platform] for a, b in zip(times, times[1:], strict=False))
    assert Counter(p.clip_id for p in plan.posts) == Counter({c.id: 2 for c in clips})


def test_smart_slots_avoid_posts_already_queued(engine):
    c = add_clip(engine)
    t = add_account(engine)
    other = add_account(engine)
    add_post(engine, t, datetime(2026, 11, 3, 7, 0, tzinfo=SYD))
    add_post(engine, t, datetime(2026, 11, 3, 12, 0, tzinfo=SYD))
    add_post(engine, other, datetime(2026, 11, 4, 7, 0, tzinfo=SYD))
    (p,) = plan_for(engine, [c], [t]).posts
    assert sydney(p.scheduled_at).date() == datetime(2026, 11, 4).date()  # today is full
    assert p.scheduled_at - datetime(2026, 11, 4, 7, 0, tzinfo=SYD).timestamp() >= 15 * 60


def test_plan_has_no_side_effects(engine, memory_keyring, monkeypatch):
    c = add_clip(engine, rendered=False, rights="not_cleared")
    accounts = [add_account(engine), add_account(engine, "instagram", status="needs_login")]
    memory_keyring.set_password(keychain.SERVICE, keychain.ref_for(accounts[1].id), "kept")

    def no_keychain(*args, **kwargs):
        raise AssertionError("the plan read the Keychain")

    monkeypatch.setattr(keychain, "get_token", no_keychain)
    conn = engine.db()

    def snapshot():
        tables = {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("posts", "batches", "jobs")
        }
        files = sorted(
            (str(p), p.stat().st_mtime_ns)
            for p in engine.paths.home.rglob("*")
            if p.is_file() and p.suffix != ".db"
        )
        return tables, files, dict(memory_keyring.items), conn.total_changes

    before = snapshot()
    plan = plan_for(engine, [c], accounts)
    assert plan.renders == [c.id] and plan.blocked
    assert snapshot() == before


# The Details step's "Every TikTok post"


def test_platform_options_go_on_every_post_there_and_a_posts_own_switch_wins(engine):
    c, d = add_clip(engine), add_clip(engine)
    t1, t2 = add_account(engine), add_account(engine, assisted=True)
    i = add_account(engine, "instagram")
    shared = {"tiktok": PostOptions.model_validate({"comments": False, "visibility": "only_me"})}
    own = {f"{d.id}:{t1.id}": PostEdit(options=PostOptions.model_validate({"comments": True}))}
    plan = plan_for(
        engine, [c, d], [t1, t2, i], mode="every", acknowledged_every=True, platform_options=shared, edits=own
    )
    options = {p.key: p.options for p in plan.posts}
    assert len(options) == 6
    for p in plan.posts:
        o = options[p.key]
        if p.platform == "instagram":
            assert o == PostOptions()  # TikTok's switches stay on TikTok
            continue
        assert o.visibility == "only_me" and o.duet is True  # only what the block changed
        assert o.comments is (p.key == f"{d.id}:{t1.id}")  # the post's own switch over the block's
        assert o.assisted is (p.account_id == t2.id)  # still the account's, whatever the block sends


def test_plan_refuses_sounds_an_account_cannot_take(engine):
    conn = engine.db()
    c = add_clip(engine)
    t = add_account(engine)
    a, mode_b, chrome = (add_account(engine, "instagram") for _ in range(3))
    store.update_account(conn, a.id, ig_mode="facebook")
    store.update_account(conn, mode_b.id, ig_mode="instagram")
    store.update_account(conn, chrome.id, method="browser")
    ig_sound = PostSound(source="ig_trending", external_id="a1", title="Song a1")
    tiktok_sound = PostSound(source="shortlist", external_id="7234567890123456789", title="Original Sound")

    def with_sound(account, sound):
        own = {f"{c.id}:{account.id}": PostEdit(options=PostOptions(sound=sound))}
        return plan_for(engine, [c], [account], edits=own)

    fine = with_sound(a, ig_sound)
    assert codes(fine) == [] and fine.posts[0].options.sound == ig_sound
    for account in (mode_b, chrome):
        w = warning(with_sound(account, ig_sound), "sound_route")
        assert w.blocking and w.sentence == "Instagram only adds sounds to accounts linked through Facebook."
    w = warning(with_sound(a, tiktok_sound), "sound_route")
    assert w.blocking and w.sentence == (
        "A TikTok sound can't go on Instagram. Pick one of Instagram's trending sounds, or none."
    )
    w = warning(with_sound(t, ig_sound), "sound_route")
    assert w.blocking and w.sentence == (
        "An Instagram sound can't go on TikTok. Pick one from your TikTok shortlist, or none."
    )
    # a platform block's sound is left out, as its assisted is: a sound is picked per post
    shared = {"instagram": PostOptions(sound=ig_sound)}
    assert plan_for(engine, [c], [a], platform_options=shared).posts[0].options.sound is None


def test_tiktok_sound_makes_the_post_assisted(engine):
    c, t = add_clip(engine), add_account(engine)
    sound = PostSound(source="shortlist", external_id="7234567890123456789", title="Night Drive")
    key = f"{c.id}:{t.id}"

    def with_options(**options):
        return plan_for(engine, [c], [t], edits={key: PostEdit(options=PostOptions(**options))})

    plan = with_options(sound=sound)
    (p,) = plan.posts
    assert p.options.assisted and p.options.sound == sound
    w = warning(plan, "sound_by_hand")
    assert not w.blocking and not plan.blocked
    assert w.sentence == "You add this sound yourself in TikTok's window: Night Drive."
    assert with_options(sound=sound, assisted=False).posts[0].options.assisted  # the sound needs you there
    assert not plan_for(engine, [c], [t]).posts[0].options.assisted
    assert "sound_by_hand" not in codes(plan_for(engine, [c], [t]))


def test_ended_campaign_warns_its_drafts(engine):
    t = add_account(engine)
    clip = linked_clip(engine, ends_on="2026-11-02")  # the day before NOW
    plan = plan_for(engine, [clip], [t])
    ended = warning(plan, "campaign_ended")
    assert ended.sentence == "Beast clips ended on 2 Nov. This post may not be paid."
    assert not ended.blocking and not plan.blocked
    draft = draft_of(engine, clip, t)
    later = approving(engine, draft)
    assert (
        warning(later, "campaign_ended").sentence == "Beast clips ended on 2 Nov. This draft may not be paid."
    )
    assert not later.blocked
    unlinked = approving(engine, draft, edits={f"{clip.id}:{t.id}": PostEdit(off_campaign=True)})
    assert "campaign_ended" not in codes(unlinked)
