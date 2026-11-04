"""The Queue (SPEC 14.6): every post with its status, clip, account, time and sentence, the ones that need
you pinned on top; filters by status, account and platform; each post's buttons from where it is, Cancel
asking first; the log with its screenshots; Pause posting in the header, kept across a reload, with its
banner; the rail's Needs you count; a clip's card saying where it went; and all of it kept up to date by the
engine's events. Nothing runs and nothing leaves 127.0.0.1: the engine's jobs never start here."""

from __future__ import annotations

import io
import json
import re

import pytest
from PIL import Image
from playwright.sync_api import Page, expect

from clipforge import repo
from clipforge.post import queue, store
from clipforge.post.models import PostOptions
from clipforge.trends.models import PostSound
from tests.fakes import Clock, fake_engine
from tests.posting import add_account, add_clip, at, custom
from tests.ui.conftest import live_server
from tests.ui.seed import hook, picture, seed_project
from tests.ui.test_shell import opened, toasts

NOW = at(6)  # 6:00 am on Tue 3 Nov 2026 in Sydney
LOGIN = "TikTok logged you out. Press Log in, then Retry once you're in."
BLOCKED = "Instagram didn't take the video: it is too long for a reel."
BANNER = "Posting is paused. Nothing goes out until you turn it back on."
NOT_A_LINK = "That isn't a web link. Paste the post's address from the site."
LINK = "https://www.tiktok.com/@tt_one/video/7311111111111111111"
REFUSED = "Instagram turned this post down: The audio isn't available"
SOUND = PostOptions(sound=PostSound(source="ig_trending", external_id="a1", title="Late Train"))


@pytest.fixture
def engine():
    e = fake_engine(now=Clock(NOW))
    e.thumb_fetch = picture
    yield e
    e.close()


@pytest.fixture
def window(engine, page, monkeypatch):
    """The window over ``engine`` with nothing running, its clock at the engine's, so Today is the same day
    for both."""
    monkeypatch.setattr(engine.runner, "start", lambda: None)
    page.clock.set_fixed_time(NOW)
    with live_server(engine) as live:
        yield live


@pytest.fixture
def accounts(engine):
    return {
        "tt_one": add_account(engine, "tiktok", "tt_one"),
        "tt_two": add_account(engine, "tiktok", "tt_two"),
        "ig_one": add_account(engine, "instagram", "ig_one"),
    }


def scheduled(engine, account, hour: int, minute: int = 0, *, day: int = 3):
    """A post of a new clip ("The deadline") to ``account`` at that time, as Confirm and post leaves it."""
    return custom(engine, add_clip(engine), account, at(hour, minute, day=day))


def becomes(engine, post, status: str, **fields):
    """The post moved on as its route would move it, everyone listening told."""
    if status in ("uploading", "processing", "posted") and post.status == "scheduled":
        post = queue.move_post(engine, post.id, "uploading")
    if status == "posted" and post.status == "uploading":
        return queue.move_post(engine, post.id, "posted", **fields)
    if status == post.status:
        return queue.change_post(engine, post.id, **fields) if fields else post
    return queue.move_post(engine, post.id, status, **fields)


def queue_page(page: Page, window) -> None:
    opened(page, f"{window.url}/queue")
    expect(page.get_by_role("heading", name="Queue", level=1)).to_be_visible()


def row(page: Page, name: str):
    return page.get_by_role("article", name=f"The deadline to {name}", exact=True)


def names(region) -> list[str]:
    return [a.get_attribute("aria-label") for a in region.get_by_role("article").all()]


