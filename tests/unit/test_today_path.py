"""The path from nothing to your first payout on Today: six levels of steps, each done once ClipForge sees it
done or you tick it, and done for good after that. A step joins the list when the steps before it are done,
worth what the account it leads to could earn a day; one another item already stands for is left to that
item. The first account is a niche one when one niche holds 40% of the money in the live briefs, else a
broad one."""

import json

import pytest

from clipforge import service
from clipforge.post import store
from clipforge.today import day, path
from clipforge.today.models import Need
from clipforge.today.needs import needs
from tests.briefing import add_brief
from tests.fakes import Clock, FakePublisher, fake_engine
from tests.posting import add_account, add_campaign, add_clip, at, campaign_post, linked_clip, posted_post

OPENING = ["notify", "service", "make_account"]


@pytest.fixture
def clock():
    return Clock(at(8, 0))


def guided(clock, **settings):
    publishers = {
        "tiktok:browser": FakePublisher(),
        "instagram:api": FakePublisher(platform="instagram", method="api"),
    }
    today = {"guide": True} | settings.pop("today", {})
    return fake_engine(now=clock, publishers=publishers, today=today, **settings)


@pytest.fixture
def engine(clock):
    eng = guided(clock)
    yield eng
    eng.close()


def today(engine):
    return day.today_of(engine)


def kinds(engine) -> set[str]:
    return {n.kind for n in needs(engine, today(engine))}


def steps_of(engine) -> dict[str, str]:
    """Each step's state by its id."""
    return {s.id: s.state for s in path.steps(engine, kinds(engine))}


def listed(engine) -> dict[str, object]:
    """The path's needs by step."""
    return {n.ref: n for n in needs(engine, today(engine)) if n.kind == "path"}


def test_a_fresh_home_starts_on_the_first_three_steps(engine):
    found = listed(engine)
    assert sorted(found) == sorted(OPENING)
    assert found["make_account"].title == "Make a broad clips account on TikTok, like Late Night Clips"
    assert "Late Night Clips" in found["make_account"].detail
    states = steps_of(engine)
    assert states["link_account"] == "locked" and states["first_payout"] == "locked"
    assert [lv.name for lv in path.LEVELS] == [
        "Set up",
        "Your first account",
        "Your first campaign",
        "Your first post",
        "Your first money",
        "Your daily machine",
    ]


def test_every_step_has_a_level_a_title_and_steps_before_it_that_exist(engine):
    ids = [s.id for s in path.STEPS]
    assert len(ids) == len(set(ids))
    for step in path.STEPS:
        assert 1 <= step.level <= len(path.LEVELS) and step.title and step.detail
        assert set(step.after) <= set(ids[: ids.index(step.id)])  # only ever on steps listed before it


def test_one_niche_with_most_of_the_money_gets_a_niche_account(engine):
    for n in range(4):
        add_brief(engine, niche="Gaming", rate_per_1000=3.0, title=f"G{n}")
    add_brief(engine, niche="Music", rate_per_1000=1.0)
    first = path.plan(engine)
    assert (first.broad, first.niche) == (False, "Gaming")
    assert listed(engine)["make_account"].title == "Make a gaming clips account on TikTok"
    assert "Gaming TV Clips" in listed(engine)["make_account"].detail
    assert first.tags == ["gaming", "games", "gamer", "fortnite", "minecraft", "valorant", "roblox"]


def test_money_spread_over_many_niches_gets_a_broad_account(engine):
    for niche in ("Gaming", "Music", "Tech", "Sports", "Comedy and entertainment"):
        add_brief(engine, niche=niche, rate_per_1000=2.0)
    first = path.plan(engine)
    assert first.broad and first.niche is None
    # one tag for each niche, the richest first, never news and politics on TikTok, at most 10
    assert set(first.tags) == {"gaming", "music", "tech", "sports", "comedy"}
    assert len(first.tags) <= 10


def test_news_never_makes_the_first_account_a_news_one(engine):
    for n in range(5):
        add_brief(engine, niche="News and politics", rate_per_1000=9.0, title=f"N{n}")
    add_brief(engine, niche="Gaming", rate_per_1000=1.0)
    first = path.plan(engine)
    assert first.niche != "News and politics" and "news" not in first.tags


def test_the_steps_on_the_way_to_money_are_worth_what_the_account_could_earn_a_day(engine):
    add_brief(engine, niche="Gaming", rate_per_1000=3.0, title="best")  # 1,000 views a post at the guess
    add_brief(engine, niche="Gaming", rate_per_1000=2.0, title="next")
    add_brief(engine, niche="Gaming", rate_per_1000=1.0, title="third")  # beyond TikTok's 2 posts a day
    found = listed(engine)
    assert (found["make_account"].value, found["make_account"].currency) == (5.0, "USD")
    assert found["make_account"].guess
    assert found["notify"].value == 0.0  # it earns nothing itself


