"""What needs you now (SPEC 14.10 item 1, 10.11): one need per kind and thing, each with its title, what it
could earn or protects, whether it is due today and how many of its things each campaign has."""

from dataclasses import replace
from datetime import date

import pytest

from clipforge import repo
from clipforge.briefs import store as briefs
from clipforge.briefs.models import BriefSource
from clipforge.campaigns import store as campaigns
from clipforge.campaigns import tracker
from clipforge.post import store
from clipforge.today.models import Need
from clipforge.today.needs import needs
from clipforge.watch import drafts
from tests.briefing import add_brief
from tests.fakes import Clock, FakePublisher, fake_engine
from tests.posting import add_account, add_campaign, add_clip, at, custom, history, linked_clip, posted
from tests.watching import add_channel, add_check, watched

TODAY = date(2026, 11, 3)


@pytest.fixture
def clock():
    return Clock(at(8, 0))  # in the first posting window, so posts can go up now


def engine_with(clock, **settings):
    publishers = {
        "tiktok:browser": FakePublisher(),
        "instagram:api": FakePublisher(platform="instagram", method="api"),
    }
    return fake_engine(now=clock, publishers=publishers, **settings)


@pytest.fixture
def engine(clock):
    eng = engine_with(clock)
    yield eng
    eng.close()


def of(engine, kind: str, today: date = TODAY) -> dict[str, Need]:
    """The needs of one kind by ref, each with its ids sorted so they compare."""
    return {n.ref: replace(n, ids=sorted(n.ids)) for n in needs(engine, today) if n.kind == kind}


def need(
    kind, ref, ids, title, *, detail=None, value=0.0, currency="USD", guess=False, due=False, campaigns=None
):
    return Need(kind, ref, sorted(ids), title, detail, value, currency, guess, due, campaigns or {})


def status(engine, post, value: str):
    return store.update_post(engine.db(), post.id, status=value)


def test_drafts_need_per_channel(engine):
    podcasts_account = add_account(engine, "tiktok", "pods", daily_cap=2)
    gaming_account = add_account(engine, "tiktok", "games", daily_cap=1)
    podcasts = add_channel(engine, [podcasts_account], "Podcasts")
    gaming = add_channel(engine, [gaming_account], "Gaming")
    pods, _ = watched(engine, podcasts, add_check(engine, podcasts), [90, 60])
    games, _ = watched(engine, gaming, add_check(engine, gaming), [80])
    drafts.settle(engine, pods)
    drafts.settle(engine, games)
    beast = add_campaign(engine, rate_per_1000=2.0, hand_rules=["No watermark"])
    repo.update_project(engine.db(), pods, campaign_id=beast.id, rights_basis="clipping_programme")
    made = {p.account_id: [] for p in drafts.live_drafts(engine)}
    for p in drafts.live_drafts(engine):
        made[p.account_id].append(p.id)

    assert of(engine, "drafts") == {
        podcasts.id: need(
            "drafts",
            podcasts.id,
            made[podcasts_account.id],
            "Approve 2 drafts (Podcasts)",
            detail="2 with rules to check by hand",
            value=4.0,  # the starting guess of 1,000 views at US$2 a 1,000, twice
            guess=True,
            campaigns={beast.id: 2},
        ),
        gaming.id: need("drafts", gaming.id, made[gaming_account.id], "Approve 1 draft (Gaming)"),
    }


def test_drafts_share_the_budget_left(engine):
    account = add_account(engine, "tiktok", "pods", daily_cap=3)
    podcasts = add_channel(engine, [account], "Podcasts")
    pods, _ = watched(engine, podcasts, add_check(engine, podcasts), [90, 60, 30])
    drafts.settle(engine, pods)
    beast = add_campaign(engine, rate_per_1000=2.0, budget_left=5.0)
    repo.update_project(engine.db(), pods, campaign_id=beast.id, rights_basis="clipping_programme")
    (got,) = of(engine, "drafts").values()
    assert len(got.ids) == 3
    assert got.value == 5.0  # US$2 each would make US$6, and only US$5 is left


def test_needs_you_and_login(engine):
    beast = add_campaign(engine, rate_per_1000=2.0)
    late = add_account(engine, "tiktok", "late")
    reels = add_account(engine, "instagram", "reels")
    stuck = [
        status(engine, custom(engine, add_clip(engine, n), add_account(engine), at(19)), "needs_you")
        for n in (1, 2)
    ]
    waiting = custom(engine, linked_clip(engine, beast, 3), late, at(20))
    store.update_account(engine.db(), late.id, status="needs_login")
    store.update_account(engine.db(), reels.id, status="token_expiring")

    assert of(engine, "needs_you") == {
        "all": need("needs_you", "all", [p.id for p in stuck], "2 posts need you", due=True)
    }
    assert of(engine, "login") == {
        late.id: need(
            "login",
            late.id,
            [late.id],
            "Log in again: @late on TikTok",
            value=2.0,
            guess=True,
            due=True,
            campaigns={beast.id: 1},
        ),
        reels.id: need("login", reels.id, [reels.id], "Renew the token: @reels on Instagram", due=True),
    }
    status(engine, stuck[1], "scheduled")
    assert of(engine, "needs_you")["all"].title == "1 post needs you"
    assert waiting.id not in of(engine, "needs_you")["all"].ids


