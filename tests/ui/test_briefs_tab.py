"""The Briefs tab on the Campaigns page (SPEC 10.11 and 14.11, phase 5d): the briefs the marketplaces list,
the richest first, each with its rate, its budget and the day that was read, the accounts it fits and what
it could earn a day; filters by niche and marketplace; Open, Use this campaign (the New campaign form
filled from the brief, each filled field to check, and the brief's page opened) and Hide; and a box a
paste-only marketplace for pasting a campaign's rules into the form. Nothing leaves 127.0.0.1: Claude is a
fake and ``open`` is recorded, never run."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from clipforge.briefs import store
from clipforge.campaigns import parse
from clipforge.campaigns import store as campaign_store
from tests.briefing import add_brief
from tests.fakes import Clock, FakeProvider, fake_engine
from tests.posting import add_account, at
from tests.ui.conftest import live_server
from tests.ui.test_shell import opened, toasts

NOW = at(7, 10)  # 7:10 am on Tue 3 Nov 2026 in Sydney
READ = at(9, 0, day=2).timestamp()  # the budget read on Mon 2 Nov
RULES = "Earn $1.50 per 1,000 views on TikTok. Tag #MrBeast."
CHECK = re.compile("Check this")


def answer(req):
    """Claude's reading of RULES: the name, the rate and the hashtag, every other field null."""
    given = {"name": "Beast clips", "rate_per_1000": 1.5, "hashtags": ["#MrBeast"]}
    return {name: None for name in parse.RulesAnswer.model_fields} | given


@pytest.fixture
def engine():
    e = fake_engine(now=Clock(NOW), provider=FakeProvider({"campaign": answer}))
    yield e
    e.close()


@pytest.fixture
def window(engine, page, monkeypatch):
    """The window over ``engine`` with nothing running, its clock at the engine's."""
    monkeypatch.setattr(engine.runner, "start", lambda: None)
    page.clock.set_fixed_time(NOW)
    with live_server(engine) as live:
        yield live


def briefs_tab(page: Page, window) -> None:
    opened(page, window.url + "/campaigns?tab=briefs")
    expect(page.get_by_role("tab", name="Briefs")).to_have_attribute("aria-selected", "true")


def card(page: Page, title: str):
    return page.get_by_role("article", name=title, exact=True)


def opened_links(engine) -> list[list[str]]:
    return [c for c in engine.run_cmd.calls if c[:1] == ["open"]]


def test_briefs_tab_lists_by_worth(page, engine, window):
    """The richest first: the small budget holds the highest rate's day below the others'."""
    add_account(engine, "tiktok", "realme", niche_tags=["gaming"])
    add_brief(engine, title="Big rate, small budget", rate_per_1000=5.0, budget_left=0.5)
    add_brief(engine, title="Podcast moments", rate_per_1000=1.0, rates={"tiktok": 1.0, "instagram": 0.5})
    add_brief(
        engine,
        title="Gaming clips",
        brand="Arcade Co",
        niche="Gaming",
        marketplace="Vyro",
        rate_per_1000=2.0,
        budget_left=812,
        budget_read_at=READ,
        platforms=["tiktok"],
    )
    opened(page, window.url + "/campaigns")
    page.get_by_role("tab", name="Briefs").click()
    expect(page).to_have_url(re.compile(r"/campaigns\?tab=briefs$"))

    cards = page.get_by_role("article")
    expect(cards).to_have_count(3)
    for i, title in enumerate(["Gaming clips", "Podcast moments", "Big rate, small budget"]):
        expect(cards.nth(i)).to_have_accessible_name(title)

    gaming = card(page, "Gaming clips")
    for line in ["Arcade Co", "Vyro", "US$2.00 per 1,000 views", "US$812 left, read 2 Nov", "TikTok only"]:
        expect(gaming).to_contain_text(line)
    expect(gaming).to_contain_text("Fits @realme")
    expect(gaming).to_contain_text(re.compile(r"about US\$\d+\.\d\d a day, a guess"))
    podcast = card(page, "Podcast moments")
    for line in ["up to US$1.00 per 1,000 views", "Budget not shown", "Fits no account yet"]:
        expect(podcast).to_contain_text(line)
    expect(podcast).to_contain_text("about US$1.00 a day, a guess")
    small = card(page, "Big rate, small budget")
    expect(small).to_contain_text("US$0.50 left")
    expect(small).to_contain_text("about US$0.50 a day, a guess")

    page.reload()  # the tab is kept in the address
    expect(page.get_by_role("tab", name="Briefs")).to_have_attribute("aria-selected", "true")
    expect(cards).to_have_count(3)


def test_reposting_briefs_come_first_with_a_tag(page, engine, window):
    add_brief(engine, title="Rich clips", rate_per_1000=5.0)
    add_brief(engine, title="Edit Reposts", rate_per_1000=0.5)
    briefs_tab(page, window)
    cards = page.get_by_role("article")
    expect(cards).to_have_count(2)
    expect(cards.nth(0)).to_have_accessible_name("Edit Reposts")
    expect(card(page, "Edit Reposts").get_by_text("Reposting", exact=True)).to_be_visible()
    expect(card(page, "Rich clips").get_by_text("Reposting", exact=True)).to_have_count(0)


