"""From linking the accounts to Posted (SPEC 10, 14.4 to 14.6) with nothing faked between the window and the
publishers: a TikTok account logged in on the look-alike TikTok, an Instagram account linked through the
Graph look-alike, three clips sent with Post now, and each site gets its first post once while the rest
wait for their times. The Chrome here is headless and nothing leaves 127.0.0.1."""

from __future__ import annotations

import random
import re

import pytest
from playwright.sync_api import expect

from clipforge.post import store
from clipforge.post.browser.pacing import Pacer
from clipforge.post.browser.session import BrowserSessions
from tests.fakes import fake_engine
from tests.fakes_graph import USER_TOKEN, FakeGraph
from tests.mock_sites.server import FIRST_ID, MockSites
from tests.ui.conftest import live_server
from tests.ui.seed import hook, picture, seed_project
from tests.ui.test_accounts import adding, card, open_accounts
from tests.ui.test_post_dialog import confirm_button, on_step, rows, start
from tests.ui.test_shell import toasts
from tests.ui.test_tiktok_browser import FAST

HANDLE = "realme"


@pytest.fixture
def sites():
    with MockSites(HANDLE) as s:
        s.login_as = HANDLE  # the person logs in while the login page is open
        yield s


@pytest.fixture
def meta():
    return FakeGraph()


@pytest.fixture
def engine(sites, meta):
    # The real clock: the runner starts a post's job by the time on this Mac.
    e = fake_engine()
    e.thumb_fetch = picture
    e.site_base = sites.origin
    e.browser = BrowserSessions(e.paths, headless=True, timing=FAST, pacer=Pacer(random.Random(1), 0.0, 0.0))
    e.graph_transport = meta.transport()
    yield e
    e.close()


@pytest.fixture
def window(engine):
    with live_server(engine) as live:
        yield live


def test_e2e_link_to_posted(page, engine, window, sites, meta, video):
    pid = seed_project(engine, clips=3, rendered=3, video=video)

    open_accounts(page, window)
    dialog = adding(page, "TikTok in Chrome")
    dialog.get_by_role("button", name="Log in with Chrome").click()
    expect(toasts(page)).to_contain_text("Chrome window")
    expect(card(page, f"@{HANDLE}")).to_contain_text("Connected", timeout=20_000)

    dialog = adding(page, "Instagram through Facebook")
    dialog.get_by_label("Access token", exact=True).fill(USER_TOKEN)
    dialog.get_by_role("button", name="Find accounts").click()
    expect(dialog.get_by_role("checkbox", name="@clips_1")).to_be_checked()
    dialog.get_by_role("checkbox", name="@clips_3").uncheck()  # the token's other account stays out
    dialog.get_by_role("button", name="Add 1 account").click()
    expect(card(page, "@clips_1")).to_contain_text("Connected")

    dialog = start(page, window, pid, 0, 1, 2, accounts=(f"@{HANDLE}", "@clips_1"))
    expect(dialog.get_by_role("radio", name="Spread")).to_be_checked()
    dialog.get_by_role("button", name="Next").click()
    dialog.get_by_role("radio", name="Post now").check()
    dialog.get_by_role("button", name="Next").click()
    on_step(dialog, "Review")
    expect(rows(dialog)).to_have_count(6)  # each clip to one account on each platform
    confirm_button(dialog).click()

    expect(page.get_by_role("heading", name="Queue", level=1)).to_be_visible()
    posted = page.get_by_role("article").filter(has_text="Posted")
    expect(posted).to_have_count(2, timeout=60_000)
    tiktok = page.get_by_role("article", name=f"{hook(0)} to @{HANDLE}", exact=True)
    instagram = page.get_by_role("article", name=f"{hook(0)} to @clips_1", exact=True)
    for row in (tiktok, instagram):
        expect(row).to_contain_text("Posted")
    expect(tiktok.get_by_role("link", name="Open the post")).to_have_attribute(
        "href", re.compile(rf"/@{HANDLE}/video/{FIRST_ID}$")
    )
    expect(instagram.get_by_role("link", name="Open the post")).to_have_attribute(
        "href", re.compile(r"^https://www\.instagram\.com/reel/[^/]+/$")
    )
    expect(page.get_by_role("article").filter(has_text="Scheduled")).to_have_count(4)
    assert len(sites.submissions) == 1
    assert len(meta.published) == 1
    posts = store.list_posts(engine.db())
    assert sorted(p.status for p in posts) == ["posted"] * 2 + ["scheduled"] * 4
