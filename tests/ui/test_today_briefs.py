"""Today's block 10 and the brief to-dos (SPEC 10.11 and 14.10, phase 5d): the new briefs worth a look, the
richest 5 first, with See all briefs and I've looked; the Accounts worth adding card, its Set it up (a
paused clipping channel from the briefs' footage, for the account you tagged), Not now and Not this niche;
and a campaign made from a brief, to join with Open and I've joined from the To do list and from the
sheet's triangle. Nothing leaves 127.0.0.1: ``open`` is recorded, never run."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from clipforge.briefs import store
from clipforge.briefs.models import Footage
from clipforge.campaigns import store as campaign_store
from clipforge.watch import store as watch_store
from tests.briefing import add_brief
from tests.fakes import Clock, fake_engine
from tests.posting import add_account, add_campaign, at
from tests.ui.conftest import live_server
from tests.ui.test_campaigns import triangle
from tests.ui.test_shell import opened, toasts
from tests.ui.test_today import still, todo

NOW = at(7, 10)  # 7:10 am on Tue 3 Nov 2026 in Sydney
PAGE = "https://whop.com/beast"
DRAMA_PAGE = "https://whop.com/drama"
VIDEO = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.fixture
def engine():
    e = fake_engine(now=Clock(NOW))
    yield e
    e.close()


@pytest.fixture
def window(engine, page, monkeypatch):
    """The window over ``engine`` with nothing running, its clock at the engine's."""
    monkeypatch.setattr(engine.runner, "start", lambda: None)
    page.clock.set_fixed_time(NOW)
    with live_server(engine) as live:
        yield live


def briefs_block(page: Page):
    return page.get_by_role("region", name="New briefs", exact=True)


def advice_card(page: Page):
    return page.get_by_role("region", name="Accounts worth adding", exact=True)


def suggestion(page: Page, label: str):
    return advice_card(page).get_by_role("article", name=label, exact=True)


def opened_links(engine) -> list[list[str]]:
    return [c for c in engine.run_cmd.calls if c[:1] == ["open"]]


def test_block_ten_and_ive_looked(page, engine, window):
    add_account(engine, "tiktok", "realme", niche_tags=["gaming"])
    made = [
        add_brief(engine, title=f"Gaming {n}", niche="Gaming", rate_per_1000=float(n)) for n in range(1, 7)
    ]
    add_brief(engine, title="Looked at before", niche="Gaming", rate_per_1000=9.0, status="seen")
    opened(page, window.url)
    item = still(page).filter(has_text="Look at 6 new briefs")
    expect(item).to_have_count(1)
    expect(item.get_by_role("link", name="Look")).to_have_attribute("href", "#briefs")
    item.get_by_role("link", name="Look").click()
    expect(page).to_have_url(window.url + "/#briefs")
    block = briefs_block(page)
    expect(block).to_be_in_viewport()

    rows = block.get_by_role("article")
    expect(rows).to_have_count(5)  # the richest 5 of the 6 new ones; the one seen before isn't new
    for i, n in enumerate([6, 5, 4, 3, 2]):
        expect(rows.nth(i)).to_have_accessible_name(f"Gaming {n}")
    expect(rows.first).to_contain_text("Fits @realme")
    see_all = block.get_by_role("link", name="See all briefs")
    expect(see_all).to_have_attribute("href", "/campaigns?tab=briefs")

    block.get_by_role("button", name="I've looked").click()
    expect(rows).to_have_count(1)
    expect(rows.first).to_have_accessible_name("Gaming 1")
    assert [store.get_brief(engine.db(), b.id).status for b in made] == ["new"] + ["seen"] * 5
    expect(still(page).filter(has_text="Look at 1 new brief")).to_have_count(1)

    block.get_by_role("button", name="I've looked").click()
    expect(rows).to_have_count(0)
    expect(block.get_by_text("No new briefs to look at.")).to_be_visible()
    expect(still(page).filter(has_text="new brief")).to_have_count(0)  # the To do item ticked itself
    todo(page).get_by_text("Done today (1)").click()
    expect(todo(page).get_by_role("list", name="Done today")).to_contain_text("done in ClipForge")

    see_all.click()
    expect(page).to_have_url(window.url + "/campaigns?tab=briefs")
    expect(page.get_by_role("tab", name="Briefs")).to_have_attribute("aria-selected", "true")
    assert opened_links(engine) == []