def test_queue_pins_needs_you_with_its_sentence(page, window, engine, accounts):
    first = scheduled(engine, accounts["tt_one"], 19)
    scheduled(engine, accounts["ig_one"], 20)
    waiting = becomes(engine, scheduled(engine, accounts["tt_two"], 21), "needs_you", error=LOGIN)
    queue_page(page, window)

    pinned = page.get_by_role("region", name="Needs you")
    expect(pinned.get_by_role("article")).to_have_count(1)
    card = pinned.get_by_role("article", name="The deadline to @tt_two")
    expect(card).to_contain_text("Needs you")
    expect(card).to_contain_text(LOGIN)
    expect(card).to_contain_text("Today 9:00 pm")
    expect(card).to_contain_text("TikTok")
    expect(card.get_by_role("link", name="The deadline")).to_have_attribute(
        "href", f"/clips/{waiting.clip_id}"
    )

    rest = page.get_by_role("region", name="Up next")
    expect(rest.get_by_role("article")).to_have_count(2)
    assert names(rest) == ["The deadline to @tt_one", "The deadline to @ig_one"]
    expect(row(page, "@tt_one")).to_contain_text("Scheduled")
    expect(row(page, "@tt_one")).to_contain_text("Today 7:00 pm")
    expect(row(page, "@tt_one")).to_contain_text(first.caption.splitlines()[0])

    becomes(engine, waiting, "cancelled")
    expect(pinned).to_have_count(0)  # the section goes once nothing needs you


def test_queue_filters(page, window, engine, accounts):
    scheduled(engine, accounts["tt_one"], 19)
    scheduled(engine, accounts["ig_one"], 20)
    becomes(
        engine, scheduled(engine, accounts["tt_two"], 7, day=4), "posted", url=LINK, posted_at=NOW.timestamp()
    )
    becomes(engine, scheduled(engine, accounts["ig_one"], 12, day=4), "failed", error=BLOCKED)
    queue_page(page, window)
    rest = page.get_by_role("main")
    expect(rest.get_by_role("article")).to_have_count(4)

    page.get_by_label("Platform").select_option(label="Instagram")
    expect(rest.get_by_role("article")).to_have_count(2)
    page.get_by_label("Status").select_option(label="Failed")
    expect(rest.get_by_role("article")).to_have_count(1)
    expect(row(page, "@ig_one")).to_contain_text(BLOCKED)

    page.reload()  # the filters are in the address, so they stay
    expect(page.get_by_label("Status")).to_have_value("failed")
    expect(rest.get_by_role("article")).to_have_count(1)

    page.get_by_label("Platform").select_option(label="TikTok")
    expect(page.get_by_text("No posts match these filters.")).to_be_visible()
    page.get_by_role("button", name="Clear the filters").click()
    expect(rest.get_by_role("article")).to_have_count(4)

    page.get_by_label("Account").select_option(label="@tt_two")
    expect(rest.get_by_role("article")).to_have_count(1)
    card = row(page, "@tt_two")
    expect(card).to_contain_text("Posted")
    expect(card.get_by_role("link", name="Open the post")).to_have_attribute("href", LINK)


def test_cancel_asks_first(page, window, engine, accounts):
    post = scheduled(engine, accounts["tt_one"], 19)
    queue_page(page, window)
    card = row(page, "@tt_one")

    card.get_by_role("button", name="Cancel post").click()
    ask = page.get_by_role("dialog", name="Cancel this post?")
    expect(ask).to_be_visible()
    ask.get_by_role("button", name="Keep it").click()
    expect(ask).to_have_count(0)
    assert store.get_post(engine.db(), post.id).status == "scheduled"

    card.get_by_role("button", name="Cancel post").click()
    page.get_by_role("dialog", name="Cancel this post?").get_by_role("button", name="Cancel the post").click()
    expect(card).to_contain_text("Cancelled")
    expect(card.get_by_role("button", name="Cancel post")).to_have_count(0)
    assert store.get_post(engine.db(), post.id).status == "cancelled"


