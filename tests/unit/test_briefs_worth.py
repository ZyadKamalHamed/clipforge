"""What a brief is worth (SPEC 10.11): for each account that fits it, a post's estimate on the brief's rate
for that account's platform, times the account's daily cap, held to the budget left, the best account
winning; with none fitting, one post at the starting guess on its best rate; nothing known without a rate,
and nothing once it is gone. And the brief links ClipForge may open: https, on a marketplace it reads."""

from urllib.parse import quote

import pytest

from clipforge.briefs import advice, store, worth
from clipforge.briefs.models import BriefSource
from clipforge.briefs.worth import Worth
from clipforge.campaigns import links
from clipforge.errors import ClipForgeError
from clipforge.post import store as post_store
from clipforge.today.day import today_of
from tests.briefing import add_brief
from tests.fakes import Clock, fake_engine
from tests.posting import add_account, at, history
from tests.server import api_client

WEEKLY = [500, 3000, 4000, 4000, 800, 5000]  # 4 of 6 reach 1,000, and their median is 4,000


@pytest.fixture
def engine():
    eng = fake_engine(now=Clock(at(9, 0)))
    yield eng
    eng.close()


def test_worth_of_the_best_fitting_account(engine):
    gamer = add_account(engine, "tiktok", "gamer", niche_tags=["gaming"], daily_cap=3)
    history(engine, gamer, WEEKLY)
    newbie = add_account(engine, "tiktok", "newbie", niche_tags=["minecraft"], daily_cap=3)
    add_account(engine, "tiktok", "cook", niche_tags=["food"], daily_cap=3)  # another niche
    add_account(engine, "instagram", "reels", niche_tags=["gaming"])  # a platform the brief doesn't pay
    paused = add_account(engine, "tiktok", "resting", niche_tags=["gaming"], daily_cap=3)
    post_store.update_account(engine.db(), paused.id, paused=True)
    logged_out = add_account(engine, "tiktok", "away", niche_tags=["gaming"], daily_cap=3)
    post_store.update_account(engine.db(), logged_out.id, status="needs_login")
    brief = add_brief(engine, niche="Gaming", rate_per_1000=1.5, min_views=1000, platforms=["tiktok"])

    assert [a.id for a in worth.fits(engine, brief)] == [gamer.id, newbie.id]
    # 4/6 x 4,000 / 1,000 x 1.5 = 4.00 a post, 3 a day; the newbie's guess is 1,000 views, 4.50 a day
    assert worth.worth(engine, brief) == Worth(12.0, "USD", False, gamer.id)


def test_worth_per_platform_rate(engine):
    tiktok = add_account(engine, "tiktok", "tt", niche_tags=["gaming"], daily_cap=2)
    reels = add_account(engine, "instagram", "ig", niche_tags=["gaming"], daily_cap=2)
    brief = add_brief(
        engine,
        niche="gaming",
        rate_per_1000=1.5,
        rates={"tiktok": 0.5, "instagram": 1.5},
        max_payout=5.0,
        caps={"tiktok": 0.4},
    )
    assert (worth.rate_for(brief, "tiktok"), worth.rate_for(brief, "instagram")) == (0.5, 1.5)
    assert (worth.cap_for(brief, "tiktok"), worth.cap_for(brief, "instagram")) == (0.4, 5.0)
    stand_in = worth.stand_in(brief, "tiktok")
    assert (stand_in.rate_per_1000, stand_in.max_payout, stand_in.platforms) == (0.5, 0.4, ["tiktok"])
    assert store.get_brief(engine.db(), brief.id) == brief  # the stand-in is never saved
    assert worth.niche_tags(engine, "GAMING") >= {"gaming", "minecraft"}
    assert worth.niche_tags(engine, "Knitting") == set()
    assert [a.id for a in worth.fits(engine, brief)] == [tiktok.id, reels.id]
    # TikTok: 1,000 views at 0.5, held to 0.40 a clip, twice; Instagram: 1.50 twice
    assert worth.worth(engine, brief) == Worth(3.0, "USD", True, reels.id)


def test_worth_caps_at_budget_left(engine):
    gamer = add_account(engine, "tiktok", "gamer", niche_tags=["gaming"], daily_cap=3)
    history(engine, gamer, WEEKLY)
    brief = add_brief(engine, niche="Gaming", rate_per_1000=1.5, min_views=1000, budget_left=5.0)
    assert worth.worth(engine, brief) == Worth(5.0, "USD", False, gamer.id)  # 12.00 a day, 5.00 left
    capped = add_brief(engine, niche="Gaming", rate_per_1000=1.5, min_views=1000, max_payout=3.0)
    assert worth.worth(engine, capped).amount == 9.0  # 3.00 a clip at most, 3 a day


def test_worth_without_a_fit_is_a_guess(engine):
    brief = add_brief(engine, niche="Gaming", rate_per_1000=1.0)
    assert worth.fits(engine, brief) == []
    assert worth.worth(engine, brief) == Worth(1.0, "USD", True, None)  # 1,000 views at 1.0, one post
    add_account(engine, "tiktok", "gamer", niche_tags=["gaming"])
    no_niche = add_brief(engine, rate_per_1000=2.0, rates={"tiktok": 0.5, "instagram": 2.0})
    assert worth.fits(engine, no_niche) == []  # a brief with no niche fits nobody
    assert worth.worth(engine, no_niche) == Worth(2.0, "USD", True, None)  # on the best rate
    beyond = add_brief(engine, niche="Gaming", rate_per_1000=1.0, min_views=5000, platforms=["instagram"])
    assert worth.worth(engine, beyond) == Worth(0.0, "USD", True, None)  # the guess doesn't reach 5,000


