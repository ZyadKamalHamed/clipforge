"""The Accounts page (SPEC 11): a card per account with how it posts, how it is, today's posts against its
cap and its last post; adding one through Chrome or Meta's API with the steps said first and the password
never asked for; a pasted key never shown again; Check, Reconnect, Pause, Settings and Remove, which asks
first and names the account, and each account's style kit. Logins and self-tests are fakes here and Meta is
the Graph look-alike, so nothing leaves 127.0.0.1."""

from __future__ import annotations

import re
import threading

import pytest
from playwright.sync_api import Page, expect

from clipforge.config import WINDOW_WRONG, Window
from clipforge.post import keychain, queue, store
from clipforge.post.browser import login
from clipforge.post.browser import run as browser_run
from clipforge.post.browser.login import NO_TYPED_HANDLE
from clipforge.post.browser.selftest import SelfTestResult
from clipforge.post.browser.tiktok import CHANGED
from clipforge.render.styles import StyleKit
from clipforge.server.routes.accounts import CREDIT_NO_CHANNEL
from tests.fakes import Clock, fake_engine
from tests.fakes_graph import IG_TOKEN, PAGE_TOKEN, USER_TOKEN, FakeGraph, error
from tests.posting import add_account, add_clip, at, custom
from tests.ui.conftest import live_server
from tests.ui.seed import picture
from tests.ui.test_shell import opened, toasts

NO_ACCOUNTS = "No accounts yet."
NOTHING_POSTS = "Nothing posts until you press Confirm and post."
NEVER_SEES = "ClipForge never sees your password."
ROUTES = [
    "TikTok in Chrome",
    "Instagram through Facebook",
    "Instagram with Instagram login",
    "Instagram in Chrome",
]
CAP_WRONG = "Posts a day is a whole number from 1 to 10."
TIKTOK_SPAM = "More than 3 a day on one TikTok account risks looking like spam."
ONE_A_DAY = "ClipForge posts at most 1 a day this way, so this account posts 1."
KIT_HINT = (
    "Posts to this account wear this look. A clip that looks different renders this account's version"
    " before it goes up."
)


@pytest.fixture
def graph(ui_engine):
    meta = FakeGraph()
    ui_engine.graph_transport = meta.transport()
    return meta


def open_accounts(page: Page, server) -> None:
    opened(page, f"{server.url}/accounts")
    expect(page.get_by_role("heading", name="Accounts", level=1)).to_be_visible()


def adding(page: Page, route: str):
    """The Add account dialog with ``route`` picked."""
    page.get_by_role("button", name="Add account").first.click()
    dialog = page.get_by_role("dialog", name="Add account")
    dialog.get_by_role("radio", name=route).check()
    return dialog


def card(page: Page, name: str):
    return page.get_by_role("article", name=name, exact=True)


def linked(engine, handle: str, mode: str, token: str, **fields):
    """An Instagram account linked through Meta's API, its key in the Keychain."""
    account = add_account(engine, "instagram", handle)
    ref = keychain.ref_for(account.id)
    keychain.put_token(ref, token)
    ids = {"ig_user_id": "17841400" if mode == "instagram" else "ig9", "fb_page_id": None}
    if mode == "facebook":
        ids["fb_page_id"] = "pg9"
    return store.update_account(engine.db(), account.id, ig_mode=mode, token_ref=ref, **ids | fields)


def test_accounts_empty_state_offers_add(page, server):
    open_accounts(page, server)
    expect(page.get_by_role("heading", name=NO_ACCOUNTS)).to_be_visible()
    expect(page.get_by_text(NOTHING_POSTS)).to_be_visible()
    expect(page.get_by_role("button", name="Add account")).to_have_count(1)
    page.get_by_role("button", name="Add account").click()
    dialog = page.get_by_role("dialog", name="Add account")
    for route in ROUTES:
        dialog.get_by_role("radio", name=route).check()
        steps = dialog.get_by_role("list", name="What happens")
        assert 3 <= steps.get_by_role("listitem").count() <= 5, route
        expect(steps).to_contain_text(NEVER_SEES)
    expect(dialog.get_by_role("radio", name="Instagram through Facebook")).to_have_accessible_name(
        "Instagram through Facebook Recommended"
    )
    dialog.get_by_role("button", name="Cancel").click()
    expect(dialog).to_be_hidden()