def test_scheduled_on_tiktok_says_how_to_cancel(page, window, engine, accounts):
    """A post TikTok Studio holds says so, says what cancelling or moving it means, and Cancel says that
    ClipForge only stops following it (SPEC 10.3)."""
    post = becomes(engine, scheduled(engine, accounts["tt_one"], 19), "uploading")
    queue.move_post(engine, post.id, "scheduled_on_tiktok", state=post.state | {"on_tiktok": True})
    queue_page(page, window)
    card = row(page, "@tt_one")

    expect(card).to_contain_text("Scheduled on TikTok")
    expect(card).to_contain_text("Cancelling or moving it means deleting it in TikTok Studio by hand.")
    expect(card.get_by_role("button", name="Reschedule")).to_have_count(0)
    expect(card.get_by_role("button", name="Retry")).to_have_count(0)
    card.get_by_role("button", name="Cancel post").click()
    ask = page.get_by_role("dialog", name="Cancel this post?")
    expect(ask).to_contain_text(
        "This post is already scheduled on TikTok. Cancelling here only stops ClipForge following it, "
        "so delete it in TikTok Studio too."
    )
    ask.get_by_role("button", name="Cancel the post").click()
    expect(card).to_contain_text("Cancelled")
    expect(card).not_to_contain_text("deleting it in TikTok Studio")
    assert store.get_post(engine.db(), post.id).status == "cancelled"


def test_reschedule(page, window, engine, accounts, console):
    console.allow(r"status of 422")  # the refused time, which Chrome logs
    post = scheduled(engine, accounts["tt_one"], 19)
    queue_page(page, window)
    card = row(page, "@tt_one")

    card.get_by_role("button", name="Reschedule").click()
    dialog = page.get_by_role("dialog", name="Reschedule the post")
    field = dialog.get_by_label("New time")
    expect(field).to_have_value("2026-11-03T19:00")
    field.fill("")
    dialog.get_by_role("button", name="Save").click()
    expect(dialog.get_by_text("Pick a date and time.")).to_be_visible()
    assert store.get_post(engine.db(), post.id).scheduled_at == post.scheduled_at
    field.fill("2026-11-04T03:00")  # the account's rules hold here as in the Post dialog
    dialog.get_by_role("button", name="Save").click()
    expect(
        dialog.get_by_text(
            "Wed 4 Nov, 3:00 am is outside the posting windows. The next free time is Wed 4 Nov, 7:00 am."
        )
    ).to_be_visible()
    assert store.get_post(engine.db(), post.id).scheduled_at == post.scheduled_at

    field.fill("2026-11-04T08:30")
    dialog.get_by_role("button", name="Save").click()
    expect(dialog).to_have_count(0)
    expect(card).to_contain_text("Tomorrow 8:30 am")
    assert store.get_post(engine.db(), post.id).scheduled_at == at(8, 30, day=4).timestamp()


def test_times_follow_the_posting_time_zone(page, window, engine, accounts):
    """Settings' Time zone moves the engine's windows and caps, so the window shows its times in it too."""
    posting = engine.settings.posting.model_copy(update={"timezone": "Asia/Bangkok"})
    engine.update_settings(engine.settings.model_copy(update={"posting": posting}))
    post = scheduled(engine, accounts["tt_one"], 22)  # 10:00 pm in Sydney is 6:00 pm in Bangkok
    queue_page(page, window)
    card = row(page, "@tt_one")
    expect(card).to_contain_text("Today 6:00 pm")

    card.get_by_role("button", name="Reschedule").click()
    dialog = page.get_by_role("dialog", name="Reschedule the post")
    expect(dialog.get_by_text("The time is in Asia/Bangkok.")).to_be_visible()
    field = dialog.get_by_label("New time")
    expect(field).to_have_value("2026-11-03T18:00")
    field.fill("2026-11-03T21:00")  # in Bangkok's 6 to 10 pm window
    dialog.get_by_role("button", name="Save").click()
    expect(dialog).to_have_count(0)
    expect(card).to_contain_text("Today 9:00 pm")
    # 9:00 pm in Bangkok is 1:00 am the next day in Sydney.
    assert store.get_post(engine.db(), post.id).scheduled_at == at(1, day=4).timestamp()