def test_insights_permission_need(engine, clock):
    beast = add_campaign(engine, rate_per_1000=2.0)
    reels = add_account(engine, "instagram", "reels", daily_cap=2)
    read = posted(engine, linked_clip(engine, beast), reels)
    clock.set(at(19))  # one account's posts go up hours apart
    blind = posted(engine, linked_clip(engine, beast, 2), reels, insights="no_permission")
    assert read.id != blind.id
    assert of(engine, "insights") == {
        reels.id: need(
            "insights",
            reels.id,
            [reels.id],
            "Give Instagram the insights permission: @reels",
            value=2.0,
            guess=True,
            campaigns={beast.id: 1},
        )
    }


def test_submit_need_per_campaign(engine):
    beast = add_campaign(engine, rate_per_1000=1.0, ends_on="2026-11-04")
    drama = add_campaign(engine, name="Drama", marketplace="Vyro")
    live = [posted(engine, linked_clip(engine, beast, n), add_account(engine)) for n in (1, 2)]
    sent, left = (posted(engine, linked_clip(engine, drama, n), add_account(engine)) for n in (3, 4))
    tracker.move_submission(engine, campaigns.submission_of_post(engine.db(), sent.id).id, "submitted")
    gone = add_campaign(engine, name="Over", status="ended")
    posted(engine, linked_clip(engine, gone, 5), add_account(engine))

    assert of(engine, "submit") == {
        beast.id: need(
            "submit",
            beast.id,
            [p.id for p in live],
            "Submit 2 live posts to Whop (Beast clips)",
            value=2.0,
            guess=True,
            due=True,  # it ends tomorrow
            campaigns={beast.id: 2},
        ),
        drama.id: need(
            "submit", drama.id, [left.id], "Submit 1 live post to Vyro (Drama)", campaigns={drama.id: 1}
        ),
    }


def test_views_need_uses_views_due(engine, clock):
    beast = add_campaign(engine, rate_per_1000=2.0)
    clock.set(at(8, day=1))
    old = posted(engine, linked_clip(engine, beast, 1), add_account(engine, "tiktok", "a"))
    read = posted(engine, linked_clip(engine, beast, 2), add_account(engine, "instagram", "b"))
    clock.set(at(8, day=2))
    young = posted(engine, linked_clip(engine, beast, 3), add_account(engine, "tiktok", "c"))
    clock.set(at(8, day=3))
    found = of(engine, "views")
    assert found == {
        "tiktok": need(
            "views",
            "tiktok",
            [old.id],
            "Enter TikTok views for 1 post",
            value=2.0,
            guess=True,
            due=True,
            campaigns={beast.id: 1},
        )
    }
    assert read.id not in found["tiktok"].ids and young.id not in found["tiktok"].ids
    assert of(engine, "views", date(2026, 11, 4))["tiktok"].title == "Enter TikTok views for 2 posts"


def test_decide_waits_a_day(engine, clock):
    beast = add_campaign(engine, rate_per_1000=1.0)
    first, second = (posted(engine, linked_clip(engine, beast, n), add_account(engine)) for n in (1, 2))
    for p in (first, second):
        tracker.add_views(engine, p.id, 3000)
    tracker.move_submission(engine, campaigns.submission_of_post(engine.db(), first.id).id, "submitted")
    clock.advance(hours=23)
    tracker.move_submission(engine, campaigns.submission_of_post(engine.db(), second.id).id, "submitted")
    clock.advance(hours=2)
    assert of(engine, "decide", date(2026, 11, 4)) == {
        "whop": need(
            "decide", "whop", [first.id], "Record 1 Whop decision", value=3.0, campaigns={beast.id: 1}
        )
    }
    clock.advance(hours=22)
    assert of(engine, "decide", date(2026, 11, 5))["whop"].title == "Record 2 Whop decisions"