def test_add_tiktok_shows_the_steps_and_waits_for_login(page, ui_server, ui_engine, monkeypatch):
    gate = threading.Event()

    def logs_in(engine, account_id, ctx=None):
        assert gate.wait(15), "the test never let the login finish"
        return queue.change_account(
            engine, account_id, handle="clips_tt", status="connected", status_note=None
        )

    monkeypatch.setattr(login, "login_account", logs_in)
    try:
        open_accounts(page, ui_server)
        dialog = adding(page, "TikTok in Chrome")
        expect(dialog).to_contain_text(NEVER_SEES)
        dialog.get_by_label("Label", exact=True).fill("Gaming")
        dialog.get_by_label("Niche tags", exact=True).fill("Gaming, streamers")
        dialog.get_by_role("button", name="Log in with Chrome").click()
        expect(dialog).to_be_hidden()
        expect(toasts(page)).to_contain_text("Chrome window")
        waiting = card(page, "Gaming")
        expect(waiting.get_by_text("Logging in", exact=True)).to_be_visible()
        expect(waiting).to_contain_text("Log in in the Chrome window.")
        expect(waiting.get_by_role("button", name="Check")).to_have_count(0)
        gate.set()
        done = card(page, "@clips_tt")
        expect(done).to_contain_text("Connected")
        expect(done).not_to_contain_text("Log in in the Chrome window.")
        expect(done).to_contain_text("Gaming")
    finally:
        gate.set()
    (account,) = store.list_accounts(ui_engine.db())
    assert (account.label, account.settings.niche_tags) == ("Gaming", ["gaming", "streamers"])


def test_add_instagram_mode_a_picks_accounts(page, ui_server, ui_engine, graph):
    here = linked(ui_engine, "clips_3", "facebook", f"{PAGE_TOKEN}-old", ig_user_id="ig3", fb_page_id="pg3")
    open_accounts(page, ui_server)
    dialog = adding(page, "Instagram through Facebook")
    token = dialog.get_by_label("Access token", exact=True)
    expect(token).to_have_attribute("type", "password")
    token.fill(USER_TOKEN)
    dialog.get_by_role("button", name="Find accounts").click()
    first = dialog.get_by_role("checkbox", name="@clips_1")
    expect(first).to_be_checked()
    again = dialog.get_by_role("checkbox", name="@clips_3")
    expect(again).not_to_be_checked()
    expect(dialog).to_contain_text("Already added")
    dialog.get_by_role("button", name="Add 1 account").click()
    expect(dialog).to_be_hidden()
    expect(card(page, "@clips_1")).to_contain_text("Connected")
    expect(card(page, "@clips_1")).to_contain_text("Meta's API, through Facebook")
    expect(card(page, "@clips_3")).to_be_visible()
    content = page.content()
    assert USER_TOKEN not in content and PAGE_TOKEN not in content
    assert keychain.get_token(here.token_ref) == f"{PAGE_TOKEN}-old"  # left alone: it wasn't picked


def test_token_field_is_cleared_and_never_shown(page, ui_server, graph, console):
    console.allow(r"status of 400")  # Instagram's refusal of the key, which Chrome logs
    graph.script["me"].append(error(190, message="Invalid OAuth access token."))
    open_accounts(page, ui_server)
    dialog = adding(page, "Instagram with Instagram login")
    token = dialog.get_by_label("Access token", exact=True)
    expect(token).to_have_attribute("type", "password")
    expect(token).to_have_attribute("autocomplete", "off")
    token.fill(IG_TOKEN)
    dialog.get_by_role("button", name="Add account").click()
    expect(dialog).to_contain_text("Instagram didn't accept that key")
    expect(token).to_have_value("")
    expect(token).to_have_attribute("aria-invalid", "true")
    token.fill(IG_TOKEN)
    dialog.get_by_role("button", name="Add account").click()
    expect(dialog).to_be_hidden()
    expect(card(page, "@clips_b")).to_contain_text("Meta's API, Instagram login")
    assert IG_TOKEN not in page.content()


