"""Accounts worth adding (SPEC 10.11): the money on offer per niche from the live briefs, less the niches a
clipping channel already posts for and the ones you hid; the top 3 as suggestions with the accounts to make
(never TikTok for news), name ideas that never pass as the show or brand, a bio, a picture idea, the
clipping channel and a posting plan; and the accounts that could take a brief today."""

from datetime import timedelta

import pytest

from clipforge.briefs import advice, names, store
from clipforge.briefs.advice import covered
from clipforge.config import DEFAULT_NICHES, Niche
from clipforge.post import store as post_store
from tests.briefing import add_brief
from tests.fakes import Clock, fake_engine
from tests.posting import add_account, at, posted_post
from tests.watching import add_channel

YT = "https://www.youtube.com/watch?v=abcdefghijk"
LINKS = [YT, "https://vimeo.com/1", "youtu.be/abcdefghijk?t=5", "https://www.youtube.com/@clipper"]


@pytest.fixture
def clock():
    return Clock(at(9, 0))


@pytest.fixture
def engine(clock):
    eng = fake_engine(now=clock)
    yield eng
    eng.close()


def today(engine):
    return engine.now().date()


def niches(found) -> list[str]:
    return [s.niche for s in found.suggestions]


def test_top_three_uncovered_niches(engine):
    pod = add_account(engine, "tiktok", "pod", niche_tags=["podcast"])
    add_channel(engine, [pod])
    add_brief(engine, niche="Podcasts and interviews", rate_per_1000=9.0)  # a channel posts for it
    rich = [
        add_brief(engine, niche="Gaming", title="G3", rate_per_1000=3.0, budget_left=100.0),
        add_brief(engine, niche="Gaming", title="G1", rate_per_1000=1.0, budget_left=50.0),
        add_brief(engine, niche="Gaming", title="G05", rate_per_1000=0.5),
        add_brief(engine, niche="Gaming", title="G2", rate_per_1000=2.0, footage={"links": LINKS}),
    ]
    add_brief(engine, niche="Sports", rate_per_1000=5.0)
    add_brief(engine, niche="Music", rate_per_1000=1.0)
    add_brief(engine, niche="Music", rate_per_1000=30.0, status="gone")
    add_brief(engine, niche="Comedy and entertainment", rate_per_1000=0.5)
    add_brief(engine, niche="Comedy and entertainment", rate_per_1000=50.0, status="hidden")
    add_brief(engine, rate_per_1000=80.0)  # no niche

    found = advice.advice(engine, today(engine))
    assert niches(found) == ["Gaming", "Sports", "Music"]
    gaming = found.suggestions[0]
    assert (gaming.count, gaming.budget_left, gaming.per_day, gaming.guess) == (4, 150.0, 6.5, True)
    assert [b["id"] for b in gaming.best] == [rich[0].id, rich[3].id, rich[1].id]
    assert gaming.best[0]["worth"]["amount"] == 3.0
    assert (gaming.label, gaming.language, gaming.make) == ("Gaming", "en", ["tiktok", "instagram"])
    sources = [YT, "https://www.youtube.com/@clipper/videos"]  # YouTube only, each once
    assert gaming.channel == {"name": "Gaming clips", "sources": sources, "account_ids": []}
    assert gaming.plan == "Up to 2 posts a day on TikTok and 1 on Instagram, in your posting windows."
    assert found.suggestions[1].budget_left is None  # no brief said
    assert covered(engine) == {"Podcasts and interviews": "channel"}
    assert advice.plan_text({"tiktok": 2, "instagram": 2}) == (
        "Up to 2 posts a day on each, in your posting windows."
    )


def test_advice_with_no_accounts(engine):
    assert advice.advice(engine, today(engine)) == advice.Advice([], [])  # no briefs either
    add_brief(engine, niche="Gaming", rate_per_1000=1.0, platforms=["instagram"])
    found = advice.advice(engine, today(engine))
    assert niches(found) == ["Gaming"]
    assert found.suggestions[0].make == ["instagram"]  # only what its briefs allow
    assert found.suggestions[0].plan == "Up to 1 post a day, in your posting windows."
    assert found.could_take == []


def test_every_niche_covered_gives_none(engine):
    gamer = add_account(engine, "tiktok", "gamer", niche_tags=["gaming"])
    fan = add_account(engine, "instagram", "fan", niche_tags=["nba"])
    add_channel(engine, [gamer, fan], name="Mine")
    add_brief(engine, niche="Gaming", rate_per_1000=1.0)
    add_brief(engine, niche="Sports", rate_per_1000=1.0)
    assert advice.advice(engine, today(engine)).suggestions == []
    assert covered(engine) == {"Gaming": "channel", "Sports": "channel"}