def test_seen_steps_are_done_for_good_and_the_next_ones_join(engine):
    account = add_account(engine, "tiktok", "late")
    states = steps_of(engine)
    assert states["make_account"] == states["link_account"] == "done"
    assert states["check_account"] == states["tag_account"] == "todo"
    store.delete_account(engine.db(), account.id)  # gone later, still done: the path never goes back
    assert steps_of(engine)["link_account"] == "done"
    assert path.done_how(engine)["link_account"] == "app"


def test_the_background_service_is_seen_by_its_file(engine):
    assert steps_of(engine)["service"] == "todo"
    service.AGENTS.mkdir(parents=True)
    service.plist_path(service.AGENTS).write_bytes(b"")
    assert steps_of(engine)["service"] == "done"


def test_a_tick_finishes_a_step_clipforge_cannot_see(engine):
    items = day.refresh(engine)
    (notify,) = [i for i in items if i.kind == "path" and i.ref == "notify"]
    day.tick(engine, notify.id)
    assert path.done_how(engine)["notify"] == "hand"
    assert "notify" not in listed(engine)
    assert [i.done for i in day.refresh(engine) if i.ref == "notify"] == [True]


def test_a_step_another_item_stands_for_is_left_to_it(engine):
    add_account(engine, "tiktok", "late", niche_tags=["gaming"])
    brief = add_brief(engine, niche="Gaming", rate_per_1000=2.0)
    add_campaign(engine, brief_id=brief.id)  # not joined yet: the join item asks for it
    states = steps_of(engine)
    assert states["pick_campaign"] == "done"
    assert states["join_campaign"] == "listed"
    assert "join_campaign" not in listed(engine)
    assert [n.kind for n in needs(engine, today(engine))].count("join") == 1


def test_a_step_waiting_on_the_world_says_so(engine):
    account = add_account(engine, "tiktok", "late", niche_tags=["gaming"])
    campaign = add_campaign(engine, joined_at=1.0)
    campaign_post(engine, campaign, account, days_ago=0.1)  # submitted, too soon for a decision
    states = steps_of(engine)
    assert states["submit_post"] == "done"
    assert states["first_approved"] == "waiting"


def test_money_made_finishes_the_money_level(engine):
    account = add_account(engine, "tiktok", "late", niche_tags=["gaming"])
    campaign = add_campaign(engine, joined_at=1.0)
    campaign_post(engine, campaign, account, days_ago=9, counts=[(7, 5000)], status="paid", paid=12.0)
    states = steps_of(engine)
    assert states["first_approved"] == states["first_payout"] == "done"


def test_new_briefs_wait_until_the_first_post_is_up(engine):
    add_account(engine, "tiktok", "late", niche_tags=["gaming"])
    add_brief(engine, niche="Gaming", rate_per_1000=2.0)
    assert "briefs" not in {n.kind for n in needs(engine, today(engine))}  # Pick your first campaign is it
    path.record(engine, "first_post", "app")
    assert "briefs" in {n.kind for n in needs(engine, today(engine))}


def test_each_step_keeps_its_own_estimate_and_the_rest_past_15_minutes_are_optional(engine, clock):
    items = {i.ref: i for i in day.refresh(engine) if i.kind == "path"}
    assert items["make_account"].estimate_s == 600
    assert items["notify"].estimate_s == 60
    assert not any(i.optional for i in items.values())  # 13 minutes in all
    add_account(engine, "tiktok", "late")
    path.record(engine, "notify", "hand")
    path.record(engine, "service", "hand")
    later = {i.ref: i for i in day.refresh(engine) if i.kind == "path" and not i.done}
    assert set(later) == {"check_account", "tag_account", "instagram"}
    # the first three took 13 of today's 15 minutes, so only Check still fits
    assert sorted(ref for ref, i in later.items() if i.optional) == ["instagram", "tag_account"]
    clock.advance(days=1)
    tomorrow = {i.ref: i for i in day.refresh(engine) if i.kind == "path" and not i.done}
    # a fresh 15 minutes: Instagram, the slowest and worth no more, waits for a longer day
    assert [ref for ref, i in tomorrow.items() if i.optional] == ["instagram"]


def test_the_view_has_the_level_the_progress_and_the_numbers(engine):
    found = path.view(engine, kinds(engine))
    assert found["on"] and found["level"] == 1
    assert (found["done"], found["total"]) == (0, len(path.STEPS))
    assert found["levels"][0]["name"] == "Set up"
    first = found["levels"][0]["steps"][0]
    assert set(first) == {"id", "title", "detail", "state", "done_how", "to"}
    assert found["metrics"] == {"accounts": 0, "posts": 0, "earned": 0.0, "currency": "USD"}
    assert found["account"]["names"][0] == "Late Night Clips"
    add_account(engine, "tiktok", "late")
    path.record(engine, "notify", "hand")
    path.record(engine, "service", "app")
    found = path.view(engine, kinds(engine))
    assert found["level"] == 2 and found["done"] == 4
    assert found["metrics"]["accounts"] == 1


def test_a_path_walked_to_the_end_leaves_the_list_and_says_so(engine):
    for step in path.STEPS:
        path.record(engine, step.id, "hand")
    assert listed(engine) == {}
    found = path.view(engine, kinds(engine))
    assert found["level"] is None and found["done"] == found["total"]