def test_ends_tomorrow(engine):
    beast = add_campaign(engine, rate_per_1000=1.0, ends_on="2026-11-04")
    add_campaign(engine, name="Later", ends_on="2026-11-10")
    add_campaign(engine, name="Done", ends_on="2026-11-04", status="ended")
    last = add_campaign(engine, name="Last day", ends_on="2026-11-03")
    won = posted(engine, linked_clip(engine, beast, 1), add_account(engine))
    tracker.add_views(engine, won.id, 3000)
    sub = campaigns.submission_of_post(engine.db(), won.id)
    tracker.move_submission(engine, sub.id, "submitted")
    tracker.move_submission(engine, sub.id, "approved")
    posted(engine, linked_clip(engine, beast, 2), add_account(engine))  # still to submit

    assert of(engine, "ends") == {
        beast.id: need(
            "ends",
            beast.id,
            [beast.id],
            "Beast clips ends tomorrow",
            value=4.0,  # US$3 pending and a guessed US$1 to submit
            guess=True,
            due=True,
            campaigns={beast.id: 1},
        ),
        last.id: need("ends", last.id, [last.id], "Last day ends today", due=True, campaigns={last.id: 1}),
    }


def test_payout_when_ready(clock):
    engine = engine_with(clock, marketplaces={"Whop": {"min_payout": 5.0}})
    try:
        beast = add_campaign(engine, rate_per_1000=2.0)

        def approve(rank: int, views: int) -> None:
            p = posted(engine, linked_clip(engine, beast, rank), add_account(engine))
            tracker.add_views(engine, p.id, views)
            sub = campaigns.submission_of_post(engine.db(), p.id)
            tracker.move_submission(engine, sub.id, "submitted")
            tracker.move_submission(engine, sub.id, "approved")

        approve(1, 2000)
        assert of(engine, "payout") == {}  # US$4 is under Whop's US$5 minimum
        approve(2, 1000)
        assert of(engine, "payout") == {
            "Whop:USD": need(
                "payout",
                "Whop:USD",
                ["Whop:USD"],
                "Payout ready on Whop",
                detail="US$6.00 approved",
                value=6.0,
                campaigns={beast.id: 2},
            )
        }
    finally:
        engine.close()


def test_exported_posts(engine):
    out = [
        status(engine, custom(engine, add_clip(engine, n), add_account(engine), at(19)), "exported")
        for n in (1, 2)
    ]
    assert of(engine, "exported") == {
        "all": need("exported", "all", [p.id for p in out], "Put up 2 exported posts")
    }


def test_no_needs_when_all_is_done(engine):
    assert needs(engine, TODAY) == []
    account = add_account(engine)
    custom(engine, add_clip(engine, 1), account, at(19))  # scheduled, and nothing to do about it
    posted(engine, add_clip(engine, 2), account)  # live, with no campaign to submit to
    assert needs(engine, TODAY) == []


def test_needs_count_per_campaign(clock):
    engine = engine_with(clock)
    try:
        us = add_campaign(engine, rate_per_1000=1.0)
        au = add_campaign(engine, name="Aussie", rate_per_1000=10.0, currency="AUD")
        clips = [linked_clip(engine, us, 1), linked_clip(engine, us, 2), linked_clip(engine, au, 3)]
        clips.append(add_clip(engine, 4))
        for n, clip in enumerate(clips):
            status(engine, custom(engine, clip, add_account(engine), at(19, 10 * n)), "needs_you")
        (found,) = of(engine, "needs_you").values()
        assert found.campaigns == {us.id: 2, au.id: 1}
        # A$10 against US$2: with no rate the A$ is taken as if US$, so the need is in A$
        assert (found.value, found.currency, found.guess) == (12.0, "AUD", True)
        engine.update_settings(
            engine.settings.model_copy(
                update={"today": engine.settings.today.model_copy(update={"rates": {"AUD": 0.65}})}
            )
        )
        (found,) = of(engine, "needs_you").values()
        assert (found.value, found.currency) == (13.08, "AUD")  # 10 + 2 / 0.65
    finally:
        engine.close()


def test_an_ended_campaign_adds_nothing_before_a_post_is_live(engine):
    """A post not yet live on a campaign that has ended may not be paid, so it is counted and worth
    nothing."""
    over = add_campaign(engine, rate_per_1000=2.0)
    stuck = status(
        engine, custom(engine, linked_clip(engine, over), add_account(engine), at(19)), "needs_you"
    )
    campaigns.update_campaign(engine.db(), over.id, status="ended")
    assert of(engine, "needs_you") == {
        "all": need("needs_you", "all", [stuck.id], "1 post needs you", due=True, campaigns={over.id: 1})
    }


def ran(engine, source_id: str, result: str, error: str | None = None, *, days_ago: int = 0):
    when = engine.now().timestamp() - days_ago * 86400
    briefs.update_source(engine.db(), source_id, last_run_at=when, last_result=result, error=error)