def test_news_never_on_tiktok(engine):
    news = add_brief(engine, niche="News and politics", rate_per_1000=4.0)
    found = advice.advice(engine, today(engine))
    assert [(s.niche, s.make) for s in found.suggestions] == [("News and politics", ["instagram"])]
    store.update_brief(engine.db(), news.id, platforms=["tiktok"])
    assert advice.advice(engine, today(engine)).suggestions == []  # only TikTok could post it


def test_hidden_comes_back(engine):
    add_brief(engine, niche="Gaming", rate_per_1000=1.0)
    add_brief(engine, niche="Sports", rate_per_1000=2.0)
    day = today(engine)
    store.hide_advice(engine.db(), "suggestion", "Sports", "en", (day + timedelta(days=30)).isoformat())
    store.hide_advice(engine.db(), "niche", "Gaming", "en", (day + timedelta(days=1)).isoformat())
    assert niches(advice.advice(engine, day)) == []
    assert niches(advice.advice(engine, day + timedelta(days=1))) == ["Gaming"]
    assert niches(advice.advice(engine, day + timedelta(days=30))) == ["Sports", "Gaming"]  # 30 days on


def test_tagged_account_needs_only_a_channel(engine):
    gamer = add_account(engine, "tiktok", "gamer", niche_tags=["gaming"])
    add_brief(engine, niche="Gaming", rate_per_1000=1.0)
    found = advice.advice(engine, today(engine))
    gaming = found.suggestions[0]
    assert (gaming.niche, gaming.make) == ("Gaming", [])
    assert gaming.channel["account_ids"] == [gamer.id]
    assert gaming.plan == "Up to 2 posts a day, in your posting windows."
    assert covered(engine) == {"Gaming": "tagged"}


def test_names_never_official(engine):
    added = Niche(name="knitting and crafts", tags=["knitting"])
    labels = [names.label(n) for n in [*DEFAULT_NICHES, added]]
    assert labels == [
        "Drama", "Gaming", "Streamer", "Podcast", "Sports", "Comedy", "Music", "Money", "Tech", "Fitness",
        "Learning", "News", "Knitting",
    ]  # fmt: skip
    assert names.label(Niche(name="Supercalifragilisticexpialidocious", tags=["x"])) == "Supercalifragili"
    assert names.label(Niche(name="AI tools", tags=["ai"])) == "AI"
    for niche in [*DEFAULT_NICHES, added]:
        ideas = names.ideas(niche)
        assert len(ideas) == 3
        assert all("Clip" in idea for idea in ideas)
        assert "fan clips page" in names.bio(niche)
        assert names.bio(niche).startswith(f"Daily {niche.name.lower()} clips.")
        assert names.picture(niche).endswith("with no logo or anyone's photo")
    assert names.ideas(DEFAULT_NICHES[0]) == ["Drama TV Clips", "Drama Clips Daily", "Drama Clip Vault"]
    assert (
        names.picture(added) == "a simple knitting icon in your accent colour, with no logo or anyone's photo"
    )

    add_brief(engine, niche="Reality TV and drama", brand="Love Island", rate_per_1000=2.0)
    drama = advice.advice(engine, today(engine)).suggestions[0]
    assert drama.names == names.ideas(DEFAULT_NICHES[0])
    assert not any("love island" in idea.casefold() for idea in drama.names)
    assert "love island" not in drama.bio.casefold()


def test_could_take_needs_room_today(engine):
    gamer = add_account(engine, "tiktok", "gamer", niche_tags=["gaming"], daily_cap=1)
    resting = add_account(engine, "tiktok", "resting", niche_tags=["gaming"])
    post_store.update_account(engine.db(), resting.id, paused=True)
    add_brief(engine, niche="Gaming", brand="Kick", rate_per_1000=2.0)
    add_brief(engine, niche="Gaming", title="Clip the finals", rate_per_1000=1.0)
    add_brief(engine, niche="Gaming", brand="Nobody", rate_per_1000=5.0, status="hidden")
    found = advice.advice(engine, today(engine))
    assert found.could_take == ["@gamer could take Kick's brief today (about US$2.00 a post)"]
    posted_post(engine, gamer, days_ago=0)  # today's one post
    assert advice.advice(engine, today(engine)).could_take == []


def test_could_take_names_a_reposting_brief_first(engine):
    add_account(engine, "tiktok", "gamer", niche_tags=["gaming"], daily_cap=1)
    add_brief(engine, niche="Gaming", brand="Kick", rate_per_1000=2.0)
    add_brief(engine, niche="Gaming", brand="Henny", title="Edit Reposts", rate_per_1000=1.0)
    found = advice.advice(engine, today(engine))
    assert found.could_take == ["@gamer could take Henny's brief today (about US$1.00 a post)"]
