"""The Instagram browser route (SPEC 10.4, research 02 section 3b), experimental, against the look-alike
instagram.com on this Mac in headless Chrome: a Reel goes through the web composer as a person would share
it, and login checks the logged-in account is the handle that was typed. No request leaves 127.0.0.1."""

import json
import random
import stat
import time

import pytest

from clipforge.errors import ClipForgeError
from clipforge.post import publisher, store
from clipforge.post.browser import instagram
from clipforge.post.browser.instagram import InstagramBrowserPublisher
from clipforge.post.browser.login import WRONG_HANDLE, login_account
from clipforge.post.browser.pacing import Pacer
from clipforge.post.browser.run import why
from clipforge.post.browser.session import BrowserSessions, Timing
from clipforge.post.models import PostOptions
from clipforge.post.plan import CAPS
from tests.fakes import Clock, fake_engine
from tests.mock_sites.server import FIRST_CODE, OTHER_CODE, MockSites
from tests.posting import add_account, add_clip, at, custom, interrupt_first_visit, run

NOW = at(7, 10)
# Instagram's waits, cut down so the look-alike runs them in seconds. ``success_s`` is shorter than the
# look-alike's slow share, so only ``share_s`` can let a slow share through.
FAST = Timing(
    page_s=10,
    action_s=3,
    find_s=3,
    success_s=0.5,
    notice_s=1,
    settle_s=0.1,
    share_s=4,
    minute_s=0.15,
    poll_s=0.05,
)
AWAY_S = 10 * FAST.minute_s  # away_min is 10


@pytest.fixture
def sites():
    with MockSites(site="instagram") as s:
        yield s


@pytest.fixture
def engine(sites):
    eng = fake_engine(now=Clock(NOW))
    eng.site_base = sites.origin
    eng.browser = BrowserSessions(
        eng.paths, headless=True, timing=FAST, pacer=Pacer(random.Random(1), 0.0, 0.0)
    )
    yield eng
    eng.close()


def due(engine, caption="Watch this", hashtags=("money", "fyp"), **options):
    """An Instagram post to @mine through the browser that is due now."""
    account = add_account(engine, "instagram", "mine", method="browser")
    p = custom(engine, add_clip(engine), account, NOW)
    return store.update_post(
        engine.db(), p.id, caption=caption, hashtags=list(hashtags), options=PostOptions(**options)
    )


def steps(engine, post_id):
    path = engine.paths.post_logs / post_id / "steps.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


# Posting


def test_instagram_browser_posts_on_the_lookalike(engine, sites):
    p = due(engine)
    _, after = run(engine, p.id)
    assert after.status == "posted", after.error
    (sub,) = sites.submissions
    assert sub["caption"].rstrip() == "Watch this\n\n#money #fyp"
    assert sub["crop"] == "original"
    (chosen,) = [e for e in sites.events if e["name"] == "file_chosen"]
    assert chosen["file"].endswith(".mp4") and chosen["size"] == len(b"video 1")
    # Its link, from the reel whose caption matches: the account's pinned reel comes first in the grid
    assert after.external_id == FIRST_CODE
    assert after.url.endswith(f"/reel/{FIRST_CODE}/") and OTHER_CODE not in after.url
    assert after.note is None
    names = [s["step"] for s in steps(engine, p.id)]
    assert names.index("caption") < names.index("share") < names.index("shared")


def test_reels_notice_and_crop_original(engine, sites):
    _, after = run(engine, due(engine).id)
    assert after.status == "posted", after.error
    names = sites.event_names()
    assert names.index("file_chosen") < names.index("reels_notice_ok") < names.index("crop_original")
    assert [e["to"] for e in sites.events if e["name"] == "next"] == ["edit", "caption"]
    assert names.index("crop_original") < names.index("next")
    assert sites.submissions[0]["crop"] == "original"


def test_no_reels_notice_still_posts(engine, sites):
    sites.variants = {"no_reels_notice"}
    _, after = run(engine, due(engine).id)
    assert after.status == "posted", after.error
    assert "reels_notice_ok" not in sites.event_names()
    assert sites.submissions[0]["crop"] == "original"


def test_caption_keeps_line_breaks_and_ends_the_hashtag_list(engine, sites):
    body = "Line one\nLine two \U0001f525\n\nLast line"
    _, after = run(engine, due(engine, caption=body, hashtags=["money"]).id)
    assert after.status == "posted", after.error
    (sub,) = sites.submissions
    assert sub["caption"] == body + "\n\n#money "  # the trailing space closes Instagram's hashtag list
    assert sub["suggestions_open"] is False


def test_waits_up_to_six_minutes_for_shared(engine, sites):
    assert Timing().share_s == 6 * 60
    sites.variants = {"slow_share"}  # "Sharing" for 2 s: past success_s, inside share_s
    started = time.monotonic()
    _, after = run(engine, due(engine).id)
    assert after.status == "posted", after.error
    assert time.monotonic() - started >= 2
    assert len(sites.submissions) == 1


def test_not_now_modals_dismissed(engine, sites):
    sites.variants = {"save_login"}
    _, after = run(engine, due(engine).id)
    assert after.status == "posted", after.error
    names = sites.event_names()
    assert names.index("save_login_dismissed") < names.index("composer_opened")
    assert names.index("notifications_dismissed") < names.index("composer_opened")
    assert "notifications_on" not in names and "save_login_saved" not in names


