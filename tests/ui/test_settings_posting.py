"""Settings' Posting section (SPEC 10.4, 14.7 and 19): each posting setting saves as it changes, a refused one
says the engine's sentence at its field until it is fixed, and nothing here ever turns Pause posting on or
off: only the header's switch does."""

from __future__ import annotations

import re
import subprocess
from datetime import timedelta
from zoneinfo import ZoneInfo

from playwright.sync_api import Page, expect

from tests.posting import add_account, add_clip, custom
from tests.ui.test_shell import opened, toasts

CAP = "TikTok's daily cap must be a whole number from 1 to 10."
WINDOW_WRONG = "Each posting window has a start and an end like 07:00, and ends after it starts."
HTTPS = "The public address must start with https://."
TIKTOK_SPAM = "More than 3 a day on one TikTok account risks looking like spam."
INSTAGRAM_SPAM = "More than 2 a day on one Instagram account risks looking like spam."


def posting(page: Page, server):
    opened(page, f"{server.url}/settings")
    section = page.get_by_role("region", name="Posting")
    expect(section).to_be_visible()
    return section


def enter(field, text: str) -> None:
    field.fill(text)
    field.press("Enter")


def test_settings_posting_saves_and_validates(page, server, ui_engine, console):
    console.allow(r"status of 422")  # the refused values, which Chrome logs
    section = posting(page, server)
    cap = section.get_by_label("TikTok posts a day")
    expect(cap).to_have_value("2")
    expect(section.get_by_label("Posting windows")).to_have_value(
        "07:00 to 09:00\n12:00 to 13:00\n18:00 to 22:00"
    )

    enter(cap, "3")
    expect(toasts(page)).to_contain_text("Saved")
    assert ui_engine.settings.posting.tiktok_daily_cap == 3

    enter(cap, "11")
    expect(section.get_by_text(CAP)).to_be_visible()
    expect(cap).to_have_value("11")  # the refused value stays to fix
    assert ui_engine.settings.posting.tiktok_daily_cap == 3
    enter(cap, "4")
    expect(section.get_by_text(CAP)).to_have_count(0)
    assert ui_engine.settings.posting.tiktok_daily_cap == 4

    windows = section.get_by_label("Posting windows")
    windows.fill("07:00 to 09:00\n18:00 to 17:00")
    windows.blur()
    expect(section.get_by_text(WINDOW_WRONG)).to_be_visible()
    windows.fill("7:00 to 9:30\n18:00-22:00")
    windows.blur()
    expect(section.get_by_text(WINDOW_WRONG)).to_have_count(0)
    expect(windows).to_have_value("07:00 to 09:30\n18:00 to 22:00")
    assert [(w.start, w.end) for w in ui_engine.settings.posting.windows] == [
        ("07:00", "09:30"),
        ("18:00", "22:00"),
    ]

    address = section.get_by_label("Public address")
    enter(address, "http://mac.tail.ts.net")
    expect(section.get_by_text(HTTPS)).to_be_visible()
    enter(address, "https://mac.tail.ts.net/")
    expect(section.get_by_text(HTTPS)).to_have_count(0)
    assert ui_engine.settings.posting.public_base_url == "https://mac.tail.ts.net"

    section.get_by_role("switch", name="Alerts on Telegram").check()
    expect(toasts(page)).to_contain_text("Saved")
    assert ui_engine.settings.posting.telegram_alerts is True


def test_caps_above_what_looks_right_say_so(page, server, ui_engine):
    """SPEC 5 item 8: a warning above 3 a day on TikTok and 2 on Instagram, beside the number, which still
    saves."""
    section = posting(page, server)
    tiktok = section.get_by_label("TikTok posts a day")
    expect(section.get_by_text(TIKTOK_SPAM)).to_have_count(0)
    enter(tiktok, "4")
    expect(section.get_by_text(TIKTOK_SPAM)).to_be_visible()
    expect(tiktok).to_have_accessible_description(re.compile(re.escape(TIKTOK_SPAM)))
    assert ui_engine.settings.posting.tiktok_daily_cap == 4
    enter(section.get_by_label("Instagram posts a day"), "3")
    expect(section.get_by_text(INSTAGRAM_SPAM)).to_be_visible()
    enter(tiktok, "3")
    expect(section.get_by_text(TIKTOK_SPAM)).to_have_count(0)
    expect(section.get_by_text(INSTAGRAM_SPAM)).to_be_visible()


def test_saving_a_posting_setting_leaves_the_pause_alone(page, server, ui_engine):
    section = posting(page, server)
    page.get_by_role("banner").get_by_role("switch", name="Pause posting").check()
    expect(page.get_by_text("Posting is paused.", exact=False)).to_be_visible()

    enter(section.get_by_label("Instagram posts a day"), "2")
    expect(toasts(page)).to_contain_text("Saved")
    assert ui_engine.settings.posting.instagram_daily_cap == 2
    assert ui_engine.settings.posting.paused is True
    expect(page.get_by_role("banner").get_by_role("switch", name="Pause posting")).to_be_checked()
    page.get_by_role("banner").get_by_role("switch", name="Pause posting").uncheck()
    expect(page.get_by_text("Posting is paused.", exact=False)).to_have_count(0)
    assert ui_engine.settings.posting.paused is False