def test_with_the_guide_off_there_is_no_path(clock):
    eng = guided(clock, today={"guide": False})
    try:
        assert listed(eng) == {}
        assert path.view(eng, kinds(eng)) == {"on": False}
    finally:
        eng.close()


def test_a_step_done_once_is_recorded_once(engine):
    path.record(engine, "notify", "hand")
    path.record(engine, "notify", "app")
    assert path.done_how(engine) == {"notify": "hand"}
    with pytest.raises(LookupError):
        path.record(engine, "no_such_step", "hand")


def test_fake_engines_leave_the_guide_off_unless_asked(clock):
    eng = fake_engine(now=clock)
    try:
        assert eng.settings.today.guide is False
    finally:
        eng.close()


def checked(engine, account, status: str) -> None:
    """A Check of ``account`` run to the end, finding it ``status``."""
    job_id = engine.runner.enqueue("account.selftest", account.id, unique=False)
    engine.db().execute(
        "UPDATE jobs SET status = 'done', result_json = ? WHERE id = ?",
        (json.dumps({"status": status}), job_id),
    )


def test_check_account_waits_for_a_check_that_passed(engine):
    account = add_account(engine, "tiktok", "late")
    store.update_account(engine.db(), account.id, last_checked_at=1.0)  # a login stamps it too
    assert steps_of(engine)["check_account"] == "todo"
    checked(engine, account, "error")
    assert steps_of(engine)["check_account"] == "todo"
    checked(engine, account, "connected")
    assert steps_of(engine)["check_account"] == "done"


def test_the_first_account_stays_as_planned_once_it_is_made(engine):
    for niche in ("Gaming", "Music", "Tech", "Sports", "Comedy and entertainment"):
        add_brief(engine, niche=niche, rate_per_1000=2.0)
    made = path.plan(engine)
    assert made.broad
    add_account(engine, "tiktok", "late")
    assert steps_of(engine)["make_account"] == "done"
    for n in range(8):
        add_brief(engine, niche="Gaming", rate_per_1000=3.0, title=f"G{n}")  # gaming has most now
    later = path.plan(engine)
    assert (later.broad, later.names, later.bio, later.tags) == (True, made.names, made.bio, made.tags)
    assert later.second == "Gaming"  # the account to add next still follows the briefs


def test_clips_and_a_post_count_only_once_they_are_for_a_campaign(engine):
    account = add_account(engine, "tiktok", "late")
    add_clip(engine)  # a project with no campaign picked in its header
    posted_post(engine, account, days_ago=1)  # live, but for no campaign
    steps_of(engine)
    done = path.done_how(engine)
    assert "first_clips" not in done and "first_post" not in done
    campaign = add_campaign(engine, joined_at=1.0)
    linked_clip(engine, campaign)
    campaign_post(engine, campaign, account, days_ago=0.1, status="to_submit")
    steps_of(engine)
    assert {"first_clips", "first_post"} <= set(path.done_how(engine))


def test_the_post_step_says_to_press_tiktoks_schedule(engine):
    (first_post,) = [s for s in path.STEPS if s.id == "first_post"]
    assert "Schedule" in first_post.detail and "once it's live" in first_post.detail


def test_money_items_come_before_the_paths_steps_and_keep_their_place_in_the_15_minutes(engine, monkeypatch):
    found = [
        Need("submit", "Whop", ["s1"], "Submit 1 live post to Whop", value=1.0),
        Need(
            "path", "second_account", ["second_account"], "Make a second account", value=6.0, estimate_s=600
        ),
        Need("path", "instagram", ["instagram"], "Make an Instagram account", value=6.0, estimate_s=900),
    ]
    monkeypatch.setattr(day, "needs", lambda engine, today: list(found))
    items = day.refresh(engine)
    assert [i.ref for i in items] == ["Whop", "second_account", "instagram"]
    assert [i.ref for i in items if i.optional] == ["instagram"]


def test_turning_the_guide_off_takes_its_open_steps_off_the_list(engine):
    items = day.refresh(engine)
    (notify,) = [i for i in items if i.kind == "path" and i.ref == "notify"]
    day.tick(engine, notify.id)
    engine.update_settings(
        engine.settings.model_copy(
            update={"today": engine.settings.today.model_copy(update={"guide": False})}
        )
    )
    after = [i for i in day.refresh(engine) if i.kind == "path"]
    assert [(i.ref, i.done_how) for i in after] == [("notify", "hand")]  # the one you did stays done


def test_the_first_campaign_to_pick_is_a_reposting_one(engine):
    add_brief(engine, niche="Gaming", brand="Kick", rate_per_1000=3.0)
    add_brief(engine, niche="Gaming", brand="Henny Hermes", title="Edit Reposts", rate_per_1000=1.0)
    found = path.view(engine, kinds(engine))
    pick = next(s for lv in found["levels"] for s in lv["steps"] if s["id"] == "pick_campaign")
    assert pick["title"] == "Pick your first campaign: Henny Hermes"
    assert "reposting campaigns come first" in pick["detail"]