def test_failed_share_needs_you_with_a_screenshot(engine, sites):
    sites.variants = {"failed"}
    started = time.monotonic()
    _, after = run(engine, due(engine).id)
    assert (after.status, after.error) == ("needs_you", f"{why(instagram.FAILED)} {instagram.LATE}")
    shot = after.state["screenshot"]
    png = engine.paths.post_logs / after.id / shot.rsplit("/", 1)[-1]
    assert png.read_bytes().startswith(b"\x89PNG")
    page = png.with_suffix(".html")
    assert stat.S_IMODE(page.stat().st_mode) == 0o600
    assert "couldn't be shared" in page.read_text(encoding="utf-8")
    assert time.monotonic() - started >= AWAY_S  # the window stayed open for the person first
    assert sites.submissions == []


def test_a_modal_that_comes_up_late_is_closed(engine, sites):
    sites.variants = {"late_notifications"}  # it comes up over the Create menu
    _, after = run(engine, due(engine).id)
    assert after.status == "posted", after.error
    names = sites.event_names()
    assert names.index("notifications_dismissed") < names.index("composer_opened")


def test_caption_that_doesnt_take_needs_you(engine, sites):
    sites.variants = {"drop_caption"}
    _, after = run(engine, due(engine).id)
    assert (after.status, after.error) == ("needs_you", f"{why(instagram.CAPTION_WRONG)} {instagram.LATE}")
    assert "share_clicked" not in sites.event_names() and sites.submissions == []


def test_pause_before_share_shares_nothing(engine, sites):
    def pause(event):
        if event["name"] == "file_chosen":
            posting = engine.settings.posting.model_copy(update={"paused": True})
            engine.update_settings(engine.settings.model_copy(update={"posting": posting}))

    sites.on_event = pause
    _, after = run(engine, due(engine).id)
    assert after.status == "scheduled"
    assert sites.submissions == [] and "share_clicked" not in sites.event_names()


def test_logged_out_needs_login(engine, sites):
    sites.variants = {"login_redirect"}
    p = due(engine)
    _, after = run(engine, p.id)
    sentence = instagram.LOGGED_OUT.format(handle="mine")
    assert (after.status, after.error) == ("needs_you", instagram.LOGGED_OUT_POST.format(handle="mine"))
    account = store.get_account(engine.db(), p.account_id)
    assert (account.status, account.status_note) == ("needs_login", sentence)
    assert sites.submissions == []


def test_logged_out_at_home_needs_login(engine, sites):
    sites.variants = {"login_home"}  # instagram.com shows its login form at / itself
    p = due(engine)
    _, after = run(engine, p.id)
    assert (after.status, after.error) == ("needs_you", instagram.LOGGED_OUT_POST.format(handle="mine"))
    assert store.get_account(engine.db(), p.account_id).status == "needs_login"


# Login and the self-test


def typed(engine, handle):
    a = add_account(engine, "instagram", handle, method="browser")
    return store.update_account(engine.db(), a.id, status="needs_login")


def test_login_checks_the_typed_handle(engine, sites):
    sites.login_as = "mine"
    a = typed(engine, "mine")
    account = login_account(engine, a.id)
    assert (account.handle, account.status, account.status_note) == ("mine", "connected", None)
    avatar = engine.paths.accounts / a.id / "avatar.png"
    assert account.avatar_path == str(avatar) and avatar.read_bytes().startswith(b"\x89PNG")
    assert sites.served.index("/login/submit") < sites.served.index("/mine/")

    other = typed(engine, "someone.else")
    with pytest.raises(ClipForgeError) as err:
        login_account(engine, other.id)
    assert (err.value.code, str(err.value)) == ("wrong_handle", WRONG_HANDLE)
    assert WRONG_HANDLE == "That handle isn't the account you logged in with."
    assert "/someone.else/" in sites.served
    assert store.get_account(engine.db(), other.id).status == "needs_login"


def test_login_waits_out_the_sites_own_redirect(engine, sites, monkeypatch):
    sites.login_as = "mine"
    a = typed(engine, "mine")
    visits = interrupt_first_visit(monkeypatch, "/mine/")
    account = login_account(engine, a.id)
    assert (account.handle, account.status, account.status_note) == ("mine", "connected", None)
    assert sum(url.endswith("/mine/") for url in visits) == 2


def test_login_times_out_naming_instagram(engine, sites):
    a = typed(engine, "mine")
    with pytest.raises(ClipForgeError) as err:
        login_account(engine, a.id)
    assert err.value.code == "login_timeout"
    assert str(err.value) == "No Instagram login came through in 10 minutes. Press Log in to try again."


def test_selftest_on_the_lookalike_chooses_nothing(engine, sites):
    a = add_account(engine, "instagram", "mine", method="browser")
    route = engine.publishers["instagram:browser"]
    health = route.check(a)
    assert (health.status, health.sentence) == ("connected", instagram.READY)
    assert "composer_opened" not in sites.event_names() and sites.submissions == []
    sites.variants = {"login_home"}
    health = route.check(a)
    assert (health.status, health.sentence) == ("needs_login", instagram.LOGGED_OUT.format(handle="mine"))


def test_marked_experimental(engine):
    route = engine.publishers["instagram:browser"]
    assert isinstance(route, InstagramBrowserPublisher) and route.experimental is True
    caps = route.caps
    assert caps == CAPS["instagram:browser"]
    assert (caps.needs_human_finish, caps.max_duration_s, caps.max_size_mb, caps.daily_cap) == (
        False,
        900,
        300,
        1,
    )
    assert publisher.is_experimental(engine, add_account(engine, "instagram", "ig", method="browser"))
    assert not publisher.is_experimental(engine, add_account(engine, "instagram", "api"))
    assert not publisher.is_experimental(engine, add_account(engine, "tiktok", "tt"))