def test_set_it_up_makes_a_paused_channel(page, engine, window):
    add_brief(
        engine,
        title="Island drama",
        niche="Reality TV and drama",
        rate_per_1000=2.0,
        budget_left=500,
        footage=Footage(kind="the show's clips", links=[VIDEO]),
    )
    opened(page, window.url)
    card = suggestion(page, "Drama")
    for line in [
        "1 live brief",
        "US$500 left",
        "Island drama",
        "Make a TikTok and an Instagram account",
        "Drama TV Clips",
        "Drama Clips Daily",
        "Drama Clip Vault",
    ]:
        expect(card).to_contain_text(line)
    set_up = card.get_by_role("button", name="Set it up")
    expect(set_up).to_be_disabled()
    expect(set_up).to_have_accessible_description(re.compile(r"Tag an account with reality or drama"))

    account = add_account(engine, "tiktok", "dramaclips", niche_tags=["drama"])
    page.reload()
    card = suggestion(page, "Drama")
    expect(card).not_to_contain_text("Make a TikTok")  # you have the account now
    card.get_by_role("button", name="Set it up").click()

    dialog = page.get_by_role("dialog", name="New clipping channel")
    expect(dialog.get_by_label("Name")).to_have_value("Drama clips")
    expect(dialog.get_by_label("YouTube channels")).to_have_value(VIDEO)
    expect(dialog.get_by_role("checkbox", name="@dramaclips")).to_be_checked()
    expect(dialog).to_contain_text("It starts paused")
    dialog.get_by_role("button", name="Save").click()
    expect(dialog).to_have_count(0)

    (channel,) = watch_store.list_channels(engine.db())
    assert (channel.name, channel.paused, channel.account_ids) == ("Drama clips", True, [account.id])
    (source,) = watch_store.list_sources(engine.db(), channel.id)
    assert engine.youtube.called("fetch_info") == [("fetch_info", VIDEO)]
    assert engine.youtube.called("list_channel") == []  # no backfill
    assert watch_store.items_for(engine.db(), channel.id) == []
    expect(suggestion(page, "Drama")).to_have_count(0)  # a clipping channel posts for it now
    assert source.clip_channel_id == channel.id


def test_not_this_niche_hides_it(page, engine, window):
    add_brief(engine, title="Speedrun", niche="Gaming", rate_per_1000=3.0)
    add_brief(engine, title="Big tune", niche="Music", rate_per_1000=2.0)
    add_brief(engine, title="Rally", niche="Sports", rate_per_1000=1.0)
    opened(page, window.url)
    for label in ("Gaming", "Music", "Sports"):
        expect(suggestion(page, label)).to_be_visible()

    suggestion(page, "Music").get_by_role("button", name="Not this niche").click()
    expect(suggestion(page, "Music")).to_have_count(0)
    suggestion(page, "Sports").get_by_role("button", name="Not now").click()
    expect(suggestion(page, "Sports")).to_have_count(0)
    expect(suggestion(page, "Gaming")).to_be_visible()
    assert store.hidden_advice(engine.db(), "2026-11-03") == {
        ("niche", "Music", "en"),
        ("suggestion", "Sports", "en"),
    }

    page.reload()  # it stays hidden
    expect(suggestion(page, "Gaming")).to_be_visible()
    expect(suggestion(page, "Music")).to_have_count(0)
    expect(suggestion(page, "Sports")).to_have_count(0)


def test_join_from_the_todo_and_the_triangle(page, engine, window):
    beast = add_campaign(engine, brief_id=add_brief(engine, title="Beast").id, campaign_url=PAGE)
    drama = add_campaign(
        engine, name="Drama clips", brief_id=add_brief(engine, title="Drama").id, campaign_url=DRAMA_PAGE
    )
    opened(page, window.url)
    item = still(page).filter(has_text="Join Beast clips on Whop")
    expect(item).to_have_count(1)
    item.get_by_role("button", name="Open").click()
    expect(toasts(page)).to_contain_text("Opened the campaign page in your browser.")
    assert opened_links(engine) == [["open", PAGE]]
    item.get_by_role("button", name="I've joined").click()
    expect(still(page).filter(has_text="Join Beast clips on Whop")).to_have_count(0)  # the item ended
    assert campaign_store.get_campaign(engine.db(), beast.id).joined_at is not None
    expect(still(page).filter(has_text="Join Drama clips on Whop")).to_have_count(1)

    page.get_by_role("navigation", name="Pages").get_by_role("link", name=re.compile("^Campaigns")).click()
    expect(triangle(page, "Beast clips")).to_have_count(0)
    mark = triangle(page, "Drama clips")
    mark.hover()
    shown = page.get_by_role("dialog", name="Drama clips needs you")
    line = shown.get_by_role("listitem").filter(has_text="Join on Whop")
    line.get_by_role("button", name="Open").click()
    expect(toasts(page)).to_contain_text("Opened the campaign page in your browser.")
    assert opened_links(engine) == [["open", PAGE], ["open", DRAMA_PAGE]]
    line.get_by_role("button", name="I've joined").click()
    expect(triangle(page, "Drama clips")).to_have_count(0)  # the line ended, and with it the triangle
    assert campaign_store.get_campaign(engine.db(), drama.id).joined_at is not None