@pytest.fixture
def busy(monkeypatch):
    """A Tuesday at 6 am with one TikTok account that has posted twice today, against a cap of 2."""
    engine = fake_engine(now=Clock(at(6)))
    engine.thumb_fetch = picture
    monkeypatch.setattr(engine.runner, "start", lambda: None)
    tiktok = add_account(engine, "tiktok", "clips_tt", daily_cap=2)
    for n, hour in enumerate((7, 19), 1):
        went = custom(engine, add_clip(engine, n), tiktok, at(hour, 10))
        store.update_post(
            engine.db(), went.id, status="posted", posted_at=at(hour, 12).timestamp(), url=f"https://x.y/{n}"
        )
    expiring = linked(engine, "clips_ig", "instagram", IG_TOKEN, status="token_expiring", paused=True)
    store.update_account(engine.db(), expiring.id, status_note="This key runs out in 9 days.")
    with live_server(engine) as live:
        yield live
    engine.close()


def test_card_shows_status_cap_and_last_post(page, busy):
    open_accounts(page, busy)
    expect(page.get_by_role("heading", name="TikTok", level=2)).to_be_visible()
    expect(page.get_by_role("heading", name="Instagram", level=2)).to_be_visible()
    tiktok = card(page, "@clips_tt")
    expect(tiktok).to_contain_text("Posts in Chrome")
    expect(tiktok).to_contain_text("Connected")
    expect(tiktok).to_contain_text("2 of 2 today")
    expect(tiktok).to_contain_text("Last post")
    expect(tiktok.get_by_role("link", name="The deadline")).to_have_attribute("href", "https://x.y/2")
    expect(tiktok.get_by_role("button", name="Log in again")).to_be_visible()
    instagram = card(page, "@clips_ig")
    expect(instagram).to_contain_text("Token expiring")
    expect(instagram).to_contain_text("Paused")
    expect(instagram).to_contain_text("This key runs out in 9 days.")
    expect(instagram).to_contain_text("No posts yet")
    expect(instagram.get_by_role("button", name="Resume")).to_be_visible()
    expect(instagram.get_by_role("button", name="Reconnect")).to_be_visible()


def test_check_and_reconnect(page, ui_server, ui_engine, graph, monkeypatch):
    own_key = linked(ui_engine, "clips_b", "instagram", IG_TOKEN, status="needs_login")
    facebook = linked(ui_engine, "clips_9", "facebook", f"{PAGE_TOKEN}-9")
    tiktok = add_account(ui_engine, "tiktok", "clips_tt")
    gate = threading.Event()

    def checks(engine, account):
        assert gate.wait(15), "the test never let the check finish"
        return SelfTestResult(found=["file_input"], missing=["caption"])

    monkeypatch.setattr(browser_run, "self_test", checks)
    try:
        open_accounts(page, ui_server)
        b = card(page, "@clips_b")
        expect(b).to_contain_text("Needs login")
        b.get_by_role("button", name="Check").click()
        expect(toasts(page)).to_contain_text("Connected.")
        expect(b).to_contain_text("Connected")

        b.get_by_role("button", name="Reconnect").click()
        dialog = page.get_by_role("dialog", name="New key for @clips_b")
        token = dialog.get_by_label("Access token", exact=True)
        expect(token).to_have_attribute("type", "password")
        token.fill(f"{IG_TOKEN}-new")
        dialog.get_by_role("button", name="Save key").click()
        expect(dialog).to_be_hidden()
        expect(toasts(page)).to_contain_text("New key saved")
        assert keychain.get_token(own_key.token_ref) == f"{IG_TOKEN}-new"

        card(page, "@clips_9").get_by_role("button", name="Reconnect").click()
        add = page.get_by_role("dialog", name="Add account")
        expect(add.get_by_role("radio", name="Instagram through Facebook")).to_be_checked()
        add.get_by_role("button", name="Cancel").click()
        assert store.get_account(ui_engine.db(), facebook.id) is not None

        t = card(page, "@clips_tt")
        t.get_by_role("button", name="Check").click()
        expect(t).to_contain_text("Checking the account's page")
        expect(t.get_by_role("button", name="Check")).to_be_disabled()
        gate.set()
        expect(t).to_contain_text("Not working")
        expect(t).to_contain_text(CHANGED)
        expect(t.get_by_role("button", name="Check")).to_be_enabled()
    finally:
        gate.set()
    assert store.get_account(ui_engine.db(), tiktok.id).status == "error"