def test_brief_item_counts_the_top_ten_fitting(engine):
    gamer = add_account(engine, "tiktok", "gamer", niche_tags=["gaming"])
    history(engine, gamer, [2000] * 5)  # 2,000 views a post, 2 posts a day: 4 times the rate a day
    fitting = [add_brief(engine, niche="Gaming", rate_per_1000=float(r)) for r in (1, 2)]
    fitting += [add_brief(engine, niche="Gaming") for _ in range(3)]  # no rate: they count with nothing
    for r in range(50, 55):
        add_brief(engine, rate_per_1000=float(r))  # no niche, so nothing fits them
    add_brief(engine, niche="Gaming", rate_per_1000=90.0, status="seen")  # looked at already
    title = "Look at 5 new briefs (about US$12.00 a day at your views)"
    assert of(engine, "briefs") == {"new": need("briefs", "new", [b.id for b in fitting], title, value=12.0)}
    richer = [add_brief(engine, niche="Gaming", rate_per_1000=float(r)) for r in range(3, 23)]
    top = richer[-10:]  # rates 13 to 22
    title = "Look at 10 new briefs (about US$700.00 a day at your views)"
    assert of(engine, "briefs") == {"new": need("briefs", "new", [b.id for b in top], title, value=700.0)}


def test_brief_item_keeps_a_place_for_reposting_briefs(engine):
    gamer = add_account(engine, "tiktok", "gamer", niche_tags=["gaming"])
    history(engine, gamer, [2000] * 5)  # 4 times the rate a day
    richer = [add_brief(engine, niche="Gaming", rate_per_1000=float(r)) for r in range(3, 13)]
    repost = add_brief(engine, niche="Gaming", title="Edit Reposts", rate_per_1000=1.0)
    # the reposting brief goes in ahead of the ten richer ones, so the poorest of them drops out
    title = "Look at 10 new briefs, 1 of them reposting (about US$292.00 a day at your views)"
    ids = [b.id for b in richer[1:]] + [repost.id]
    assert of(engine, "briefs") == {"new": need("briefs", "new", ids, title, value=292.0)}


def test_brief_item_says_a_guess(engine):
    sports = add_brief(engine, niche="Sports", rate_per_1000=2.0)  # no account: a suggested niche
    title = "Look at 1 new brief (about US$2.00 a day, a guess)"
    assert of(engine, "briefs") == {"new": need("briefs", "new", [sports.id], title, value=2.0, guess=True)}


def test_no_fitting_brief_no_item(engine):
    fan = add_account(engine, "tiktok", "fan", niche_tags=["nba"])
    store.update_account(engine.db(), fan.id, paused=True)
    add_channel(engine, [fan])  # Sports is covered, so it isn't suggested, and its account is paused
    add_brief(engine, niche="Sports", rate_per_1000=2.0)
    add_brief(engine, rate_per_1000=3.0)  # no niche
    assert of(engine, "briefs") == {}


def test_failed_source_asks_for_a_paste(engine):
    site = BriefSource(
        id="bs_site", name="Clip Site", marketplace="Clip Site", list_url="https://clips.example/c",
        reader="general", created_at=1.0, updated_at=1.0,
    )  # fmt: skip
    briefs.add_source(engine.db(), site)
    briefs.add_source(
        engine.db(), site.model_copy(update={"id": "bs_off", "list_url": "https://b.example/c"})
    )
    ran(engine, "bs_site", "blocked", "This site turned ClipForge away today.")
    ran(engine, "bs_off", "sign_in", "This site wants a sign-in.")
    briefs.update_source(engine.db(), "bs_off", enabled=False)
    ran(engine, "bs_scout", "ok")
    assert of(engine, "source") == {
        "bs_site": need(
            "source", "bs_site", ["bs_site"], "Paste briefs from Clip Site",
            detail="This site turned ClipForge away today.",
        )
    }  # fmt: skip
    ran(engine, "bs_site", "blocked", "This site turned ClipForge away today.", days_ago=1)
    assert of(engine, "source") == {}  # it tries again this morning


MISSING_NEXT = (
    "Open HeyClicky so its Campaign Scout writes the file, or turn off Use Campaign Scout (HeyClicky) in "
    "Settings."
)
AGAIN_NEXT = (
    "ClipForge tries it again tomorrow morning. To stop, turn off Use Campaign Scout (HeyClicky) in Settings."
)


@pytest.mark.parametrize(
    ("result", "error", "title", "detail"),
    [
        ("changed", "missing", "HeyClicky's campaign file is missing", MISSING_NEXT),
        ("changed", "unreadable", "HeyClicky's campaign file couldn't be read", AGAIN_NEXT),
        ("changed", "reshaped", "HeyClicky's campaign file has changed shape", AGAIN_NEXT),
        (
            "error",
            "This source couldn't be read today.",
            "HeyClicky's campaign file couldn't be read",
            AGAIN_NEXT,
        ),
    ],
)
def test_scout_problem_names_the_file_and_what_to_do(engine, result, error, title, detail):
    ran(engine, "bs_scout", result, error)
    assert of(engine, "source") == {
        "bs_scout": need("source", "bs_scout", ["bs_scout"], title, detail=detail)
    }
