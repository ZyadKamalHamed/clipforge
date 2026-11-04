"""A fresh Mac (Phase 6 Review Focus 1): an empty ClipForge home with no accounts, no Keychain items, no
gameplay, no music and no Campaign Scout file. Every page loads and says what to do first, and nothing
shows a traceback, an undefined value or a bare error."""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

# The real read, taken before tests/ui/conftest.py's no_brief_reads swaps the module's tick for one that
# reads nothing: this test wants the morning read a Mac without HeyClicky gets.
from clipforge.briefs.run import tick as read_briefs
from tests.ui.conftest import live_server
from tests.ui.test_shell import opened

# Each page, its heading and the sentence that says what to do first.
FIRST_RUN = [
    ("", "Today", "Drop a YouTube or Drive link, or a video file"),
    ("create", "Create", "Drop a YouTube or Drive link, or a video file"),
    ("projects", "Projects", "Drop a YouTube or Drive link anywhere to start."),
    ("queue", "Queue", "Nothing is queued yet. Pick clips on a project and press Post."),
    ("channels", "Channels", "No clipping channels yet."),
    ("campaigns", "Campaigns", "No campaigns yet. Paste a campaign's rules to make one."),
    (
        "campaigns?tab=briefs",
        "Campaigns",
        "No briefs yet. Campaign Scout's file is read each morning, and you can paste a campaign's rules "
        "below.",
    ),
    ("money", "Money", "Money fills in once a post on a campaign goes up"),
    ("accounts", "Accounts", "Add the TikTok and Instagram accounts your clips go to."),
    ("trends", "Trends", "No Instagram account is linked through Facebook yet."),
    ("settings", "Settings", "Checks the tools ClipForge needs on this Mac."),
]
LIBRARY = [
    ("Gameplay", "Add videos you made or have a licence to use, with who made them."),
    ("Music", "Add tracks you own or have a licence for."),
    ("SFX", "ClipForge makes these itself."),
    ("Fonts", "The caption fonts that come with ClipForge"),
]
# Links to things that aren't there: each says so and where to go instead.
GONE = [
    ("projects/p_gone", "This project isn't here.", "Go to Projects"),
    ("clips/c_gone", "This clip isn't here.", "Go to Projects"),
    ("campaigns/cm_gone", "This campaign isn't here.", "Go to Campaigns"),
    ("nowhere", "That page isn't here.", "Go to the start"),
]
BROKEN = ("Traceback", "undefined", "NaN", "[object", "null")


@pytest.fixture
def window(ui_engine, monkeypatch):
    """The window over an empty home with nothing running."""
    monkeypatch.setattr(ui_engine.runner, "start", lambda: None)
    with live_server(ui_engine) as live:
        yield live


def main_text(page: Page) -> str:
    return page.get_by_role("main").inner_text()


def looks_whole(page: Page, where: str) -> None:
    text = main_text(page)
    for word in BROKEN:
        assert word not in text, f"{where or 'Today'} shows {word!r}: {text[:300]}"


def test_fresh_home_every_page_says_what_to_do(page, window):
    for path, heading, first in FIRST_RUN:
        opened(page, f"{window.url}/{path}")
        expect(page.get_by_role("heading", name=heading, level=1)).to_be_visible()
        expect(page.get_by_role("main").get_by_text(first).first).to_be_visible()
        looks_whole(page, path)

    opened(page, f"{window.url}/library")
    for tab, first in LIBRARY:
        page.get_by_role("tab", name=tab).click()
        expect(page.get_by_role("main").get_by_text(first).first).to_be_visible()
        looks_whole(page, f"library {tab}")


def test_fresh_home_gone_links_say_where_to_go(page, console, window):
    console.allow(r"status of 404")  # each missing thing is a 404 from the server, which Chrome logs
    for path, title, action in GONE:
        opened(page, f"{window.url}/{path}")
        expect(page.get_by_role("heading", name=title)).to_be_visible()
        expect(page.get_by_role("link", name=action)).to_be_visible()
        looks_whole(page, path)


def test_fresh_home_without_heyclicky_says_what_to_do(page, ui_engine, window):
    """The first morning read on a Mac with no Campaign Scout file puts one to-do on Today that says what
    to do about it, and Today doesn't talk of clipping channels that don't exist."""
    # The real clock runs here, so the read is due from midnight: before 06:00 nothing would be due yet.
    briefs = ui_engine.settings.briefs.model_copy(update={"read_at": "00:00"})
    ui_engine.update_settings(ui_engine.settings.model_copy(update={"briefs": briefs}))
    assert read_briefs(ui_engine) == 1
    opened(page, window.url)
    item = page.get_by_role("listitem").filter(has_text="HeyClicky's campaign file is missing")
    expect(item).to_contain_text(
        "Open HeyClicky so its Campaign Scout writes the file, or turn off Use Campaign Scout (HeyClicky) in "
        "Settings."
    )
    expect(item.get_by_role("link", name="Open Settings")).to_be_visible()
    main = page.get_by_role("main")
    expect(main).to_contain_text("No clips to review. Make a clipping channel on the Channels page")
    expect(main).not_to_contain_text("each clipping channel is paused")
    looks_whole(page, "")