def test_edit_caption_only_while_scheduled(page, window, engine, accounts, console):
    console.allow(r"status of 422")  # the refused values, which Chrome logs
    post = scheduled(engine, accounts["ig_one"], 19)
    becomes(engine, scheduled(engine, accounts["tt_one"], 20), "failed", error=BLOCKED)
    becomes(engine, scheduled(engine, accounts["tt_two"], 21), "needs_you", error=LOGIN)
    queue_page(page, window)
    card = row(page, "@ig_one")

    card.get_by_role("button", name="Edit caption").click()
    dialog = page.get_by_role("dialog", name="Edit the caption")
    caption = dialog.get_by_label("Caption")
    expect(caption).to_have_value(post.caption)
    caption.fill("x" * 2300)
    expect(dialog.get_by_text(re.compile(r"^\d+ of 2200$"))).to_be_visible()
    dialog.get_by_role("button", name="Save").click()
    expect(dialog.get_by_text(re.compile("over the 2200 Instagram takes"))).to_be_visible()
    assert store.get_post(engine.db(), post.id).caption == post.caption

    caption.fill("A new caption for the reel")
    dialog.get_by_label("Hashtags").fill("#money focus")
    dialog.get_by_role("button", name="Save").click()
    expect(dialog).to_have_count(0)
    expect(card).to_contain_text("A new caption for the reel")
    saved = store.get_post(engine.db(), post.id)
    assert (saved.caption, saved.hashtags) == ("A new caption for the reel", ["money", "focus"])

    for name in ("@tt_one", "@tt_two"):
        expect(row(page, name).get_by_role("button", name="Edit caption")).to_have_count(0)
    becomes(engine, store.get_post(engine.db(), post.id), "posted", url=None)
    expect(card).to_contain_text("Posted")
    expect(card.get_by_role("button", name="Edit caption")).to_have_count(0)


def test_retry_and_mark_as_posted(page, window, engine, accounts, console):
    console.allow(r"status of 422")  # the refused values, which Chrome logs
    failed = becomes(engine, scheduled(engine, accounts["ig_one"], 19), "failed", error=BLOCKED)
    waiting = becomes(engine, scheduled(engine, accounts["tt_one"], 20), "needs_you", error=LOGIN)
    queue_page(page, window)

    failing = row(page, "@ig_one")
    failing.get_by_role("button", name="Retry").click()
    expect(toasts(page)).to_contain_text(
        re.compile(r"Scheduled again for (Today|Tomorrow) \d{1,2}:\d{2} [ap]m")
    )
    expect(failing).to_contain_text("Scheduled")
    expect(failing).not_to_contain_text(BLOCKED)
    assert store.get_post(engine.db(), failed.id).status == "scheduled"

    card = row(page, "@tt_one")
    card.get_by_role("button", name="Mark as posted").click()
    dialog = page.get_by_role("dialog", name="Mark as posted")
    dialog.get_by_label("The post's link").fill("my video")
    dialog.get_by_role("button", name="Mark as posted").click()
    expect(dialog.get_by_text(NOT_A_LINK)).to_be_visible()
    assert store.get_post(engine.db(), waiting.id).status == "needs_you"

    dialog.get_by_label("The post's link").fill(LINK)
    dialog.get_by_role("button", name="Mark as posted").click()
    expect(dialog).to_have_count(0)
    posted = page.get_by_role("region", name="Done").get_by_role("article", name="The deadline to @tt_one")
    expect(posted).to_contain_text("Posted")
    expect(posted.get_by_role("link", name="Open the post")).to_have_attribute("href", LINK)
    assert store.get_post(engine.db(), waiting.id).status == "posted"
    expect(page.get_by_role("region", name="Needs you")).to_have_count(0)


def png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (320, 180), (20, 160, 120)).save(out, "PNG")
    return out.getvalue()