def test_no_rate_is_worth_nothing(engine):
    add_account(engine, "tiktok", "gamer", niche_tags=["gaming"])
    unknown = add_brief(engine, niche="Gaming")
    assert worth.worth(engine, unknown) == Worth(None, "USD", False, None)
    gone = add_brief(engine, niche="Gaming", rate_per_1000=3.0, status="gone")
    assert worth.worth(engine, gone) == Worth(0.0, "USD", False, None)


def test_openable_hosts(engine):
    store.add_source(
        engine.db(),
        BriefSource(
            id="bs_clips",
            name="Clips",
            marketplace="Clips",
            list_url="https://www.clips.example/campaigns",
            reader="general",
            created_at=1.0,
            updated_at=1.0,
        ),
    )
    opens = [
        add_brief(engine, url="https://contentrewards.com/c/1"),
        add_brief(engine, url="https://app.whop.com/c/2"),
        add_brief(engine, url="https://vyro.com/campaigns/3", marketplace="Vyro"),
        add_brief(engine, url="https://clips.example/c/4", source_id="bs_clips"),
    ]
    stays = [
        add_brief(engine, url="http://contentrewards.com/c/5"),
        add_brief(engine, url="https://clippingalpha.com/c/6"),
        add_brief(engine, url="https://notwhop.com/c/7"),
        add_brief(engine, url="https://vyro.com.example/c/8"),
        add_brief(engine, url="https://me@whop.com/c/9"),
    ]
    assert [worth.openable(engine, b) for b in opens] == [True] * 4
    assert [worth.openable(engine, b) for b in stays] == [False] * 5
    no_link = add_brief(engine, url=None, external_id="row-10")
    assert worth.openable(engine, no_link) is False

    client = api_client(engine)
    for brief in opens[:2]:
        assert client.post(f"/api/links/{quote('brief:' + brief.id, safe=':')}/open").status_code == 204
    for brief in stays[:2]:
        refused = client.post(f"/api/links/brief:{brief.id}/open")
        assert refused.status_code == 409
        assert refused.json()["error"] == "bad_link"
        assert refused.json()["message"].endswith(".")
    assert client.post(f"/api/links/brief:{no_link.id}/open").status_code == 404
    assert client.post("/api/links/brief:br_nothing/open").status_code == 404
    opened = [c for c in engine.run_cmd.calls if c[:1] == ["open"]]
    assert opened == [["open", "https://contentrewards.com/c/1"], ["open", "https://app.whop.com/c/2"]]
    with pytest.raises(ClipForgeError):
        links.link_url(engine, f"brief:{stays[1].id}")


def test_tiktok_never_fits_a_news_brief(engine):
    add_account(engine, "tiktok", "newsy", niche_tags=["news"])
    reels = add_account(engine, "instagram", "newsreels", niche_tags=["news"], daily_cap=1)
    brief = add_brief(engine, niche="News and politics", brand="Daily Wire", rate_per_1000=2.0)
    assert [a.id for a in worth.fits(engine, brief)] == [reels.id]
    assert worth.worth(engine, brief) == Worth(2.0, "USD", True, reels.id)
    found = advice.advice(engine, today_of(engine))
    assert not any(line.startswith("@newsy") for line in found.could_take)
    unfit = add_brief(
        engine, niche="News and politics", rate_per_1000=1.0, rates={"tiktok": 9.0, "instagram": 1.0}
    )
    post_store.update_account(engine.db(), reels.id, paused=True)
    assert worth.worth(engine, unfit) == Worth(1.0, "USD", True, None)  # the guess is on Instagram's rate


@pytest.mark.parametrize(
    ("title", "text", "expected"),
    [
        ("Edit Repository Reposts", "", True),
        ("Cantina UGC Reposting Campaign", "", True),
        ("Clip the stream", "Clip it or repost from the edit repo.", True),
        ("Clip the stream", "Re-post the approved edits.", True),
        ("Clip the stream", "Edit the provided assets, no raw reposts.", False),
        ("Clip the stream", "Don\u2019t repost the trailer. Reposts are not paid.", False),
        ("Clip the stream", "No raw reposts. You may also repost approved edits from the drive.", True),
        ("Repository of clips", "Clip the best moments.", False),
        ("Clip the stream", "No stolen content or reposts.", False),
        ("Clip the stream", "Reposted content will be rejected.", False),
        ("Clip the stream", "Reposts will be disqualified.", False),
        ("Clip the stream", "Reposted videos are not eligible.", False),
        ("Clip the stream", "Reposting is strictly prohibited.", False),
        ("Clip the stream", "You can't repost.", False),
        ("Clip the stream", "You cannot repost content.", False),
        ("Clip the stream", "Avoid reposting.", False),
        ("Clip the stream", "No AI. Reposting welcome.", True),
        ("Clip the stream", "We're looking for reposts from the approved edits repository.", True),
        ("Clip the stream", "Founder interview plus UGC repost options; full brief not opened.", True),
        ("Clip the stream", "Two ways in: restyle and repost edits from the drive, or make your own.", True),
    ],
)
def test_reposting_briefs(engine, title, text, expected):
    """A brief asks for reposts when its title or text says so, unless a no, not or don't sits just before
    the word or a not just after it."""
    assert worth.reposting(add_brief(engine, title=title, text=text)) is expected
