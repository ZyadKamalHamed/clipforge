"""Every screen as a picture for the design review (SPEC 22, Phase 3): at a laptop's 1440x900 and a narrow
820x1000 window, dark and light, saved to CLIPFORGE_SCREENS when it is set, else the test's tmp folder. No
screen may scroll sideways. Nothing runs: every project, clip and check stays as seeded. Every
screen's text must meet WCAG AA contrast, in both themes. Phase 4 adds Accounts, Add account, the Post
dialog's three steps, the Queue with every status and Settings' Posting section."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from clipforge import repo
from clipforge.doctor import Check
from clipforge.library import gameplay
from clipforge.post import accounts as account_jobs
from clipforge.post import instagram_api, store
from clipforge.post.browser import tiktok
from clipforge.server.routes import system
from tests.fakes import Clock, fake_engine
from tests.posting import add_account, add_clip, at, custom
from tests.ui.conftest import live_server
from tests.ui.contrast import low_text
from tests.ui.seed import picture, seed_project
from tests.ui.test_accounts import adding, open_accounts
from tests.ui.test_create import button, menu
from tests.ui.test_editor import TABS, open_editor, seed_clip, tab
from tests.ui.test_library_settings import open_library, open_settings, section
from tests.ui.test_post_dialog import on_step, rows, start
from tests.ui.test_queue import becomes, queue_page
from tests.ui.test_shell import OVERLAY, URL, drag, opened, toasts

SIZES = {"wide": (1440, 900), "narrow": (820, 1000)}
LIBRARY_TABS = ["Gameplay", "SFX", "Fonts"]
CLIPS = 40
RENDERED = 10
DISCARDED = (12, 27)
OLDER = "An older talk"
TIERS = ["post", "test", "weak", "no"]
# The clips after the rendered ones go through every other state a clip can be in.
STATES = [
    {"status": "idea"},
    {"status": "packaged"},
    {"status": "analyzing"},
    {"status": "rendering"},
    {"status": "failed", "error": "The render stopped: ffmpeg couldn't read the source."},
]
FLAGS = {"red": ["defamation"], "soft": ["sponsor_read"]}
CHECKS = [
    Check("ffmpeg", True, "/opt/homebrew/bin/ffmpeg"),
    Check("yt-dlp", True, "2026.10.10"),
    Check("claude", False, "not found", "Install Claude Code, then sign in once in Terminal."),
    Check("speech model", False, "Parakeet is not downloaded yet", "It downloads by itself.", required=False),
]
# Lazy pictures below the fold load now, so a full-page picture shows them.
EAGER = """() => document.querySelectorAll("img[loading=lazy]").forEach((i) => { i.loading = "eager"; })"""
SETTLED = "async () => { await document.fonts.ready; return [...document.images].every((i) => i.complete); }"
# How far the page, or the main area that scrolls inside it, runs past the window's width.
SIDEWAYS = """() => Math.max(
  ...[document.documentElement, document.querySelector("main")].map((e) => e.scrollWidth - e.clientWidth)
)"""
# How much more the main area holds than it shows. The shell scrolls inside it, so a full-page picture of
# the document alone would stop at the window's height.
HIDDEN = (
    """() => { const m = document.querySelector("main"); return m ? m.scrollHeight - m.clientHeight : 0; }"""
)
# The window has taken its new height, and two frames have been drawn at it. A picture taken sooner repeats
# the top of the page where the new space should be.
RESIZED = "(height) => window.innerHeight === height"
DRAWN = "() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))"
MIN_BYTES = 10_000  # a blank page's picture is smaller than this
# An open dialog's middle: where it is scrolled to, how far it can go and how much of it shows. A dialog
# is shot at the window's own height, since a taller window makes it taller than anyone sees it.
DIALOG = """() => { const d = document.querySelector('[role="dialog"], [role="alertdialog"]');
  return d && { top: d.scrollTop, end: d.scrollHeight - d.clientHeight, shown: d.clientHeight }; }"""
SCROLL_DIALOG = (
    """(top) => { document.querySelector('[role="dialog"], [role="alertdialog"]').scrollTop = top; }"""
)
OVERLAP = 160  # px each further picture of a dialog repeats, about its title and buttons


def clip_state(k: int) -> dict:
    """Clip k's tier and score, a flag on every seventh, and past the rendered ones a state of its own."""
    row = {"tier": TIERS[k % len(TIERS)], "final_score": 92.0 - k}
    if k % 7 == 3:
        row["flags"] = FLAGS
    if k >= RENDERED:
        row |= STATES[k % len(STATES)]
    return row