def test_pause_account(page, server, ui_engine):
    tiktok = add_account(ui_engine, "tiktok", "clips_tt")
    open_accounts(page, server)
    t = card(page, "@clips_tt")
    t.get_by_role("button", name="Pause").click()
    expect(t).to_contain_text("Paused")
    expect(toasts(page)).to_contain_text("Paused")
    assert store.get_account(ui_engine.db(), tiktok.id).paused
    t.get_by_role("button", name="Resume").click()
    expect(t.get_by_role("button", name="Pause")).to_be_visible()
    assert not store.get_account(ui_engine.db(), tiktok.id).paused


def test_settings_saves_niche_tags_and_pair(page, server, ui_engine, console):
    console.allow(r"status of 422")  # the refused windows, which Chrome logs
    tiktok = add_account(ui_engine, "tiktok", "clips_tt")
    instagram = add_account(ui_engine, "instagram", "clips_ig")
    open_accounts(page, server)
    card(page, "@clips_tt").get_by_role("button", name="Settings").click()
    dialog = page.get_by_role("dialog", name="Settings for @clips_tt")
    dialog.get_by_label("Niche tags", exact=True).fill("Gaming, streamers")
    cap = dialog.get_by_label("Posts a day", exact=True)
    cap.fill("12")
    windows = dialog.get_by_label("Posting windows", exact=True)
    windows.fill("09:00 to 07:00")
    dialog.get_by_label("Pair with", exact=True).select_option(label="@clips_ig")
    expect(dialog.get_by_text("Assisted posting")).to_be_visible()  # TikTok only
    dialog.get_by_role("button", name="Save").click()
    expect(dialog).to_contain_text(CAP_WRONG)
    expect(cap).to_be_focused()
    cap.fill("2")
    dialog.get_by_role("button", name="Save").click()
    expect(dialog).to_contain_text(WINDOW_WRONG)
    expect(windows).to_have_attribute("aria-invalid", "true")
    assert store.get_account(ui_engine.db(), tiktok.id).settings.niche_tags == []  # nothing saved yet
    windows.fill("7:00 to 09:00")
    dialog.get_by_role("button", name="Save").click()
    expect(dialog).to_be_hidden()
    expect(toasts(page)).to_contain_text("Saved")
    saved = store.get_account(ui_engine.db(), tiktok.id).settings
    assert (saved.niche_tags, saved.daily_cap, saved.windows, saved.pair_id) == (
        ["gaming", "streamers"],
        2,
        [Window(start="07:00", end="09:00")],
        instagram.id,
    )
    assert store.get_account(ui_engine.db(), instagram.id).settings.pair_id == tiktok.id

    card(page, "@clips_ig").get_by_role("button", name="Settings").click()
    dialog = page.get_by_role("dialog", name="Settings for @clips_ig")
    expect(dialog.get_by_label("Pair with", exact=True)).to_have_value(tiktok.id)
    expect(dialog.get_by_text("Assisted posting")).to_have_count(0)
    dialog.get_by_role("button", name="Cancel").click()
    expect(dialog).to_be_hidden()


