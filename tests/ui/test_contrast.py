"""A switch's knob stands out from its track in both themes, on and off (WCAG 1.4.11: 3:1 for the parts of a
control that show its state), a button's words still read with the mouse on it (4.5:1 for text), and every
word on Today and the Channels page, with the Channels dialogs, reads in both themes."""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from clipforge.watch import drafts
from clipforge.watch import store as watch_store
from tests.posting import add_account
from tests.ui.contrast import knob_contrast, low_text, text_contrast
from tests.ui.test_accounts import open_accounts
from tests.ui.test_library_settings import open_settings
from tests.ui.test_shell import opened
from tests.watching import add_channel, add_check, watched

STATE_BAR = 3.0
TEXT_BAR = 4.5


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_switch_knob_stands_out_on_and_off(page, ui_engine, server, theme):
    ui_engine.update_settings(ui_engine.settings.model_copy(update={"theme": theme}))
    open_settings(page, server)
    switch = page.get_by_role("switch", name="Emoji")
    seen = {}
    for _ in range(2):
        state = "on" if switch.is_checked() else "off"
        seen[state] = round(knob_contrast(page, switch), 2)
        switch.click()
        expect(switch).to_be_checked(checked=state == "off")
    assert set(seen) == {"on", "off"}
    low = {state: got for state, got in seen.items() if got < STATE_BAR}
    assert not low, f"{theme} knob under {STATE_BAR}:1 against its track: {low}"


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_a_primary_button_reads_with_the_mouse_on_it(page, ui_engine, server, theme):
    # Next and Confirm and post appear where the step before had its button, so the mouse is often on them.
    ui_engine.update_settings(ui_engine.settings.model_copy(update={"theme": theme}))
    open_accounts(page, server)
    button = page.get_by_role("button", name="Add account")
    button.hover()
    got = round(text_contrast(button), 2)
    assert got >= TEXT_BAR, f"{theme} primary button under the mouse at {got}:1"


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_today_and_channels_read_in_both_themes(page, ui_engine, server, theme):
    ui_engine.update_settings(ui_engine.settings.model_copy(update={"theme": theme}))
    account = add_account(ui_engine, "tiktok", "t0", daily_cap=2)
    channel = add_channel(ui_engine, [account], "Podcasts")
    pid, _ = watched(ui_engine, channel, add_check(ui_engine, channel), [90, 80])
    drafts.settle(ui_engine, pid)
    watch_store.update_channel(ui_engine.db(), channel.id, paused=True)  # its badge and line too
    low: list[str] = []

    opened(page, server.url)
    expect(page.get_by_role("region", name="Clips to review")).to_contain_text("2 drafts")
    low += [f"Today: {line}" for line in low_text(page)]

    opened(page, server.url + "/channels")
    card = page.get_by_role("article", name="Podcasts")
    expect(card.get_by_text("Paused", exact=True)).to_be_visible()
    low += [f"Channels: {line}" for line in low_text(page)]
    for button, dialog in [("History", "Podcasts history"), ("Edit", "Edit Podcasts"), ("Add source", None)]:
        card.get_by_role("button", name=button).click()
        shown = page.get_by_role("dialog", name=dialog or "Add a source to Podcasts")
        expect(shown).to_be_visible()
        if button == "History":
            expect(shown.get_by_role("listitem")).to_have_count(1)  # the one video it saw
        low += [f"{button}: {line}" for line in low_text(page)]
        page.keyboard.press("Escape")
        expect(shown).to_have_count(0)
    assert not low, f"{theme} text under AA:\n" + "\n".join(low)