class Shots:
    """Pictures of the page, numbered in the order taken, each checked for sideways scrolling and for text
    under AA's contrast. A dialog that scrolls gets further pictures, a page at a time, in parts."""

    def __init__(self, page: Page, folder: Path, prefix: str):
        self.page, self.folder, self.prefix = page, folder, prefix
        self.taken: list[Path] = []
        self.parts: list[Path] = []
        self.low: list[str] = []
        folder.mkdir(parents=True, exist_ok=True)

    def __call__(self, name: str) -> None:
        self.page.evaluate(EAGER)
        self.page.wait_for_function(SETTLED)
        assert self.page.evaluate(SIDEWAYS) <= 0, f"{name} scrolls sideways"
        self.low += [f"{name}: {line}" for line in low_text(self.page)]
        path = self.folder / f"{self.prefix}-{len(self.taken) + 1:02d}-{name}.png"
        dialog = self.page.evaluate(DIALOG)
        if dialog:
            self.dialog(path, dialog)
            self.taken.append(path)
            return
        size = self.page.viewport_size
        hidden = self.page.evaluate(HIDDEN)
        if hidden > 0:  # the window made tall enough to show all of it, then put back
            tall = size["height"] + hidden
            self.page.set_viewport_size({"width": size["width"], "height": tall})
            self.page.wait_for_function(RESIZED, arg=tall)
            self.page.evaluate(EAGER)
            self.page.wait_for_function(SETTLED)
        self.page.evaluate(DRAWN)
        self.page.screenshot(path=path, animations="disabled")
        self.page.set_viewport_size(size)
        self.taken.append(path)

    def dialog(self, path: Path, dialog: dict) -> None:
        """The dialog from its top, then a page further down each time until its end shows."""
        self.page.evaluate(SCROLL_DIALOG, 0)  # ticking a box near the bottom may have scrolled it
        self.page.evaluate(DRAWN)
        self.page.screenshot(path=path, animations="disabled")
        top, step, n = 0, max(dialog["shown"] - OVERLAP, 100), 1
        while top < dialog["end"]:
            top, n = min(top + step, dialog["end"]), n + 1
            self.page.evaluate(SCROLL_DIALOG, top)
            self.page.evaluate(DRAWN)
            part = path.with_name(f"{path.stem}-{n}.png")
            self.page.screenshot(path=part, animations="disabled")
            self.parts.append(part)
        self.page.evaluate(SCROLL_DIALOG, 0)


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", list(SIZES))
def test_screens(page, ui_engine, server, video, monkeypatch, tmp_path, size, theme):
    width, height = SIZES[size]
    page.set_viewport_size({"width": width, "height": height})
    ui_engine.update_settings(ui_engine.settings.model_copy(update={"theme": theme}))
    monkeypatch.setattr(system, "run_checks", lambda paths, settings: list(CHECKS))
    seed_project(ui_engine, title=OLDER, clips=3, rendered=1, video=video)
    cid = seed_clip(ui_engine, video)
    pid = seed_project(ui_engine, clips=CLIPS, rendered=RENDERED, video=video, each=clip_state)
    for k in DISCARDED:
        ui_engine.discard_clip(f"{pid}_c{k}")
    gameplay.add_gameplay(
        ui_engine.db(), ui_engine.paths, video, credit="Me", license="Mine", name="Parkour run"
    )
    shoot = Shots(page, Path(os.environ.get("CLIPFORGE_SCREENS") or tmp_path), f"{size}-{theme}")

    opened(page, server.url + "/create")
    page.get_by_role("button", name="Options").click()
    expect(page.get_by_role("group", name="Clip length")).to_be_visible()
    shoot("create")
    button(page, "Paste").click()
    expect(toasts(page)).to_contain_text("Paste with Cmd+V instead.")
    shoot("toast")
    expect(toasts(page)).to_contain_text("Paste with Cmd+V instead.")  # still up when the picture was taken

    opened(page, server.url + "/projects")
    expect(page.get_by_role("article")).to_have_count(3)
    shoot("projects")
    drag(page, "dragenter", uri=URL)
    expect(page.get_by_text(OVERLAY)).to_be_visible()
    shoot("drop-overlay")
    opened(page, server.url + "/projects")
    menu(page, OLDER, "Delete")
    expect(page.get_by_role("dialog", name="Delete this project?")).to_be_visible()
    shoot("delete-dialog")

    opened(page, f"{server.url}/projects/{pid}")
    expect(page.get_by_role("article")).to_have_count(CLIPS - len(DISCARDED))
    page.get_by_label("Show discarded").check()
    expect(page.get_by_role("article")).to_have_count(CLIPS)
    shoot("project")

    open_editor(page, server, cid)
    for name in TABS:
        tab(page, name)
        expect(page.get_by_role("tab", name=name, exact=True)).to_have_attribute("aria-selected", "true")
        shoot("editor-" + name.lower().replace(" ", "-"))

    for name in LIBRARY_TABS:
        open_library(page, server, name)
        shoot("library-" + name.lower())

    open_settings(page, server)
    expect(section(page, "Doctor").get_by_role("listitem")).to_have_count(len(CHECKS))
    shoot("settings")

    opened(page, server.url + "/nope")
    expect(page.get_by_role("heading", level=1)).to_contain_text("That page isn't here.")
    shoot("not-found")

    assert len(shoot.taken) == 8 + len(TABS) + len(LIBRARY_TABS)
    small = [p.name for p in shoot.taken + shoot.parts if p.stat().st_size < MIN_BYTES]
    assert not small, f"pictures too small to show a screen: {small}"
    assert not shoot.low, "text under AA contrast:\n" + "\n".join(shoot.low)


# Phase 4's screens: Accounts, Add account, the Post dialog, the Queue and Settings' Posting section, on a
# clock of their own so the Queue's times read the same in every picture.
POSTING_NOW = at(6)  # 6:00 am on Tue 3 Nov 2026 in Sydney
LOGIN = tiktok.LOGGED_OUT_POST.format(handle="tt_two")
TAKEN = instagram_api.REJECTED.format(why="the video is too long for a reel.")
DETAILS = "Graph error 2207026: the video is longer than 90 s."
POST_LINK = "https://www.tiktok.com/@tt_one/video/7311111111111111111"
CAP = "TikTok's daily cap must be a whole number from 1 to 10."