def test_tiktok_schedule_switch(page, server, ui_engine):
    """SPEC 10.3: TikTok's own schedule, on by default, can be turned off, and an assisted TikTok post
    confirmed after that waits for its time instead of going up ahead with TikTok's schedule set."""
    section = posting(page, server)
    switch = section.get_by_role("switch", name="TikTok's own schedule")
    expect(switch).to_be_checked()
    clip, account = add_clip(ui_engine), add_account(ui_engine, assisted=True)
    here = ui_engine.now().astimezone(ZoneInfo(ui_engine.settings.posting.timezone))
    later = (here + timedelta(days=2)).replace(hour=19, minute=0, second=0, microsecond=0)  # in a window
    assert "studio" in custom(ui_engine, clip, account, later).state
    switch.uncheck()
    expect(toasts(page)).to_contain_text("Saved")
    assert ui_engine.settings.posting.tiktok_schedule is False
    again = add_clip(ui_engine)  # another video: the same clip to the same account again is a repeat
    assert "studio" not in custom(ui_engine, again, account, later + timedelta(days=1)).state
    page.reload()
    expect(posting(page, server).get_by_role("switch", name="TikTok's own schedule")).not_to_be_checked()


def test_today_settings_save(page, server, ui_engine, console):
    """SPEC 14.10, 18 and 10.11: the daily notification's time and switch, the switch for the others (off by
    default), the starting guess and the exchange rates each save and come back after a reload."""
    console.allow(r"status of 422")
    opened(page, f"{server.url}/settings")
    today = page.get_by_role("region", name="Today")
    expect(page.get_by_role("region", name="Posting").get_by_role("switch")).to_have_count(
        2
    )  # Telegram and TikTok's schedule
    at = today.get_by_label("Daily notification at")
    expect(at).to_have_value("07:30")
    daily = today.get_by_role("switch", name="Send the daily notification")
    events = today.get_by_role("switch", name="Notify me about every post, draft and project")
    expect(daily).to_be_checked()
    expect(events).not_to_be_checked()

    enter(at, "7:5")
    expect(today.get_by_text("The daily notification's time must look like 07:30.")).to_be_visible()
    enter(at, "06:45")
    expect(today.get_by_text("The daily notification's time must look like 07:30.")).to_have_count(0)
    daily.uncheck()
    expect(toasts(page)).to_contain_text("Saved")
    events.check()
    enter(today.get_by_label("Starting guess for new accounts (views at 7 days)"), "2500")
    rates = today.get_by_label("Exchange rates to US$")
    rates.fill("aud 0.65\nGBP x")
    rates.blur()
    expect(today.get_by_role("alert")).to_have_text(
        "Write one currency a line with what one of it is worth in US$, like AUD 0.65."
    )
    rates.fill("aud 0.65")
    rates.blur()
    expect(rates).to_have_value("AUD 0.65")
    expect(toasts(page)).to_contain_text("Saved")

    def kept():
        t = ui_engine.settings.today
        return (t.notify_at, t.daily_notification, t.event_notifications, t.starting_views, t.rates)

    want = ("06:45", False, True, 2500, {"AUD": 0.65})
    for _ in range(50):  # an earlier Saved toast can still be up while the last two saves are on their way
        if kept() == want:
            break
        page.wait_for_timeout(100)
    assert kept() == want

    page.reload()
    today = page.get_by_role("region", name="Today")
    expect(today.get_by_label("Daily notification at")).to_have_value("06:45")
    expect(today.get_by_role("switch", name="Send the daily notification")).not_to_be_checked()
    expect(today.get_by_role("switch", name="Notify me about every post, draft and project")).to_be_checked()
    expect(today.get_by_label("Starting guess for new accounts (views at 7 days)")).to_have_value("2500")
    expect(today.get_by_label("Exchange rates to US$")).to_have_value("AUD 0.65")


def test_background_service_row(page, server, ui_engine, monkeypatch):
    """The row reads launchd's answer through the engine's runner, never the real launchctl, and Remove
    asks first."""
    from clipforge import service

    calls = []

    def run(args):
        calls.append(args[1])
        return subprocess.CompletedProcess(args, 0, "\tstate = running\n\tpid = 77\n", "")

    monkeypatch.setattr(service, "RUN", run)
    section = posting(page, server)
    row = section.get_by_role("group", name="Background service")
    expect(row).to_contain_text(
        "Not installed, so ClipForge runs from when you open it until you log out or restart the Mac."
    )
    row.get_by_role("button", name="Install").click()
    expect(toasts(page)).to_contain_text(service.INSTALLED)
    expect(row).to_contain_text(service.RUNNING)
    assert "bootstrap" in calls

    row.get_by_role("button", name="Remove").click()
    ask = page.get_by_role("dialog", name="Remove the background service?")
    expect(ask).to_contain_text(
        "ClipForge then runs from when you open it until you log out or restart the Mac, so after a"
        " restart the daily run and scheduled posts wait until you open it again."
    )
    ask.get_by_role("button", name="Cancel").click()
    expect(ask).to_have_count(0)
    assert service.plist_path(service.AGENTS).exists()
    row.get_by_role("button", name="Remove").click()
    page.get_by_role("dialog", name="Remove the background service?").get_by_role(
        "button", name="Remove"
    ).click()
    expect(toasts(page)).to_contain_text(service.REMOVED)
    expect(row).to_contain_text(service.OFF)
    assert not service.plist_path(service.AGENTS).exists()
