"""The path on Today (SPEC 14.10), in the window: a fresh home with the guide on opens on Today, not Create,
with the path at level 1 of 6, no money, posts or accounts yet, the broad account to make with its name
ideas, and the first three steps on the To do list. Ticking one there ticks it on the path, and a step's
button goes to the page its work is on. Nothing leaves 127.0.0.1."""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.fakes import Clock, fake_engine
from tests.posting import at
from tests.ui.conftest import live_server
from tests.ui.test_shell import opened

NAMES = "Late Night Clips, Clips After Dark, Daily Clip Drop"


@pytest.fixture
def window(monkeypatch):
    engine = fake_engine(now=Clock(at(7, 10)), today={"guide": True})
    monkeypatch.setattr(engine.runner, "start", lambda: None)
    try:
        with live_server(engine) as live:
            yield live
    finally:
        engine.close()


def test_a_fresh_home_opens_on_the_path_and_a_tick_moves_it_on(page: Page, window):
    opened(page, window.url)
    card = page.get_by_role("region", name="Your path to first money")
    expect(card.get_by_text("Level 1 of 6: Set up")).to_be_visible()
    expect(card.get_by_text("0 of 22 steps done")).to_be_visible()
    expect(card.get_by_role("progressbar", name="Steps done")).to_have_attribute("aria-valuenow", "0")
    expect(card.get_by_text(NAMES, exact=True)).to_be_visible()
    # Level 2 is open too, as its first step is already on your list, and says what that step asks.
    second = card.get_by_role("list", name="Level 2 steps")
    expect(second.get_by_text("In the TikTok app, sign up")).to_be_visible()
    todo = page.get_by_role("list", name="Still to do")
    expect(todo.get_by_text("Make a broad clips account on TikTok, like Late Night Clips")).to_be_visible()

    todo.get_by_label("Let ClipForge send you notifications").check()
    expect(card.get_by_text("1 of 22 steps done")).to_be_visible()
    expect(card.get_by_role("list", name="Level 1 steps").get_by_text("Ticked")).to_be_visible()


def test_a_step_goes_to_the_page_its_work_is_on(page: Page, window):
    opened(page, window.url)
    page.get_by_role("list", name="Still to do").get_by_role("link", name="Open Settings").click()
    expect(page.get_by_role("heading", name="Settings", level=1)).to_be_visible()
    expect(page.get_by_text("Show the path to your first money")).to_be_visible()