@pytest.fixture
def posting_engine():
    e = fake_engine(now=Clock(POSTING_NOW))
    e.thumb_fetch = picture
    yield e
    e.close()


@pytest.fixture
def posting_window(posting_engine, page, monkeypatch):
    """The window with nothing running, its clock at the engine's."""
    monkeypatch.setattr(posting_engine.runner, "start", lambda: None)
    monkeypatch.setattr(system, "run_checks", lambda paths, settings: list(CHECKS))
    page.clock.set_fixed_time(POSTING_NOW)
    with live_server(posting_engine) as live:
        yield live


def posting_accounts(engine) -> dict:
    """Three TikTok accounts and three Instagram ones: through Meta's API, and one in Chrome, which is
    experimental."""
    return {
        "tt_one": add_account(engine, "tiktok", "tt_one", niche_tags=["gaming"]),
        "tt_two": add_account(engine, "tiktok", "tt_two"),
        "tt_three": add_account(engine, "tiktok", "tt_three"),
        "ig_one": add_account(engine, "instagram", "ig_one", daily_cap=2),
        "ig_two": add_account(engine, "instagram", "ig_two"),
        "ig_web": add_account(engine, "instagram", "ig_web", method="browser"),
    }


def every_status(engine, accounts: dict) -> None:
    """A post in each state the Queue shows, one new clip each, at times inside the posting windows and the
    caps; then one TikTok account logged out, one Instagram account paused and one logging in."""

    def one(name: str, hour: int, status: str | None = None, *, minute: int = 0, **fields) -> None:
        post = custom(engine, add_clip(engine), accounts[name], at(hour, minute))
        if status:
            becomes(engine, post, status, **fields)

    one("tt_two", 19, "needs_you", error=LOGIN)
    one("tt_one", 21)
    one("tt_three", 20, "rendering")
    one("ig_one", 18, "uploading", minute=30)
    one("ig_web", 12, "processing")
    one("tt_one", 18, "posted", url=POST_LINK)
    one("ig_one", 7, "failed", error=TAKEN, error_code="rejected", details=DETAILS)  # a Retry can't change it
    one("tt_two", 8, "cancelled")
    one("tt_three", 7, "exported")
    store.update_account(engine.db(), accounts["tt_two"].id, status="needs_login")
    store.update_account(engine.db(), accounts["ig_two"].id, paused=True)
    account_jobs.start(engine, "account.login", accounts["ig_web"].id)  # waiting for you in Chrome


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", list(SIZES))
def test_posting_screens(page, posting_engine, posting_window, console, video, tmp_path, size, theme):
    console.allow(r"status of 422")  # the refused cap, which Chrome logs
    engine, window = posting_engine, posting_window
    width, height = SIZES[size]
    page.set_viewport_size({"width": width, "height": height})
    engine.update_settings(engine.settings.model_copy(update={"theme": theme}))
    shoot = Shots(page, Path(os.environ.get("CLIPFORGE_SCREENS") or tmp_path), f"{size}-{theme}-posting")

    open_accounts(page, window)
    expect(page.get_by_role("heading", name="No accounts yet.")).to_be_visible()
    shoot("accounts-empty")
    dialog = adding(page, "TikTok in Chrome")
    expect(dialog.get_by_role("button", name="Log in with Chrome")).to_be_visible()
    shoot("add-account-tiktok")
    dialog.get_by_role("radio", name="Instagram through Facebook").check()
    expect(dialog.get_by_label("Access token", exact=True)).to_be_visible()
    shoot("add-account-facebook")
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()

    accounts = posting_accounts(engine)
    every_status(engine, accounts)
    open_accounts(page, window)
    expect(page.get_by_role("article")).to_have_count(len(accounts))
    shoot("accounts")

    pid = seed_project(engine, clips=3, rendered=3, video=video)
    repo.update_project(engine.db(), pid, rights_basis="not_cleared")
    dialog = start(page, window, pid, 0, 1, accounts=("@tt_one", "@tt_three", "@ig_one"))
    dialog.get_by_role("radio", name="Every clip to every account").check()
    dialog.get_by_role("checkbox", name=re.compile("Post every clip to every account anyway")).check()
    expect(dialog.get_by_role("checkbox", name="@tt_two")).to_be_disabled()
    shoot("post-accounts")
    dialog.get_by_role("button", name="Next").click()
    on_step(dialog, "Details")
    first = dialog.get_by_role("group", name=re.compile(r" to @tt_one on TikTok$")).first
    first.locator("summary").click()  # one post open, the other five as their lines
    expect(first.get_by_label("Caption")).to_be_visible()  # the posts are planned
    shoot("post-details")
    dialog.get_by_role("button", name="Next").click()
    on_step(dialog, "Review")
    expect(rows(dialog)).to_have_count(6)
    shoot("post-review")
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()

    queue_page(page, window)
    expect(page.get_by_role("article")).to_have_count(9)
    shoot("queue")

    open_settings(page, window)
    posting = section(page, "Posting")
    cap = posting.get_by_label("TikTok posts a day")
    cap.fill("11")
    cap.press("Enter")
    expect(posting.get_by_text(CAP)).to_be_visible()
    shoot("settings-posting")

    assert len(shoot.taken) == 9
    small = [p.name for p in shoot.taken + shoot.parts if p.stat().st_size < MIN_BYTES]
    assert not small, f"pictures too small to show a screen: {small}"
    assert not shoot.low, "text under AA contrast:\n" + "\n".join(shoot.low)


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", list(SIZES))
def test_watch_screens(page, video, tmp_path, monkeypatch, size, theme):
    """Phase 5a's screens: Today before any clipping channel, Today with drafts from two, Channels with a
    paused one, New clipping channel and a History."""
    from tests.ui.test_channels import card
    from tests.ui.test_today import NOW, drafted
    from tests.watching import add_channel

    engine = fake_engine(now=Clock(NOW))
    engine.thumb_fetch = picture
    monkeypatch.setattr(engine.runner, "start", lambda: None)
    page.clock.set_fixed_time(NOW)
    width, height = SIZES[size]
    page.set_viewport_size({"width": width, "height": height})
    engine.update_settings(engine.settings.model_copy(update={"theme": theme}))
    shoot = Shots(page, Path(os.environ.get("CLIPFORGE_SCREENS") or tmp_path), f"{size}-{theme}-watch")
    try:
        with live_server(engine) as window:
            opened(page, window.url)
            expect(page.get_by_role("region", name="Start a project")).to_be_visible()
            shoot("today-empty")

            t0 = add_account(engine, "tiktok", "tt_one", daily_cap=2)
            i0 = add_account(engine, "instagram", "ig_one", daily_cap=1)
            t1 = add_account(engine, "tiktok", "tt_two", daily_cap=1)
            podcasts = add_channel(engine, [t0, i0], "Podcasts")
            gaming = add_channel(engine, [t1], "Gaming")
            drafted(engine, podcasts, "Why we wait", 90, video)
            drafted(engine, podcasts, "The long road back", 84, video)
            drafted(engine, gaming, "The last level", 70, video)
            add_channel(engine, [t1], "Reality TV", paused=True)
            opened(page, window.url)
            expect(page.get_by_role("region", name="Clips to review")).to_be_visible()
            shoot("today-drafts")

            opened(page, window.url + "/channels")
            expect(page.get_by_role("article")).to_have_count(3)
            shoot("channels")
            card(page, "Podcasts").get_by_role("button", name="History").click()
            expect(page.get_by_role("dialog", name="Podcasts history")).to_be_visible()
            shoot("history")
            page.keyboard.press("Escape")
            page.get_by_role("button", name="New clipping channel").click()
            expect(page.get_by_role("dialog", name="New clipping channel")).to_be_visible()
            shoot("new-channel")
    finally:
        engine.close()

    assert len(shoot.taken) == 5
    small = [p.name for p in shoot.taken + shoot.parts if p.stat().st_size < MIN_BYTES]
    assert not small, f"pictures too small to show a screen: {small}"
    assert not shoot.low, "text under AA contrast:\n" + "\n".join(shoot.low)


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", list(SIZES))
def test_campaign_screens(page, tmp_path, monkeypatch, size, theme):
    """Phase 5b's screens: Campaigns with rows in two currencies, a campaign's page with its submissions,
    New campaign, Today with a linked draft's rules, Settings with its Marketplaces and the Post dialog's
    Review with a linked project's rules."""
    from tests.posting import add_campaign
    from tests.ui.test_campaigns import HAND, NOW, campaign_draft, submission
    from tests.ui.test_post_dialog import rows, start, to_review

    engine = fake_engine(now=Clock(NOW))
    monkeypatch.setattr(engine.runner, "start", lambda: None)
    page.clock.set_fixed_time(NOW)
    width, height = SIZES[size]
    page.set_viewport_size({"width": width, "height": height})
    engine.update_settings(engine.settings.model_copy(update={"theme": theme}))
    shoot = Shots(page, Path(os.environ.get("CLIPFORGE_SCREENS") or tmp_path), f"{size}-{theme}-campaigns")
    beast = add_campaign(
        engine,
        rate_per_1000=1.5,
        budget_left=4200.0,
        ends_on="2026-11-30",
        required={"hashtags": ["Beast"], "mentions": ["@MrBeast"]},
        banned=["gameplay", "emoji"],
        hand_rules=[HAND],
    )
    add_campaign(engine, name="Stream highlights", marketplace="Vyro", rate_per_1000=0.8, currency="AUD")
    add_campaign(engine, name="Old clips", marketplace="Whop", status="ended")
    submission(engine, beast, views=48_000, status="approved")
    submission(engine, beast, views=12_500, status="submitted")
    submission(engine, beast, status="to_submit")
    campaign_draft(engine, beast)
    by_hand = seed_project(engine, clips=1, rendered=1)
    repo.update_project(engine.db(), by_hand, campaign_id=beast.id, rights_basis="clipping_programme")
    try:
        with live_server(engine) as window:
            opened(page, window.url + "/campaigns")
            expect(page.get_by_role("table", name="Campaigns")).to_be_visible()
            shoot("campaigns")
            page.get_by_role("link", name="Beast clips", exact=True).click()
            expect(page.get_by_role("heading", name="Beast clips", level=1)).to_be_visible()
            shoot("campaign")
            page.get_by_role("link", name="All campaigns").click()
            page.get_by_role("button", name="New campaign").click()
            expect(page.get_by_role("dialog", name="New campaign")).to_be_visible()
            shoot("new-campaign")
            page.keyboard.press("Escape")
            opened(page, window.url)
            expect(page.get_by_role("list", name="Beast clips rules")).to_be_visible()
            shoot("today-linked-draft")
            opened(page, window.url + "/settings")
            expect(page.get_by_role("region", name="Marketplaces")).to_be_visible()
            shoot("settings-marketplaces")
            dialog = start(page, window, by_hand, 0, accounts=("@t0",))
            to_review(dialog)
            expect(rows(dialog).first.get_by_role("list", name="Beast clips rules")).to_be_visible()
            shoot("post-review-rules")
    finally:
        engine.close()

    assert len(shoot.taken) == 6
    small = [p.name for p in shoot.taken + shoot.parts if p.stat().st_size < MIN_BYTES]
    assert not small, f"pictures too small to show a screen: {small}"
    assert not shoot.low, "text under AA contrast:\n" + "\n".join(shoot.low)


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", list(SIZES))
def test_today_screens(page, video, tmp_path, monkeypatch, size, theme):
    """Phase 5c's screens: Today with nothing to do, Today with a full list (an item carried over, one set
    aside as optional, Submit, Post, Guidelines, Money and yesterday's best), the payout dialog and the
    Campaigns sheet with a triangle open."""
    from clipforge.campaigns import store as campaigns
    from clipforge.campaigns import tracker
    from clipforge.campaigns.models import Metric
    from clipforge.config import Marketplace
    from clipforge.today import day
    from clipforge.today import store as today_store
    from clipforge.today.models import Timing
    from tests.fakes import FakePublisher
    from tests.posting import add_campaign, linked_clip, posted
    from tests.ui.test_campaigns import triangle
    from tests.ui.test_today import EMPTY, LIVE, NOW, SUBMIT, WITHDRAW, approved, drafted
    from tests.watching import add_channel

    clock = Clock(NOW)
    engine = fake_engine(now=clock, publishers={"tiktok:browser": FakePublisher()})
    engine.thumb_fetch = picture
    monkeypatch.setattr(engine.runner, "start", lambda: None)
    page.clock.set_fixed_time(NOW)
    width, height = SIZES[size]
    page.set_viewport_size({"width": width, "height": height})
    places = {"Whop": Marketplace(withdraw_url=WITHDRAW)}
    engine.update_settings(engine.settings.model_copy(update={"theme": theme, "marketplaces": places}))
    shoot = Shots(page, Path(os.environ.get("CLIPFORGE_SCREENS") or tmp_path), f"{size}-{theme}-today")
    podcasts = add_channel(engine, [add_account(engine, "tiktok", "tt_pods", daily_cap=2)], "Podcasts")
    try:
        with live_server(engine) as window:
            opened(page, window.url)
            expect(page.get_by_role("region", name="To do")).to_contain_text(EMPTY)
            shoot("today-nothing-to-do")

            beast = add_campaign(
                engine,
                rate_per_1000=1.5,
                submit_url=SUBMIT,
                required={"hashtags": ["Beast"], "mentions": ["@MrBeast"]},
                platforms=["tiktok"],
            )
            drama = add_campaign(
                engine, name="Reality drama", marketplace="Vyro", rate_per_1000=1.0, ends_on="2026-11-04"
            )
            clock.set(at(8, 0, day=1))
            counted = posted(engine, linked_clip(engine, beast, 1), add_account(engine, "tiktok", "tt_one"))
            clock.set(at(9, 0, day=1))
            tracker.move_submission(
                engine, campaigns.submission_of_post(engine.db(), counted.id).id, "submitted"
            )
            stuck = custom(
                engine, add_clip(engine, 9), add_account(engine, "tiktok", "tt_two"), at(19, day=2)
            )
            store.update_post(engine.db(), stuck.id, status="needs_you")
            clock.set(at(7, 30, day=2))
            day.refresh(engine)  # yesterday's list, left undone
            clock.set(at(8, 0, day=2))
            approved(engine, beast, add_account(engine, "tiktok", "tt_three"), 48_000)
            best = posted(engine, linked_clip(engine, beast, 2), add_account(engine, "tiktok", "tt_four"))
            store.update_post(engine.db(), best.id, url=LIVE)
            clock.set(at(8, 30, day=2))
            tracker.move_submission(
                engine, campaigns.submission_of_post(engine.db(), best.id).id, "submitted"
            )
            for when, views in ((at(9, 0, day=2), 2_300), (at(7, 0), 14_800)):
                campaigns.add_metric(
                    engine.db(),
                    Metric(post_id=best.id, captured_at=when.timestamp(), source="hand", views=views),
                )
            for n in range(3):  # your last three mornings: four minutes a draft, so today runs over
                done = at(7, 40 + n, day=2).timestamp()
                today_store.add_timing(
                    engine.db(),
                    Timing(
                        id=repo.new_id("tm"),
                        kind="drafts",
                        started_at=done - 480,
                        finished_at=done,
                        seconds=480.0,
                        units=2,
                    ),
                )
            clock.set(NOW)
            sending = posted(engine, linked_clip(engine, drama, 3), add_account(engine, "tiktok", "tt_drama"))
            store.update_post(engine.db(), sending.id, url=LIVE)
            reels = add_account(engine, "instagram", "ig_reels")
            store.update_account(engine.db(), reels.id, status="needs_login")
            out = custom(engine, add_clip(engine, 8), add_account(engine, "tiktok", "tt_hand"), at(19))
            engine.posting.export(out.id)
            gaming = add_channel(engine, [add_account(engine, "tiktok", "tt_games", daily_cap=1)], "Gaming")
            drafted(engine, podcasts, "Why we wait", 90, video)
            drafted(engine, podcasts, "The long road back", 84, video)
            drafted(engine, gaming, "The last level", 70, video)
            opened(page, window.url)
            todo = page.get_by_role("region", name="To do")
            expect(todo.get_by_text("Optional", exact=True)).to_have_count(1)
            expect(todo.get_by_text("Carried over", exact=True)).to_have_count(1)
            shoot("today-full")

            whop = page.get_by_role("region", name="Money", exact=True).get_by_role(
                "article", name="Whop in USD"
            )
            whop.get_by_role("button", name="I've been paid").click()
            expect(page.get_by_role("dialog", name="Paid on Whop")).to_be_visible()
            shoot("payout-dialog")
            page.keyboard.press("Escape")

            opened(page, window.url + "/campaigns")
            triangle(page, "Beast clips").focus()
            expect(page.get_by_role("dialog", name="Beast clips needs you")).to_be_visible()
            shoot("campaigns-triangle")
    finally:
        engine.close()

    assert len(shoot.taken) == 4
    small = [p.name for p in shoot.taken + shoot.parts if p.stat().st_size < MIN_BYTES]
    assert not small, f"pictures too small to show a screen: {small}"
    assert not shoot.low, "text under AA contrast:\n" + "\n".join(shoot.low)


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", list(SIZES))
def test_briefs_screens(page, tmp_path, monkeypatch, size, theme):
    """Phase 5d's screens: Today's block 10 with its briefs, the Accounts worth adding card and the paste box
    of a site that couldn't be read today, Set it up's paused clipping channel, the Briefs tab and Settings'
    Brief sources and Niches."""
    from clipforge.briefs import store as briefs
    from clipforge.briefs.models import BriefSource, Footage
    from tests.briefing import add_brief
    from tests.ui.test_today_briefs import NOW, VIDEO, advice_card, briefs_block, suggestion

    engine = fake_engine(now=Clock(NOW))
    monkeypatch.setattr(engine.runner, "start", lambda: None)
    page.clock.set_fixed_time(NOW)
    width, height = SIZES[size]
    page.set_viewport_size({"width": width, "height": height})
    engine.update_settings(engine.settings.model_copy(update={"theme": theme}))
    shoot = Shots(page, Path(os.environ.get("CLIPFORGE_SCREENS") or tmp_path), f"{size}-{theme}-briefs")
    add_account(engine, "tiktok", "realme", niche_tags=["gaming"])
    add_account(engine, "tiktok", "dramaclips", niche_tags=["drama"])
    add_brief(
        engine,
        title="Speedrun highlights",
        brand="Pixel Arena",
        niche="Gaming",
        rate_per_1000=3.0,
        budget_left=1200,
        ends_on="2026-11-20",
        platforms=["tiktok"],
    )
    add_brief(
        engine, title="Boss fights", niche="Gaming", rate_per_1000=2.0, min_views=10_000, max_payout=300
    )
    add_brief(
        engine,
        title="Island drama",
        niche="Reality TV and drama",
        rate_per_1000=2.0,
        budget_left=500,
        footage=Footage(kind="the show's clips", links=[VIDEO]),
    )
    add_brief(engine, title="Big tune", brand="Northwind", niche="Music", currency="AUD", rate_per_1000=1.5)
    add_brief(engine, title="Open brief", marketplace="Vyro", rate_per_1000=0.5)
    add_brief(engine, title="Looked at before", niche="Gaming", rate_per_1000=9.0, status="seen")
    now = engine.now().timestamp()
    briefs.add_source(
        engine.db(),
        BriefSource(
            id="bs_clips",
            name="Clip Site",
            marketplace="Clip Site",
            list_url="https://clips.example/campaigns",
            reader="general",
            terms_note="You checked this site's terms.",
            last_run_at=now,
            last_result="blocked",
            error="The site turned the read away.",
            created_at=now,
            updated_at=now,
        ),
    )
    try:
        with live_server(engine) as window:
            opened(page, window.url)
            expect(briefs_block(page).get_by_role("article")).to_have_count(4)
            expect(suggestion(page, "Music")).to_be_visible()
            expect(page.get_by_role("form", name="Paste from Clip Site")).to_be_visible()
            shoot("today-briefs")
            advice_card(page).get_by_role("article", name="Drama", exact=True).get_by_role(
                "button", name="Set it up"
            ).click()
            expect(page.get_by_role("dialog", name="New clipping channel")).to_contain_text(
                "It starts paused"
            )
            shoot("set-it-up")
            page.keyboard.press("Escape")
            opened(page, window.url + "/campaigns?tab=briefs")
            expect(page.get_by_role("article", name="Speedrun highlights", exact=True)).to_be_visible()
            shoot("briefs-tab")
            opened(page, window.url + "/settings")
            expect(page.get_by_role("region", name="Brief sources")).to_contain_text("Clip Site")
            shoot("settings-brief-sources")
    finally:
        engine.close()

    assert len(shoot.taken) == 4
    small = [p.name for p in shoot.taken + shoot.parts if p.stat().st_size < MIN_BYTES]
    assert not small, f"pictures too small to show a screen: {small}"
    assert not shoot.low, "text under AA contrast:\n" + "\n".join(shoot.low)