def test_log_shows_steps_and_screenshots(page, window, engine, accounts):
    post = becomes(engine, scheduled(engine, accounts["tt_one"], 19), "needs_you", error=LOGIN)
    quiet = scheduled(engine, accounts["ig_one"], 20)
    folder = engine.paths.post_logs / post.id
    folder.mkdir(parents=True)
    steps = [
        {"step": "open", "at": at(5, 50).timestamp(), "url": "https://www.tiktok.com/tiktokstudio/upload"},
        {"step": "login_wall", "at": at(5, 51).timestamp(), "seen": "Log in to TikTok"},
    ]
    (folder / "steps.jsonl").write_text("".join(json.dumps(s) + "\n" for s in steps))
    (folder / "01-login_wall.png").write_bytes(png())
    queue_page(page, window)

    row(page, "@tt_one").get_by_role("button", name="Log").click()
    log = page.get_by_role("dialog", name="The deadline to @tt_one: the log")
    items = log.get_by_role("list", name="Steps").get_by_role("listitem")
    expect(items).to_have_count(2)
    expect(items.nth(0)).to_contain_text("open")
    expect(items.nth(0)).to_contain_text("5:50:00 am")
    expect(items.nth(1)).to_contain_text("login_wall")
    expect(items.nth(1)).to_contain_text("seen: Log in to TikTok")
    shot = log.get_by_role("img", name="Screenshot 1: login_wall")
    expect(shot).to_be_visible()
    assert shot.evaluate("img => img.complete && img.naturalWidth") == 320
    log.get_by_role("button", name="Close").click()

    row(page, "@ig_one").get_by_role("button", name="Log").click()
    empty = page.get_by_role("dialog", name="The deadline to @ig_one: the log")
    expect(empty.get_by_text("Nothing logged yet.")).to_be_visible()
    assert quiet.status == "scheduled"


def test_bring_to_front_only_while_a_browser_post_uploads(page, window, engine, accounts, monkeypatch):
    asked = []
    monkeypatch.setattr(engine.browser, "request_front", lambda: asked.append(True))
    becomes(engine, scheduled(engine, accounts["tt_one"], 19), "uploading")
    becomes(engine, scheduled(engine, accounts["ig_one"], 20), "uploading")
    scheduled(engine, accounts["tt_two"], 21)
    queue_page(page, window)
    front = "Bring the window to the front"

    expect(row(page, "@ig_one").get_by_role("button", name=front)).to_have_count(0)
    expect(row(page, "@tt_two").get_by_role("button", name=front)).to_have_count(0)
    row(page, "@tt_one").get_by_role("button", name=front).click()
    expect(toasts(page)).to_contain_text("Bringing the window to the front.")
    assert asked == [True]


def test_pause_posting_switch_survives_a_reload(page, window, engine):
    opened(page, window.url)
    switch = page.get_by_role("banner").get_by_role("switch", name="Pause posting")
    expect(switch).not_to_be_checked()
    expect(page.get_by_text(BANNER)).to_have_count(0)

    switch.check()
    expect(page.get_by_text(BANNER)).to_be_visible()
    assert engine.settings.posting.paused is True

    page.reload()
    expect(switch).to_be_checked()
    expect(page.get_by_text(BANNER)).to_be_visible()
    page.get_by_role("link", name="Settings").click()  # it stays on every page
    expect(page.get_by_text(BANNER)).to_be_visible()

    switch.uncheck()
    expect(page.get_by_text(BANNER)).to_have_count(0)
    assert engine.settings.posting.paused is False


def test_a_logged_out_post_leads_with_log_in(page, window, engine, accounts):
    """The post's own buttons start with what its sentence asks for: Log in, then Retry, and Mark as posted
    only after them. Logging in starts the account's login, and once it is back, Log in goes."""
    tt_two = accounts["tt_two"]
    becomes(engine, scheduled(engine, tt_two, 21), "needs_you", error=LOGIN)
    store.update_account(engine.db(), tt_two.id, status="needs_login")
    queue_page(page, window)
    card = row(page, "@tt_two")
    expect(card.get_by_role("button")).to_have_text(
        ["Log in", "Retry", "Mark as posted", "Reschedule", "Export", "Cancel post", "Log"]
    )
    expect(card.get_by_role("button", name="Log in")).to_have_class(re.compile(r"\bbg-accent-strong\b"))
    expect(card.get_by_role("button", name="Retry")).not_to_have_class(re.compile(r"\bbg-accent-strong\b"))
    card.get_by_role("button", name="Log in").click()
    expect(toasts(page)).to_contain_text("Log in to TikTok in the Chrome window that opens.")
    (job,) = [j for j in engine.runner.jobs_for(tt_two.id) if j.kind == "account.login"]
    assert job.status == "queued"
    queue.change_account(engine, tt_two.id, status="connected")
    expect(card.get_by_role("button", name="Log in")).to_have_count(0)
    expect(card.get_by_role("button").first).to_have_text("Retry")