def test_style_kit_saves(page, server, ui_engine, console):
    console.allow(r"status of 422")  # the refused credit format, which Chrome logs
    tiktok = add_account(ui_engine, "tiktok", "clips_tt")
    open_accounts(page, server)
    card(page, "@clips_tt").get_by_role("button", name="Settings").click()
    dialog = page.get_by_role("dialog", name="Settings for @clips_tt")
    kit = dialog.get_by_role("region", name="Style kit")
    expect(kit).to_contain_text(KIT_HINT)
    for label in ("Caption style", "Font", "Accent", "Hook box colour", "Hook text colour", "Gameplay"):
        expect(kit.get_by_label(label, exact=True)).to_have_value("")  # the clip's own
        expect(kit.get_by_label(label, exact=True).locator("option").first).to_have_text("The clip's own")
    expect(kit.get_by_label("Your accent", exact=True)).to_have_count(0)
    kit.get_by_label("Caption style", exact=True).select_option(label="Neon")
    kit.get_by_label("Accent", exact=True).select_option(label="Your own colour")
    colour = kit.get_by_label("Your accent", exact=True)
    expect(colour).to_have_value("#00e5ff")  # it starts at the style's own
    colour.fill("#ff0066")
    credit = kit.get_by_label("Credit format", exact=True)
    credit.fill("Clip by me")
    dialog.get_by_role("button", name="Save").click()
    expect(dialog).to_contain_text(CREDIT_NO_CHANNEL)
    expect(credit).to_have_attribute("aria-invalid", "true")
    expect(credit).to_be_focused()
    assert store.get_account(ui_engine.db(), tiktok.id).settings.style_kit.empty  # nothing saved yet
    credit.fill("Clip: {channel}")
    dialog.get_by_role("button", name="Save").click()
    expect(dialog).to_be_hidden()
    expect(toasts(page)).to_contain_text("Saved")
    saved = StyleKit(preset="neon", accent="#FF0066", credit_format="Clip: {channel}")
    assert store.get_account(ui_engine.db(), tiktok.id).settings.style_kit == saved

    page.reload()
    expect(page.get_by_role("heading", name="Accounts", level=1)).to_be_visible()
    card(page, "@clips_tt").get_by_role("button", name="Settings").click()
    kit = page.get_by_role("dialog", name="Settings for @clips_tt").get_by_role("region", name="Style kit")
    expect(kit.get_by_label("Caption style", exact=True)).to_have_value("neon")
    expect(kit.get_by_label("Your accent", exact=True)).to_have_value("#ff0066")
    expect(kit.get_by_label("Credit format", exact=True)).to_have_value("Clip: {channel}")
    expect(kit.get_by_label("Font", exact=True)).to_have_value("")
    # Back to the clip's own accent: the rest of the kit stays.
    kit.get_by_label("Accent", exact=True).select_option(label="The clip's own")
    page.get_by_role("button", name="Save").click()
    expect(toasts(page)).to_contain_text("Saved")
    left = store.get_account(ui_engine.db(), tiktok.id).settings.style_kit
    assert left == StyleKit(preset="neon", credit_format="Clip: {channel}")


def test_posts_a_day_says_what_it_risks_or_cant_do(page, server, ui_engine):
    add_account(ui_engine, "tiktok", "clips_tt")
    add_account(ui_engine, "instagram", "clips_ig", method="browser")
    open_accounts(page, server)
    card(page, "@clips_tt").get_by_role("button", name="Settings").click()
    dialog = page.get_by_role("dialog", name="Settings for @clips_tt")
    cap = dialog.get_by_label("Posts a day", exact=True)
    cap.fill("4")
    expect(dialog.get_by_text(TIKTOK_SPAM)).to_be_visible()
    expect(cap).to_have_accessible_description(re.compile(re.escape(TIKTOK_SPAM)))
    cap.fill("3")
    expect(dialog.get_by_text(TIKTOK_SPAM)).to_have_count(0)
    dialog.get_by_role("button", name="Cancel").click()

    gram = card(page, "@clips_ig")
    expect(gram).to_contain_text("0 of 1 today")
    gram.get_by_role("button", name="Settings").click()
    dialog = page.get_by_role("dialog", name="Settings for @clips_ig")
    dialog.get_by_label("Posts a day", exact=True).fill("2")
    expect(dialog.get_by_text(ONE_A_DAY)).to_be_visible()
    dialog.get_by_label("Posts a day", exact=True).fill("1")
    expect(dialog.get_by_text(ONE_A_DAY)).to_have_count(0)