def test_filters(page, engine, window):
    add_brief(engine, title="Whop gaming", niche="Gaming", marketplace="Whop", rate_per_1000=3.0)
    add_brief(engine, title="Vyro streams", niche="Streamers", marketplace="Vyro", rate_per_1000=2.0)
    add_brief(engine, title="Vyro gaming", niche="Gaming", marketplace="Vyro", rate_per_1000=1.0)
    briefs_tab(page, window)
    cards = page.get_by_role("article")
    expect(cards).to_have_count(3)

    page.get_by_label("Niche").select_option("Gaming")
    expect(cards).to_have_count(2)
    expect(card(page, "Vyro streams")).to_have_count(0)
    page.get_by_label("Marketplace").select_option("Vyro")
    expect(cards).to_have_count(1)
    expect(cards.first).to_have_accessible_name("Vyro gaming")
    page.get_by_label("Niche").select_option(label="All niches")
    expect(cards).to_have_count(2)
    # only the niches the briefs have are offered
    expect(page.get_by_label("Niche").locator("option")).to_have_text(["All niches", "Gaming", "Streamers"])

    page.get_by_label("Marketplace").select_option("Whop")
    page.get_by_label("Niche").select_option("Streamers")
    expect(cards).to_have_count(0)
    expect(page.get_by_text("No briefs match these filters.")).to_be_visible()


def test_use_this_campaign_fills_the_form(page, engine, window):
    brief = add_brief(
        engine, brand="MrBeast", rate_per_1000=1.5, budget_left=812, budget_read_at=READ, text=RULES
    )
    briefs_tab(page, window)
    card(page, "Clip the stream").get_by_role("button", name="Use this campaign").click()

    dialog = page.get_by_role("dialog", name="New campaign")
    name = dialog.get_by_label("Name", exact=True)
    rate = dialog.get_by_label("Rate per 1,000 views")
    budget = dialog.get_by_label("Budget left")
    expect(name).to_have_value("MrBeast: Clip the stream")
    expect(rate).to_have_value("1.5")
    expect(budget).to_have_value("812")
    expect(dialog.get_by_label("Marketplace")).to_have_value("Whop")
    expect(dialog.get_by_label("Campaign rules")).to_have_value(RULES)
    for field in (name, rate, budget):
        expect(field).to_have_accessible_description(CHECK)
    expect(toasts(page)).to_contain_text("Opened the brief's page in your browser.")
    assert opened_links(engine) == [["open", brief.url]]

    dialog.get_by_role("checkbox", name="I've checked the filled fields").check()
    dialog.get_by_role("button", name="Save").click()
    expect(dialog).to_have_count(0)
    expect(toasts(page)).to_contain_text("MrBeast: Clip the stream saved")
    expect(card(page, "Clip the stream")).to_have_count(0)

    (campaign,) = campaign_store.list_campaigns(engine.db())
    assert (campaign.name, campaign.brief_id, campaign.budget_entered_on) == (
        "MrBeast: Clip the stream",
        brief.id,
        "2026-11-02",
    )
    assert store.get_brief(engine.db(), brief.id).status == "used"
    assert len(opened_links(engine)) == 1  # saving opens nothing more


def test_hide_a_brief(page, engine, window):
    brief = add_brief(engine, title="Not for me", rate_per_1000=1.0)
    add_brief(engine, title="Keep this", rate_per_1000=0.5)
    briefs_tab(page, window)
    card(page, "Not for me").get_by_role("button", name="Hide").click()
    expect(card(page, "Not for me")).to_have_count(0)
    expect(card(page, "Keep this")).to_be_visible()
    assert store.get_brief(engine.db(), brief.id).status == "hidden"

    toasts(page).get_by_role("button", name="Undo").click()
    expect(card(page, "Not for me")).to_be_visible()
    assert store.get_brief(engine.db(), brief.id).status == "seen"
    assert opened_links(engine) == []


def test_open_a_brief(page, engine, window):
    brief = add_brief(engine, rate_per_1000=1.0)
    add_brief(engine, title="Somewhere else", url="https://example.com/c/1", rate_per_1000=0.5)
    briefs_tab(page, window)
    expect(card(page, "Somewhere else").get_by_role("button", name="Open")).to_have_count(0)
    card(page, "Clip the stream").get_by_role("button", name="Open").click()
    expect(toasts(page)).to_contain_text("Opened the brief's page in your browser.")
    assert opened_links(engine) == [["open", brief.url]]


def test_campaign_scout_notes_are_closed(page, engine, window):
    add_brief(engine, notes={"Why": "Fast payouts this week"})
    add_brief(engine, title="No notes")
    briefs_tab(page, window)
    notes = card(page, "Clip the stream").get_by_text("Campaign Scout's notes")
    expect(notes).to_be_visible()
    expect(page.get_by_text("Fast payouts this week")).to_be_hidden()
    notes.click()
    expect(page.get_by_text("Fast payouts this week")).to_be_visible()
    expect(card(page, "No notes").get_by_text("Campaign Scout's notes")).to_have_count(0)


def test_paste_box_starts_a_campaign(page, engine, window):
    briefs_tab(page, window)
    expect(page.get_by_text("No briefs yet.", exact=False)).to_be_visible()
    expect(page.get_by_role("form", name="Paste from Whop")).to_be_visible()
    box = page.get_by_role("form", name="Paste from Vyro")
    box.get_by_label("Paste a campaign's rules from Vyro").fill(RULES)
    box.get_by_role("button", name="Make a campaign").click()

    dialog = page.get_by_role("dialog", name="New campaign")
    expect(dialog.get_by_label("Campaign rules")).to_have_value(RULES)
    expect(dialog.get_by_label("Marketplace")).to_have_value("Vyro")
    dialog.get_by_role("button", name="Read the rules").click()
    expect(dialog.get_by_label("Name", exact=True)).to_have_value("Beast clips")
    expect(dialog.get_by_label("Marketplace")).to_have_value("Vyro")  # Claude found none, so yours stays
    assert opened_links(engine) == []