def test_rail_counts_needs_you(page, window, engine, accounts):
    one = becomes(engine, scheduled(engine, accounts["tt_one"], 19), "needs_you", error=LOGIN)
    two = becomes(engine, scheduled(engine, accounts["tt_two"], 20), "needs_you", error=LOGIN)
    opened(page, window.url)
    rail = page.get_by_role("navigation", name="Pages")

    expect(rail.get_by_role("link", name="Queue, 2 need you")).to_be_visible()
    becomes(engine, one, "posted", url=LINK)
    expect(rail.get_by_role("link", name="Queue, 1 needs you")).to_be_visible()
    becomes(engine, two, "cancelled")
    expect(rail.get_by_role("link", name="Queue", exact=True)).to_be_visible()
    rail.get_by_role("link", name="Queue", exact=True).click()
    expect(page).to_have_url(re.compile(r"/queue$"))


def test_failed_posts_are_pinned_and_counted(page, window, engine, accounts):
    """A failed post stays with the ones that need you, counted in the rail, until you deal with it; one a
    retry can't fix leads with Export and has no Retry or Reschedule."""
    becomes(
        engine,
        scheduled(engine, accounts["tt_one"], 18, day=4),
        "posted",
        url=LINK,
        posted_at=NOW.timestamp(),
    )
    scheduled(engine, accounts["tt_two"], 19)
    busy = becomes(
        engine,
        scheduled(engine, accounts["tt_one"], 7, day=4),
        "failed",
        error="TikTok is busy.",
        error_code="retries_used",
    )
    becomes(
        engine,
        scheduled(engine, accounts["ig_one"], 12, day=4),
        "failed",
        error=BLOCKED,
        error_code="rejected",
    )
    queue_page(page, window)

    regions = page.get_by_role("main").get_by_role("region")
    expect(regions).to_have_count(3)
    assert [r.locator("h2").inner_text() for r in regions.all()] == [
        "Needs you",
        "Up next",
        "Done",
    ]
    pinned = page.get_by_role("region", name="Needs you")
    assert names(pinned) == ["The deadline to @ig_one", "The deadline to @tt_one"]  # newest failure first
    assert names(page.get_by_role("region", name="Up next")) == ["The deadline to @tt_two"]
    assert names(page.get_by_role("region", name="Done")) == ["The deadline to @tt_one"]
    rail = page.get_by_role("navigation", name="Pages")
    expect(rail.get_by_role("link", name="Queue, 2 need you")).to_be_visible()

    turned_down = pinned.get_by_role("article", name="The deadline to @ig_one")
    expect(turned_down.get_by_role("button")).to_have_text(["Export", "Cancel post", "Log"])  # no Reschedule
    expect(
        pinned.get_by_role("article", name="The deadline to @tt_one").get_by_role("button").first
    ).to_have_text("Retry")
    becomes(engine, busy, "cancelled")
    expect(rail.get_by_role("link", name="Queue, 1 needs you")).to_be_visible()
    expect(pinned.get_by_role("article")).to_have_count(1)