def test_a_login_in_progress_says_so_and_can_be_shown_or_stopped(page, ui_server, ui_engine, monkeypatch):
    """While a login waits for you, the card says so, brings its window to the front and stops it."""
    fronted = []
    tick = threading.Event()

    def waits(engine, account_id, ctx=None):
        for _ in range(300):
            if ctx.cancelled():
                return None
            tick.wait(0.05)
        raise AssertionError("the test never stopped the login")

    monkeypatch.setattr(login, "login_account", waits)
    monkeypatch.setattr(ui_engine.browser, "request_front", lambda: fronted.append(True))
    add_account(ui_engine, "tiktok", "mine")
    open_accounts(page, ui_server)
    mine = card(page, "@mine")
    mine.get_by_role("button", name="Log in again").click()
    expect(mine.get_by_text("Logging in", exact=True)).to_be_visible()
    expect(mine).not_to_contain_text("Connected")
    mine.get_by_role("button", name="Show the login window").click()
    expect(toasts(page)).to_contain_text("Bringing the window to the front.")
    assert fronted == [True]
    mine.get_by_role("button", name="Stop").click()
    expect(mine).not_to_contain_text("Log in in the Chrome window.")
    expect(mine).to_contain_text("Connected")
    expect(mine.get_by_role("button", name="Log in again")).to_be_enabled()


def test_a_dialogs_title_and_buttons_stay_in_sight_in_a_short_window(page, server):
    """Only the middle of a dialog scrolls, so its title and its buttons are never cut off."""
    page.set_viewport_size({"width": 1440, "height": 640})
    open_accounts(page, server)
    dialog = adding(page, "Instagram through Facebook")
    expect(dialog.get_by_role("heading", name="Add account")).to_be_in_viewport(ratio=1)
    for name in ("Cancel", "Find accounts"):
        expect(dialog.get_by_role("button", name=name)).to_be_in_viewport(ratio=1)


def test_delete_asks_first_and_names_the_account(page, server, ui_engine):
    add_account(ui_engine, "tiktok", "clips_tt")
    open_accounts(page, server)
    card(page, "@clips_tt").get_by_role("button", name="Remove").click()
    ask = page.get_by_role("dialog", name="Remove @clips_tt from ClipForge?")
    expect(ask).to_contain_text("deleted from this Mac")
    ask.get_by_role("button", name="Cancel").click()
    expect(ask).to_be_hidden()
    assert len(store.list_accounts(ui_engine.db())) == 1
    card(page, "@clips_tt").get_by_role("button", name="Remove").click()
    page.get_by_role("dialog", name="Remove @clips_tt from ClipForge?").get_by_role(
        "button", name="Remove"
    ).click()
    expect(toasts(page)).to_contain_text("Removed")
    expect(page.get_by_role("heading", name=NO_ACCOUNTS)).to_be_visible()
    assert store.list_accounts(ui_engine.db()) == []


def test_instagram_browser_marked_experimental(page, server, ui_engine, console):
    console.allow(r"status of 422")  # the missing handle, which Chrome logs
    open_accounts(page, server)
    dialog = adding(page, "Instagram in Chrome")
    expect(dialog).to_contain_text("Experimental")
    dialog.get_by_role("button", name="Log in with Chrome").click()
    handle = dialog.get_by_label("Handle", exact=True)
    expect(dialog).to_contain_text(NO_TYPED_HANDLE)
    expect(handle).to_have_attribute("aria-invalid", "true")
    handle.fill("@Clips_IG")
    dialog.get_by_role("button", name="Log in with Chrome").click()
    expect(dialog).to_be_hidden()
    made = card(page, "@clips_ig")
    expect(made).to_contain_text("Experimental")
    # Nothing runs here, so the login waits its turn in Chrome: no window yet to log in to or bring forward.
    expect(made.get_by_text("Waiting for Chrome", exact=True)).to_be_visible()
    expect(made).to_contain_text("Waiting for Chrome. The login window opens when the job before it is done.")
    expect(made.get_by_role("button", name="Show the login window")).to_have_count(0)
    expect(made.get_by_role("button", name="Stop")).to_be_visible()
    (account,) = store.list_accounts(ui_engine.db())
    assert (account.platform, account.method, account.handle) == ("instagram", "browser", "clips_ig")