# Phase 5e's Money posts: (title, campaign, account, days ago, views at 1.5 and at 7 days, status, paid).
MONEY_POSTS = [
    ("The 100 day speedrun ends here", "whop", "gaming", 1, (4200, None), "submitted", None),
    ("He never saw the boss coming", "whop", "gaming", 4, (9100, None), "approved", None),
    ("She walked out of the reunion", "vyro", "drama", 2, (6400, None), "submitted", None),
    ("The dinner that ended it all", "vyro", "drama_ig", 6, (3100, None), "approved", None),
    ("Last place to first in one lap", "whop", "gaming", 9, (12000, 18500), "paid", 37.0),
    ("Nobody expected that answer", "euro", "drama_ig", 11, (2500, 4100), "submitted", None),
    ("The secret came out on camera", "vyro", "drama", 15, (8800, 14200), "paid", 21.3),
    ("One button and it was over", "whop", "gaming", 19, (900, 1300), "rejected", None),
    ("The final round, frame by frame", "whop", "gaming", 24, (5300, 9900), "paid", 19.8),
]


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", list(SIZES))
def test_money_screens(page, tmp_path, monkeypatch, size, theme):
    """Phase 5e's screens: the Money page empty, the Money page with posts in US$, A$ and EUR (no EUR rate)
    with a timeline bar's tooltip open, and the Campaigns sheet with a filter on and its Columns menu open."""
    from tests.posting import add_campaign, campaign_post
    from tests.ui.test_money import NOW

    engine = fake_engine(now=Clock(NOW))
    monkeypatch.setattr(engine.runner, "start", lambda: None)
    page.clock.set_fixed_time(NOW)
    width, height = SIZES[size]
    page.set_viewport_size({"width": width, "height": height})
    today = engine.settings.today.model_copy(update={"rates": {"AUD": 0.65}})
    engine.update_settings(engine.settings.model_copy(update={"theme": theme, "today": today}))
    shoot = Shots(page, Path(os.environ.get("CLIPFORGE_SCREENS") or tmp_path), f"{size}-{theme}-money")
    try:
        with live_server(engine) as window:
            opened(page, window.url + "/money")
            expect(page.get_by_text("No campaign posts yet")).to_be_visible()
            shoot("money-empty")

            campaigns = {
                "whop": add_campaign(engine, rate_per_1000=2.0, max_payout=40.0),
                "vyro": add_campaign(
                    engine, name="Drama nights", marketplace="Vyro", currency="AUD", rate_per_1000=1.5
                ),
                "euro": add_campaign(
                    engine, name="Euro clips", marketplace="Clipify", currency="EUR", rate_per_1000=1.0
                ),
            }
            accounts = {
                "gaming": add_account(engine, "tiktok", "levelupclips", niche_tags=["gaming"]),
                "drama": add_account(engine, "tiktok", "dramatvclips", niche_tags=["drama"]),
                "drama_ig": add_account(engine, "instagram", "dramatv.clips", niche_tags=["drama"]),
            }
            for title, campaign, account, days_ago, (early, week), status, paid in MONEY_POSTS:
                counts = ((1.5, early),) if week is None else ((1.5, early), (7, week))
                reason = "Not original enough" if status == "rejected" else None
                campaign_post(
                    engine,
                    campaigns[campaign],
                    accounts[account],
                    days_ago=days_ago,
                    counts=counts,
                    status=status,
                    paid=paid,
                    title=title,
                    reason=reason,
                )
            opened(page, window.url + "/money")
            expect(page.get_by_role("table", name="Posts").locator("tbody tr")).to_have_count(
                len(MONEY_POSTS)
            )
            page.get_by_role("button", name=re.compile(r"^2 Nov: ")).focus()
            expect(page.get_by_role("tooltip")).to_contain_text("2 Nov")
            shoot("money")

            opened(page, window.url + "/campaigns")
            page.get_by_role("combobox", name="Marketplace").select_option("Whop")
            expect(page.locator("tbody tr th a")).to_have_text(["Beast clips"])
            page.get_by_role("button", name="Columns").click()
            expect(page.get_by_role("menuitemcheckbox", name="Views")).to_be_visible()
            shoot("campaigns-sheet")
    finally:
        engine.close()

    assert len(shoot.taken) == 3
    small = [p.name for p in shoot.taken + shoot.parts if p.stat().st_size < MIN_BYTES]
    assert not small, f"pictures too small to show a screen: {small}"
    assert not shoot.low, "text under AA contrast:\n" + "\n".join(shoot.low)


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", list(SIZES))
def test_trends_screens(page, tmp_path, monkeypatch, size, theme):
    """Phase 5f's screens: the Trends page empty, then with an account's trending sounds, the accounts
    Instagram gives no list, a shortlist with a stale sound, the New list out badge and a hashtag set."""
    from clipforge.trends import store as trends_store
    from tests.ui.test_trends import NOW, a_set, facebook_account, listed
    from tests.unit.test_trends_store import shortlist

    engine = fake_engine(now=Clock(NOW))
    monkeypatch.setattr(engine.runner, "start", lambda: None)
    page.clock.set_fixed_time(NOW)
    width, height = SIZES[size]
    page.set_viewport_size({"width": width, "height": height})
    engine.update_settings(engine.settings.model_copy(update={"theme": theme}))
    shoot = Shots(page, Path(os.environ.get("CLIPFORGE_SCREENS") or tmp_path), f"{size}-{theme}-trends")
    try:
        with live_server(engine) as window:
            opened(page, window.url + "/trends")
            expect(page.get_by_text("No Instagram account is linked through Facebook yet.")).to_be_visible()
            shoot("trends-empty")

            now = NOW.timestamp()
            drama = facebook_account(engine, "dramatv.clips")
            gaming = add_account(engine, "instagram", "levelup.clips")
            store.update_account(engine.db(), gaming.id, ig_mode="instagram")
            listed(
                engine,
                drama,
                now - 3 * 3600,
                ("m0", "music", "Night Shift", 31.0),
                ("m1", "music", "The Lanterns", 18.5),
                ("o0", "original_sound", "dramatv.daily", 12.0),
            )
            for external_id, title, region, moods, energy, days in (
                ("7234567890123456789", "Late Train", "AU", ["calm", "dark"], 2, 1),
                ("7234567890123456780", "Arcade Rush", "US", ["hype"], 5, 16),
            ):
                trends_store.put_shortlist(
                    engine.db(),
                    shortlist(
                        external_id,
                        title=title,
                        region=region,
                        mood_tags=moods,
                        energy=energy,
                        created_at=now - days * 86400,
                    ),
                )
            a_set(engine, "Gaming core", "Gaming", "gaming", "clips", "minecraft", "fyp")
            page.reload()
            expect(page.get_by_role("button", name="Open Song m0")).to_be_visible()
            expect(page.get_by_text("New list out")).to_be_visible()
            shoot("trends")
    finally:
        engine.close()

    assert len(shoot.taken) == 2
    small = [p.name for p in shoot.taken + shoot.parts if p.stat().st_size < MIN_BYTES]
    assert not small, f"pictures too small to show a screen: {small}"
    assert not shoot.low, "text under AA contrast:\n" + "\n".join(shoot.low)


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", list(SIZES))
def test_extras_screens(page, video, tmp_path, monkeypatch, size, theme):
    """Phase 5g's screens: an account's Style kit, the Starter list with a download on its way and one
    that failed, Cold open on the Look tab, a voiceover on the Sound tab, a trial reel in the Post dialog
    and the note that an account's own version renders first."""
    from clipforge.library import starter
    from clipforge.render.styles import StyleKit
    from tests.helpers import make_test_audio
    from tests.ui.test_accounts import card
    from tests.ui.test_editor import ALTS, PACKAGING, TITLE, panel

    engine = fake_engine()
    engine.thumb_fetch = picture
    monkeypatch.setattr(engine.runner, "start", lambda: None)
    width, height = SIZES[size]
    page.set_viewport_size({"width": width, "height": height})
    engine.update_settings(engine.settings.model_copy(update={"theme": theme}))
    shoot = Shots(page, Path(os.environ.get("CLIPFORGE_SCREENS") or tmp_path), f"{size}-{theme}-extras")
    try:
        with live_server(engine) as window:
            kit = StyleKit(preset="neon", accent="#FF0066", credit_format="Clip: {channel}")
            add_account(engine, "tiktok", "tt_kit", style_kit=kit)
            add_account(engine, "instagram", "ig_one")
            open_accounts(page, window)
            card(page, "@tt_kit").get_by_role("button", name="Settings").click()
            expect(page.get_by_role("dialog", name="Settings for @tt_kit")).to_be_visible()
            shoot("account-style-kit")
            page.keyboard.press("Escape")

            engine.runner.enqueue(starter.KIND, "ludo-surfs-subway")
            failed = engine.runner.enqueue(starter.KIND, "sybo-subway-9x16")
            engine.db().execute(
                "UPDATE jobs SET status = 'failed', error = ? WHERE id = ?",
                (starter.LOW_DISK.format(gb=10.0), failed),
            )
            open_library(page, window)
            expect(section(page, "Starter list")).to_contain_text("Download again")
            shoot("library-starter")

            picked = PACKAGING.model_copy(
                update={"hook_title": TITLE, "hook_alts": ALTS, "cold_open_sid": "S10"}
            )
            cid = seed_clip(engine, video, packaging=picked.model_dump(mode="json"))
            open_editor(page, window, cid)
            tab(page, "Look")
            panel(page).get_by_role("switch", name="Cold open").click()
            expect(panel(page).get_by_role("switch", name="Cold open")).to_be_checked()
            shoot("editor-cold-open")
            tab(page, "Sound")
            voice = panel(page).get_by_role("region", name="Voiceover")
            voice.get_by_label("Voiceover recording file").set_input_files(
                make_test_audio(tmp_path / "take.wav", 2.0, 440)
            )
            expect(voice.get_by_label("Your voiceover")).to_be_visible()
            shoot("editor-voiceover")

            pid = seed_project(engine, clips=1, rendered=1, video=video)
            dialog = start(page, window, pid, 0, accounts=("@tt_kit", "@ig_one"))
            dialog.get_by_role("button", name="Next").click()
            instagram = dialog.get_by_role("group", name=re.compile(r" to @ig_one on Instagram$"))
            instagram.get_by_role("switch", name="Trial reel").check()
            expect(
                instagram.get_by_role("switch", name="Share with followers if it does well")
            ).to_be_visible()
            shoot("post-trial-reel")
            dialog.get_by_role("button", name="Next").click()
            on_step(dialog, "Review")
            expect(dialog).to_contain_text("Renders this account's version first.")
            shoot("post-variant-note")
            page.keyboard.press("Escape")
    finally:
        engine.close()

    assert len(shoot.taken) == 6
    small = [p.name for p in shoot.taken + shoot.parts if p.stat().st_size < MIN_BYTES]
    assert not small, f"pictures too small to show a screen: {small}"
    assert not shoot.low, "text under AA contrast:\n" + "\n".join(shoot.low)