def test_a_post_can_go_without_its_instagram_sound(page, window, engine, accounts):
    """A post with an Instagram sound offers Post without the sound, first on one Instagram turned down. It
    asks, then a scheduled post keeps its time and a refused one is scheduled again, each without it."""
    ahead = scheduled(engine, accounts["ig_one"], 12, day=5)
    queue.change_post(engine, ahead.id, options=SOUND)
    refused = becomes(
        engine,
        scheduled(engine, accounts["ig_one"], 12, day=4),
        "failed",
        error=REFUSED,
        error_code="rejected",
        options=SOUND,
    )
    queue_page(page, window)

    up_next = page.get_by_role("region", name="Up next").get_by_role(
        "article", name="The deadline to @ig_one"
    )
    expect(up_next.get_by_role("button")).to_have_text(
        ["Edit caption", "Post without the sound", "Reschedule", "Export", "Cancel post", "Log"]
    )
    up_next.get_by_role("button", name="Post without the sound").click()
    ask = page.get_by_role("dialog", name="Post without the sound?")
    expect(ask).to_contain_text("It goes up at its time with the clip's own audio only")
    ask.get_by_role("button", name="Post without it").click()
    expect(toasts(page)).to_contain_text("The sound is off this post")
    expect(up_next.get_by_role("button", name="Post without the sound")).to_have_count(0)
    kept = store.get_post(engine.db(), ahead.id)
    assert (kept.options.sound, kept.scheduled_at) == (None, ahead.scheduled_at)

    turned_down = page.get_by_role("region", name="Needs you").get_by_role(
        "article", name="The deadline to @ig_one"
    )
    expect(turned_down.get_by_role("button")).to_have_text(
        ["Post without the sound", "Export", "Cancel post", "Log"]
    )
    turned_down.get_by_role("button", name="Post without the sound").click()
    ask = page.get_by_role("dialog", name="Post without the sound?")
    expect(ask).to_contain_text("It's scheduled again at the next free slot")
    expect(ask).to_contain_text("Instagram doesn't add Late Train.")
    ask.get_by_role("button", name="Keep the sound").click()
    expect(ask).to_have_count(0)
    assert store.get_post(engine.db(), refused.id).options.sound == SOUND.sound

    turned_down.get_by_role("button", name="Post without the sound").click()
    page.get_by_role("dialog", name="Post without the sound?").get_by_role(
        "button", name="Post without it"
    ).click()
    expect(toasts(page)).to_contain_text(re.compile(r"Scheduled again for .+, without the sound"))
    expect(page.get_by_role("region", name="Needs you")).to_have_count(0)
    again = store.get_post(engine.db(), refused.id)
    assert (again.status, again.options.sound, again.error_code) == ("scheduled", None, None)


def test_clip_card_shows_posted_to(page, window, engine, accounts):
    pid = seed_project(engine, clips=2, rendered=2)
    clip = repo.get_clip(engine.db(), f"{pid}_c0")
    for name in ("tt_one", "ig_one"):
        becomes(engine, custom(engine, clip, accounts[name], at(19)), "posted", url=None)
    custom(engine, repo.get_clip(engine.db(), f"{pid}_c1"), accounts["tt_two"], at(20))  # not up yet
    opened(page, f"{window.url}/projects/{pid}")

    expect(page.get_by_role("article", name=hook(0), exact=True)).to_contain_text("Posted to 2")
    expect(page.get_by_role("article", name=hook(1), exact=True)).not_to_contain_text("Posted to")


def test_queue_updates_live_from_events(page, window, engine, accounts):
    post = scheduled(engine, accounts["tt_one"], 19)
    queue_page(page, window)
    card = row(page, "@tt_one")
    expect(card).to_contain_text("Scheduled")

    post = becomes(engine, post, "uploading")
    expect(card).to_contain_text("Uploading")
    becomes(engine, post, "posted", url=LINK)
    expect(card).to_contain_text("Posted")
    expect(card.get_by_role("link", name="Open the post")).to_have_attribute("href", LINK)

    scheduled(engine, accounts["ig_one"], 20)  # a new batch, confirmed elsewhere
    expect(row(page, "@ig_one")).to_contain_text("Scheduled")
    becomes(
        engine, store.list_posts(engine.db(), account_id=accounts["ig_one"].id)[0], "needs_you", error=LOGIN
    )
    expect(page.get_by_role("region", name="Needs you").get_by_role("article")).to_have_count(1)


def test_an_empty_queue_says_how_to_fill_it(page, window):
    queue_page(page, window)
    expect(page.get_by_text("Nothing is queued yet. Pick clips on a project and press Post.")).to_be_visible()
    page.get_by_role("main").get_by_role("link", name="Projects").click()
    expect(page).to_have_url(re.compile(r"/projects$"))
