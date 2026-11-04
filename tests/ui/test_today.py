"""Today, the home page (SPEC 14.1, 14.9 and 14.10 item 3, phase 5a): with no clipping channels, Create's drop
zone and a way to make one; with clipping channels and no drafts, when the next check runs; with drafts,
Clips to review, grouped by clipping channel and then source video, each card with its clip playing on
hover, score, account, caption editable in place, slot and warnings, ticked at first, Approve and post on
the ticked ones through the Post dialog's review, and Discard with its Undo. Above them (14.10 items 1 and
2, phase 5c), the minutes left out of 15 and the To do list, highest value first, each item with its tick
and its action, which starts its timer; below the review (items 4 and 5), the exported posts to put up
by hand and the live posts to submit, each copying what it needs and opening the page it goes to; then
(items 6, 7 and 8) each running campaign's rules and each account's posts today, Money by marketplace with
Withdraw and I've been paid, and yesterday's best posts by what they gained. Last, the rehearsal (phase
5c Task 13): a whole morning done by clicks, each item off the list and its time kept. Nothing leaves
127.0.0.1: the engine's jobs never start here and ``open`` is recorded, never run."""

from __future__ import annotations

import re
import shutil

import pytest
from playwright.sync_api import Page, expect

from clipforge import repo
from clipforge.campaigns import store as campaigns
from clipforge.campaigns import tracker
from clipforge.campaigns.models import Metric, Required
from clipforge.config import Marketplace
from clipforge.post import store
from clipforge.post.plan import posts_today
from clipforge.today import day
from clipforge.today import store as today_store
from clipforge.today.models import Need
from clipforge.watch import drafts
from clipforge.watch import store as watch_store
from tests.fakes import Clock, FakePublisher, fake_engine
from tests.posting import PACK, add_account, add_campaign, add_clip, at, custom, linked_clip, posted
from tests.ui.conftest import live_server
from tests.ui.seed import picture
from tests.ui.test_shell import PASTE, opened, toasts
from tests.watching import add_channel, add_check, watched

NOW = at(7, 10)  # 7:10 am on Tue 3 Nov 2026 in Sydney
TALK, LEVEL = "Why we wait", "The last level"
NEXT = "No clips to review. The next check is at 6:00 am tomorrow (Podcasts)."
NONE = "No clips to review. No check is coming up: each clipping channel is paused or has nothing to watch."
EMPTY = "Nothing to do right now. New drafts and posts show up here as they come."
SUBMIT = "https://whop.com/beast/submit"
LIVE = "https://www.tiktok.com/@t0/video/7300000000000000001"
TIKTOK_UPLOAD = "https://www.tiktok.com/tiktokstudio/upload?from=upload&lang=en"
YOUTUBE = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
CLIPBOARD = ["clipboard-read", "clipboard-write"]
READ_CLIPBOARD = "() => navigator.clipboard.readText()"
PAGE = "https://whop.com/beast"
WITHDRAW = "https://whop.com/withdraw"
PRIMARY = re.compile(r"\bbg-accent-strong\b")


@pytest.fixture
def engine():
    e = fake_engine(now=Clock(NOW), publishers={"tiktok:browser": FakePublisher()})
    e.thumb_fetch = picture
    yield e
    e.close()


@pytest.fixture
def window(engine, page, monkeypatch):
    """The window over ``engine`` with nothing running, its clock at the engine's."""
    monkeypatch.setattr(engine.runner, "start", lambda: None)
    page.clock.set_fixed_time(NOW)
    with live_server(engine) as live:
        yield live


def drafted(engine, channel, hook: str, score: float, video) -> list:
    """A video the clipping channel checked, its one clip drafted to each of its accounts, the clip's
    render a video Chrome plays."""
    packaging = PACK | {"hook_title": hook}
    pid, (clip,) = watched(engine, channel, add_check(engine, channel), [score], packaging=packaging)
    for render in repo.list_renders(engine.db(), clip.id):  # where the window's media route serves it from
        served = engine.paths.home / "projects" / pid / "clips" / clip.id / f"v{render.version}" / "clip.mp4"
        served.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(video, served)
        engine.db().execute("UPDATE renders SET path = ? WHERE id = ?", (str(served), render.id))
    drafts.settle(engine, pid)
    return [p for p in drafts.live_drafts(engine) if p.clip_id == clip.id]


@pytest.fixture
def two(engine, video):
    """Podcasts with a TikTok and an Instagram account and one video drafted to both, then Gaming with a
    TikTok account and one video: three drafts, by their account's handle."""
    t0 = add_account(engine, "tiktok", "t0", daily_cap=1)
    i0 = add_account(engine, "instagram", "i0", daily_cap=1)
    t1 = add_account(engine, "tiktok", "t1", daily_cap=1)
    podcasts = add_channel(engine, [t0, i0], "Podcasts")
    gaming = add_channel(engine, [t1], "Gaming")
    made = drafted(engine, podcasts, TALK, 90, video) + drafted(engine, gaming, LEVEL, 70, video)
    handles = {a.id: a.handle for a in (t0, i0, t1)}
    return {handles[p.account_id]: p for p in made}


def review(page: Page):
    return page.get_by_role("region", name="Clips to review")


def card(page: Page, name: str):
    return page.get_by_role("article", name=name, exact=True)


def approve_dialog(page: Page):
    return page.get_by_role("dialog", name=re.compile(r"^Approve \d+ drafts?$"))


def test_today_without_channels_shows_the_drop_zone(page, window):
    opened(page, window.url)
    expect(page.get_by_role("heading", name="Today", level=1)).to_be_visible()
    expect(page.get_by_role("region", name="Start a project")).to_be_visible()
    expect(page.get_by_label("Link or file path")).to_be_visible()
    new = page.get_by_role("link", name="New clipping channel")
    expect(new).to_have_attribute("href", "/channels?new=1")
    expect(review(page)).to_have_count(0)


def test_today_lists_drafts_grouped(page, engine, window, two):
    opened(page, window.url)
    expect(page.get_by_role("heading", name="Today", level=1)).to_be_visible()
    expect(page.get_by_role("region", name="Start a project")).to_have_count(0)
    shown = review(page)
    expect(shown).to_contain_text("3 drafts")
    groups = shown.get_by_role("group")
    expect(groups).to_have_count(2)
    expect(groups.nth(0).get_by_role("heading", level=3)).to_have_text("Podcasts")
    expect(groups.nth(1).get_by_role("heading", level=3)).to_have_text("Gaming")
    expect(groups.nth(0).get_by_role("heading", level=4)).to_have_text("A talk")
    expect(groups.nth(0).get_by_role("checkbox", name="Select all in Podcasts")).to_be_checked()

    names = [f"{TALK} to @t0", f"{TALK} to @i0", f"{LEVEL} to @t1"]
    for name in names:
        expect(card(page, name).get_by_role("checkbox", name=f"Select {name}")).to_be_checked()
    talk = card(page, names[0])
    draft = two["t0"]
    expect(talk).to_contain_text("90")
    expect(talk).to_contain_text("@t0")
    expect(talk.get_by_label("Caption")).to_have_value(draft.caption)
    expect(talk.get_by_role("link", name="Edit clip")).to_have_attribute("href", f"/clips/{draft.clip_id}")
    expect(talk.locator("[data-slot]")).to_have_text(re.compile(r"^(Today|Tomorrow) \d{1,2}:\d{2} (am|pm)$"))
    expect(shown.get_by_role("button", name="Approve and post")).to_be_enabled()

    # The clip plays on hover, muted until its sound is turned on.
    expect(talk.locator("video")).to_have_count(0)
    talk.hover()
    clip = talk.locator("video")
    expect(clip).to_have_count(1)
    assert clip.evaluate("v => v.muted") is True
    talk.get_by_role("button", name="Turn the sound on").click()
    expect(talk.get_by_role("button", name="Turn the sound off")).to_be_visible()
    assert clip.evaluate("v => v.muted") is False


def test_each_video_shows_its_thumbnail(page, engine, window, two):
    pid = repo.get_clip(engine.db(), two["t0"].clip_id).project_id
    pp = engine.paths.project(pid)
    pp.ensure()
    pp.thumb.write_bytes(picture())
    opened(page, window.url)
    thumb = (
        review(page).get_by_role("group").nth(0).locator(f'img[src="/media/projects/{pid}/source/thumb.jpg"]')
    )
    expect(thumb).to_have_count(1)
    expect(thumb).to_have_attribute("alt", "")


def test_untick_and_approve_opens_review_on_the_rest(page, engine, window, two):
    opened(page, window.url)
    name = f"{TALK} to @i0"
    card(page, name).get_by_role("checkbox", name=f"Select {name}").uncheck()
    podcasts = review(page).get_by_role("checkbox", name="Select all in Podcasts")
    assert podcasts.evaluate("box => box.indeterminate") is True
    expect(review(page)).to_contain_text("2 of 3 drafts ticked")
    review(page).get_by_role("button", name="Approve and post").click()
    dialog = approve_dialog(page)
    expect(dialog).to_have_accessible_name("Approve 2 drafts")
    rows = dialog.get_by_role("table", name="Posts").locator("tbody tr")
    expect(rows).to_have_count(2)
    expect(dialog.get_by_role("table", name="Posts")).not_to_contain_text("@i0")
    expect(dialog.get_by_role("button", name="Back")).to_have_count(0)
    expect(dialog.get_by_role("list", name="Steps")).to_have_count(0)
    expect(dialog.get_by_role("button", name="Confirm and post")).to_be_enabled()

    # A group partly ticked ticks all of it, then unticks all of it.
    page.keyboard.press("Escape")
    expect(dialog).to_have_count(0)
    podcasts.check()
    expect(review(page)).to_contain_text("3 drafts")
    podcasts.uncheck()
    expect(review(page)).to_contain_text("1 of 3 drafts ticked")
    for every in review(page).get_by_role("checkbox").all():
        every.uncheck()
    expect(review(page).get_by_role("button", name="Approve and post")).to_be_disabled()


def test_confirm_schedules_the_drafts(page, engine, window, two):
    opened(page, window.url)
    review(page).get_by_role("button", name="Approve and post").click()
    dialog = approve_dialog(page)
    expect(dialog).to_have_accessible_name("Approve 3 drafts")
    dialog.get_by_role("button", name="Confirm and post").click()
    expect(page).to_have_url(re.compile(r"/queue$"))
    expect(toasts(page)).to_contain_text("3 drafts approved")
    posts = store.list_posts(engine.db())
    assert sorted(p.id for p in posts) == sorted(p.id for p in two.values())
    assert {p.status for p in posts} == {"scheduled"}
    page.get_by_role("navigation", name="Pages").get_by_role("link", name="Today", exact=True).click()
    expect(page.get_by_text(re.compile(r"^No clips to review\."))).to_be_visible()


def test_discard_and_undo(page, engine, window, two):
    opened(page, window.url)
    name = f"{LEVEL} to @t1"
    draft = two["t1"]
    card(page, name).get_by_role("button", name="Discard").click()
    expect(card(page, name)).to_have_count(0)
    expect(toasts(page)).to_contain_text("Draft discarded")
    assert store.get_post(engine.db(), draft.id).status == "discarded"
    expect(review(page)).to_contain_text("2 drafts")
    expect(review(page).get_by_role("heading", name="Gaming")).to_have_count(0)  # its only draft went
    toasts(page).get_by_role("button", name="Undo").click()
    expect(card(page, name)).to_be_visible()
    assert store.get_post(engine.db(), draft.id).status == "draft"
    expect(review(page)).to_contain_text("3 drafts")


def test_caption_edit_in_place_saves(page, engine, window, two):
    opened(page, window.url)
    draft = two["t0"]
    caption = card(page, f"{TALK} to @t0").get_by_label("Caption")
    caption.fill("Three words now")
    with page.expect_response(
        lambda r: r.url.endswith(f"/api/posts/{draft.id}") and r.request.method == "PATCH"
    ):
        caption.press("Tab")
    assert store.get_post(engine.db(), draft.id).caption == "Three words now"
    with page.expect_request(re.compile(r"/api/drafts$")):
        page.reload()
    expect(card(page, f"{TALK} to @t0").get_by_label("Caption")).to_have_value("Three words now")

    # Leaving it unchanged sends nothing.
    sent: list[str] = []
    page.on("request", lambda r: sent.append(r.url) if r.method == "PATCH" else None)
    caption = card(page, f"{TALK} to @t0").get_by_label("Caption")
    caption.focus()
    caption.press("Tab")
    page.wait_for_timeout(300)
    assert sent == []


def test_no_drafts_says_next_check(page, engine, window):
    podcasts = add_channel(engine, [add_account(engine, "tiktok", "t0")], "Podcasts")
    watch_store.update_channel(engine.db(), podcasts.id, last_check_on="2026-11-03")
    opened(page, window.url)
    expect(page.get_by_role("heading", name="Today", level=1)).to_be_visible()
    expect(page.get_by_text(NEXT)).to_be_visible()
    expect(page.get_by_role("region", name="Start a project")).to_have_count(0)

    watch_store.update_channel(engine.db(), podcasts.id, paused=True)
    engine.events.publish("clip_channel", {"id": podcasts.id})
    expect(page.get_by_text(NONE)).to_be_visible()


@pytest.fixture
def found(monkeypatch):
    """What needs you, as the test sets it: a list each refresh of the day's list reads."""
    current: list[Need] = []
    monkeypatch.setattr(day, "needs", lambda engine, today: list(current))
    return current


def todo(page: Page):
    return page.get_by_role("region", name="To do")


def still(page: Page):
    return todo(page).get_by_role("list", name="Still to do").get_by_role("listitem")


def test_todo_list_orders_and_ticks(page, engine, window, found):
    submit = "Submit 3 live posts to Whop (Beast clips)"
    found.extend(
        [
            Need("needs_you", "all", ["p1"], "1 post needs you", value=3.0),
            Need("drafts", "ch1", ["d1", "d2"], "Approve 2 drafts (Podcasts)", value=9.0, guess=True),
            Need("submit", "c1", ["s1", "s2", "s3"], submit, value=12.0),
        ]
    )
    opened(page, window.url)  # no clipping channels, but a list to work through, so no drop zone
    expect(page.get_by_role("region", name="Start a project")).to_have_count(0)
    expect(page.get_by_text("15 of 15 minutes left")).to_be_visible()
    expect(page.get_by_text("The rest of the list needs about 2 minutes.")).to_be_visible()  # 145 s
    rows = still(page)
    expect(rows).to_have_count(3)
    expected = [
        (submit, "Could earn US$12.00", "about 45 s", "Submit"),
        ("Approve 2 drafts (Podcasts)", "Could earn US$9.00 (a guess)", "about 40 s", "Review"),
        ("1 post needs you", "Unblocks US$3.00", "about 1 min", "Open the Queue"),
    ]
    for n, (title, value, estimate, action) in enumerate(expected):
        row = rows.nth(n)
        expect(row.get_by_role("checkbox", name=title, exact=True)).not_to_be_checked()
        expect(row).to_contain_text(value)
        expect(row).to_contain_text(estimate)
        expect(row.get_by_role("link", name=action, exact=True)).to_be_visible()
    expect(rows.nth(0).get_by_role("link", name="Submit", exact=True)).to_have_attribute("href", "#submit")
    expect(rows.nth(2).get_by_role("link", name="Open the Queue")).to_have_attribute("href", "/queue")

    # Started, worked on for 3 minutes and ticked: it folds under Done today and the minutes go down.
    with page.expect_response(lambda r: r.url.endswith("/start") and r.request.method == "POST"):
        rows.nth(0).get_by_role("link", name="Submit", exact=True).click()
    engine.now.advance(minutes=3)
    with page.expect_response(lambda r: r.url.endswith("/tick") and r.request.method == "POST"):
        rows.nth(0).get_by_role("checkbox", name=submit).check()
    expect(rows).to_have_count(2)
    expect(page.get_by_text("12 of 15 minutes left")).to_be_visible()
    expect(page.get_by_text("The rest of the list needs about 2 minutes.")).to_be_visible()  # 100 s
    done = todo(page).get_by_text("Done today (1)")
    expect(done).to_be_visible()
    (ticked,) = [i for i in today_store.items_of(engine.db(), "2026-11-03") if i.done]
    assert (ticked.title, ticked.done_how) == (submit, "hand")

    page.reload()
    expect(still(page)).to_have_count(2)
    expect(still(page).nth(0)).to_contain_text("Approve 2 drafts (Podcasts)")
    todo(page).get_by_text("Done today (1)").click()
    expect(todo(page).get_by_role("list", name="Done today")).to_contain_text(submit)

    # Ticking the rest empties the list.
    for title in ("Approve 2 drafts (Podcasts)", "1 post needs you"):
        still(page).get_by_role("checkbox", name=title).check()
    expect(todo(page)).to_contain_text(EMPTY)
    expect(page.get_by_text("Nothing left on the list.")).to_be_visible()


def test_todo_action_starts_the_timer(page, engine, window, found):
    found.append(Need("needs_you", "all", ["p1"], "1 post needs you", value=3.0))
    opened(page, window.url)
    starts: list[str] = []
    page.on("request", lambda r: starts.append(r.url) if r.url.endswith("/start") else None)
    with page.expect_response(lambda r: r.url.endswith("/start")):
        still(page).nth(0).get_by_role("link", name="Open the Queue").click()
    expect(page).to_have_url(re.compile(r"/queue$"))
    page.wait_for_timeout(300)
    assert len(starts) == 1
    (item,) = today_store.items_of(engine.db(), "2026-11-03")
    assert (item.started_at, item.done) == (NOW.timestamp(), False)


def block(page: Page, name: str):
    return page.get_by_role("region", name=name, exact=True)


def live_post(engine):
    """A post of a Beast clips clip, live on @t0 with its link, waiting to be submitted to Whop."""
    beast = add_campaign(engine, rate_per_1000=2.0, submit_url=SUBMIT)
    live = posted(engine, linked_clip(engine, beast), add_account(engine, "tiktok", "t0"))
    return store.update_post(engine.db(), live.id, url=LIVE)


def test_submit_copies_and_opens_then_marks(page, engine, window):
    page.context.grant_permissions(CLIPBOARD, origin=window.url)
    live = live_post(engine)
    opened(page, window.url)
    title = "Submit 1 live post to Whop (Beast clips)"
    expect(still(page).filter(has_text=title)).to_have_count(1)
    expect(still(page).filter(has_text=title).get_by_role("link", name="Submit")).to_have_attribute(
        "href", "#submit"
    )
    row = block(page, "Submit").get_by_role("article", name=live.clip_title)
    row.get_by_role("button", name="Copy link and open Whop").click()
    expect(toasts(page)).to_contain_text("Link copied. Paste it on Whop, then mark it submitted.")
    assert page.evaluate(READ_CLIPBOARD) == LIVE
    assert ["open", SUBMIT] in engine.run_cmd.calls

    row.get_by_role("button", name="Mark submitted").click()
    expect(block(page, "Submit")).to_have_count(0)  # it needs nothing more of you today
    expect(still(page).filter(has_text=title)).to_have_count(0)  # the To do item ticked itself
    todo(page).get_by_text("Done today (1)").click()
    expect(todo(page).get_by_role("list", name="Done today")).to_contain_text("done in ClipForge")
    assert campaigns.submission_of_post(engine.db(), live.id).status == "submitted"


def test_pasting_a_copied_link_starts_nothing(page, engine, window):
    engine.update_settings(engine.settings.model_copy(update={"allow_other_sites": True}))
    page.context.grant_permissions(CLIPBOARD, origin=window.url)
    live = live_post(engine)
    opened(page, window.url)
    block(page, "Submit").get_by_role("article", name=live.clip_title).get_by_role(
        "button", name="Copy link and open Whop"
    ).click()
    expect(toasts(page)).to_contain_text("Link copied.")
    before = len(engine.projects())  # the campaign clip's own
    starts: list[str] = []
    page.on("request", lambda r: starts.append(r.url) if r.url.endswith("/api/projects") else None)
    page.evaluate(PASTE, [None, LIVE])  # a TikTok link, which counts as one with other sites allowed
    page.wait_for_timeout(500)
    assert (starts, len(engine.projects())) == ([], before)
    page.evaluate(PASTE, [None, YOUTUBE])  # while any other link still starts a project
    page.wait_for_url(re.compile(r"/projects/"))
    assert len(engine.projects()) == before + 1


def test_open_in_tiktok_copies_the_caption_and_shows_the_file(page, engine, window):
    page.context.grant_permissions(CLIPBOARD, origin=window.url)
    p = custom(engine, add_clip(engine), add_account(engine, "tiktok", "t0"), at(19))
    store.update_post(engine.db(), p.id, caption="Watch this", hashtags=["money"])
    folder = engine.posting.export(p.id)
    opened(page, window.url)
    expect(still(page).filter(has_text="exported").get_by_role("link", name="Put up")).to_have_attribute(
        "href", "#post"
    )
    row = block(page, "Post").get_by_role("article", name=p.clip_title)
    expect(row).to_contain_text("@t0 on TikTok")
    with page.expect_response(lambda r: r.url.endswith("/open-upload")):
        row.get_by_role("button", name="Open in TikTok").click()
    expect(toasts(page)).to_contain_text("Caption copied. The clip is shown in Finder.")
    assert page.evaluate(READ_CLIPBOARD) == "Watch this\n\n#money"
    assert engine.run_cmd.calls[-2:] == [["open", TIKTOK_UPLOAD], ["open", "-R", str(folder / "clip.mp4")]]

    row.get_by_role("button", name="Mark as posted").click()
    dialog = page.get_by_role("dialog", name="Mark as posted")
    dialog.get_by_label("The post's link").fill(LIVE)
    dialog.get_by_role("button", name="Mark as posted").click()
    expect(block(page, "Post")).to_have_count(0)  # nothing left to put up
    done = store.get_post(engine.db(), p.id)
    assert (done.status, done.url) == ("posted", LIVE)


def whop(engine) -> None:
    """Whop's Withdraw link set, with no minimum payout."""
    places = {"Whop": Marketplace(withdraw_url=WITHDRAW)}
    engine.update_settings(engine.settings.model_copy(update={"marketplaces": places}))


def approved(engine, campaign, account, views: int):
    """A post of ``campaign`` live on ``account`` with ``views`` counted by hand, its submission approved."""
    live = posted(engine, linked_clip(engine, campaign), account)
    tracker.add_views(engine, live.id, views)
    sub = campaigns.submission_of_post(engine.db(), live.id)
    tracker.move_submission(engine, sub.id, "submitted")
    return tracker.move_submission(engine, sub.id, "approved")


def test_guidelines_show_each_campaign(page, engine, window):
    beast = add_campaign(
        engine,
        rate_per_1000=1.5,
        min_views=1000,
        max_payout=50.0,
        required=Required(hashtags=["a", "b"], mentions=["@x"]),
        banned=["gameplay", "music"],
        platforms=["tiktok", "instagram"],
        ends_on="2026-11-30",
        budget_left=800.0,
        budget_entered_on="2026-10-27",
        campaign_url=PAGE,
    )
    add_campaign(engine, name="Old clips", ends_on="2026-11-01")  # it ended before today
    account = add_account(engine, "tiktok", "t0")
    opened(page, window.url)
    rules = block(page, "Guidelines")
    card = rules.get_by_role("article", name="Beast clips")
    for line in (
        "US$1.50 per 1,000 views, at least 1,000 views, up to US$50.00 a clip",
        "Tags: #a #b",
        "Mentions: @x",
        "Not allowed: gameplay split, music",
        "TikTok and Instagram",
        "Ends 30 Nov",
        "US$800.00 left on 27 Oct",
    ):
        expect(card).to_contain_text(line)
    expect(rules.get_by_role("article", name="Old clips")).to_have_count(0)
    cap = posts_today(engine, account)
    expect(rules).to_contain_text(f"@t0 on TikTok: {cap['posts']} of {cap['cap']}")

    card.get_by_role("button", name="Open campaign page").click()
    expect(toasts(page)).to_contain_text("Opened the campaign page in your browser.")
    assert ["open", PAGE] in engine.run_cmd.calls
    card.get_by_role("link", name="Beast clips").click()
    page.wait_for_url(re.compile(rf"/campaigns/{beast.id}$"))


def test_withdraw_is_primary_when_ready(page, engine, window):
    whop(engine)
    beast = add_campaign(engine, rate_per_1000=2.0)
    live = posted(engine, linked_clip(engine, beast), add_account(engine, "tiktok", "t0"))
    tracker.add_views(engine, live.id, 1000)
    opened(page, window.url)
    row = block(page, "Money").get_by_role("article", name="Whop in USD")
    withdraw = row.get_by_role("button", name="Withdraw")
    expect(withdraw).not_to_have_class(PRIMARY)  # nothing approved to withdraw yet
    expect(row).to_contain_text("estimate")
    expect(row).to_contain_text("your entry")

    sub = campaigns.submission_of_post(engine.db(), live.id)
    tracker.move_submission(engine, sub.id, "submitted")
    expect(row).to_contain_text("US$2.00")  # a submitted post's estimate counts
    tracker.move_submission(engine, sub.id, "approved")
    expect(withdraw).to_have_class(PRIMARY)
    expect(row).to_contain_text("Ready to withdraw")
    withdraw.click()
    expect(toasts(page)).to_contain_text("Opened Whop's payout page in your browser.")
    assert ["open", WITHDRAW] in engine.run_cmd.calls


def test_payout_dialog_marks_paid(page, engine, window):
    whop(engine)
    beast = add_campaign(engine, rate_per_1000=2.0)
    small = approved(engine, beast, add_account(engine, "tiktok", "t0"), 1000)
    big = approved(engine, beast, add_account(engine, "tiktok", "t1"), 3000)  # one post an account at a time
    opened(page, window.url)
    expect(still(page).filter(has_text="Payout ready on Whop")).to_have_count(1)
    block(page, "Money").get_by_role("button", name="I've been paid").click()
    dialog = page.get_by_role("dialog", name="Paid on Whop")
    amounts = dialog.get_by_role("textbox")
    expect(amounts).to_have_count(2)
    shown = [amounts.nth(i).input_value() for i in range(2)]
    assert sorted(shown) == ["2.00", "6.00"]  # each filled in from its estimate
    amounts.nth(shown.index("6.00")).fill("5.50")
    dialog.get_by_role("button", name="Save").click()
    expect(dialog).to_have_count(0)
    expect(toasts(page)).to_contain_text("Recorded 2 payments on Whop.")
    paid = {
        s.id: (s.status, s.paid_amount)
        for s in (campaigns.get_submission(engine.db(), x.id) for x in (small, big))
    }
    assert paid == {small.id: ("paid", 2.0), big.id: ("paid", 5.5)}
    expect(still(page).filter(has_text="Payout ready on Whop")).to_have_count(0)  # it ticked itself
    todo(page).get_by_text("Done today (1)").click()
    expect(todo(page).get_by_role("list", name="Done today")).to_contain_text("Payout ready on Whop")


def test_best_posts_show_gains(page, engine, window):
    live = live_post(engine)
    now = engine.now().timestamp()
    for hours, views in ((26, 300), (1, 1500)):
        campaigns.add_metric(
            engine.db(), Metric(post_id=live.id, captured_at=now - hours * 3600, source="hand", views=views)
        )
    opened(page, window.url)
    row = block(page, "Yesterday's best").get_by_role("article", name=live.clip_title)
    expect(row).to_contain_text("@t0 on TikTok")
    expect(row).to_contain_text("+1,200 views in a day")
    expect(row).to_contain_text("1,500 views")
    expect(row.get_by_role("link", name="Open the post")).to_have_attribute("href", LIVE)


# The rehearsal (phase 5c Task 13): a whole morning by clicks


def act(page: Page, title: str, label: str) -> None:
    """The To do item's action clicked, which starts its timer on the way to where it is done."""
    with page.expect_response(lambda r: r.url.endswith("/start")):
        still(page).filter(has_text=title).get_by_role("link", name=label, exact=True).click()


def titled(engine, campaign, rank: int, title: str):
    """A clip of ``campaign`` with a title of its own, so its row is found by it."""
    clip = linked_clip(engine, campaign, rank)
    repo.update_clip(engine.db(), clip.id, packaging=(clip.packaging or PACK) | {"hook_title": title})
    return repo.get_clip(engine.db(), clip.id)


def test_a_day_on_fakes(page, engine, window, video):
    """A morning seeded as it happens, done by clicks on Today: each item's action starts its timer, the
    work on the page takes the item off the list, and each kind's time is kept for the next estimates."""
    page.context.grant_permissions(CLIPBOARD, origin=window.url)
    whop(engine)
    clock = engine.now
    beast = add_campaign(engine, rate_per_1000=2.0, submit_url=SUBMIT)
    clock.set(at(8, 0, day=1))
    counted = posted(engine, titled(engine, beast, 1, "The first count"), add_account(engine, "tiktok", "t1"))
    clock.set(at(9, 0, day=1))
    tracker.move_submission(engine, campaigns.submission_of_post(engine.db(), counted.id).id, "submitted")
    clock.set(at(8, 0, day=2))
    won = approved(engine, beast, add_account(engine, "tiktok", "t2"), 3000)
    clock.set(NOW)
    sending = posted(engine, titled(engine, beast, 3, "Fresh today"), add_account(engine, "tiktok", "t0"))
    store.update_post(engine.db(), sending.id, url=LIVE)
    podcasts = add_channel(engine, [add_account(engine, "tiktok", "pods", daily_cap=1)], "Podcasts")
    drafted(engine, podcasts, TALK, 90, video)
    opened(page, window.url)
    titles = [
        "Payout ready on Whop",
        "Approve 1 draft (Podcasts)",
        "Submit 1 live post to Whop (Beast clips)",
        "Enter TikTok views for 1 post",
        "Record 1 Whop decision",
    ]
    for title in titles:
        expect(still(page).filter(has_text=title)).to_have_count(1)
    expect(still(page)).to_have_count(len(titles))

    act(page, "Approve 1 draft", "Review")
    clock.advance(seconds=40)
    review(page).get_by_role("button", name="Approve and post").click()
    approve_dialog(page).get_by_role("button", name="Confirm and post").click()
    expect(toasts(page)).to_contain_text("1 draft approved")
    page.get_by_role("navigation", name="Pages").get_by_role("link", name="Today", exact=True).click()
    expect(todo(page)).to_be_visible()  # Today again, not the Queue the post went to
    expect(still(page).filter(has_text="Approve 1 draft")).to_have_count(0)

    act(page, "Submit 1 live post", "Submit")
    clock.advance(seconds=20)
    fresh = block(page, "Submit").get_by_role("article", name="Fresh today")
    fresh.get_by_role("button", name="Copy link and open Whop").click()
    expect(toasts(page)).to_contain_text("Link copied. Paste it on Whop, then mark it submitted.")
    assert page.evaluate(READ_CLIPBOARD) == LIVE
    fresh.get_by_role("button", name="Mark submitted").click()
    expect(still(page).filter(has_text="Submit 1 live post")).to_have_count(0)

    act(page, "Enter TikTok views", "Enter views")
    clock.advance(seconds=15)
    first = block(page, "Submit").get_by_role("article", name="The first count")
    expect(first).to_contain_text("Its 2 days view count is due.")
    first.get_by_role("button", name="Enter views").click()
    first.get_by_label("Views").fill("4,000")
    first.get_by_role("button", name="Save views").click()
    expect(still(page).filter(has_text="Enter TikTok views")).to_have_count(0)

    act(page, "Record 1 Whop decision", "Record")
    clock.advance(seconds=10)
    first.get_by_role("button", name="Approved", exact=True).click()
    expect(still(page).filter(has_text="Record 1 Whop decision")).to_have_count(0)

    act(page, "Payout ready on Whop", "Withdraw")
    clock.advance(seconds=45)
    block(page, "Money").get_by_role("button", name="I've been paid").click()
    dialog = page.get_by_role("dialog", name="Paid on Whop")
    expect(dialog.get_by_role("textbox")).to_have_count(2)  # the count you entered approved this morning
    dialog.get_by_role("button", name="Save").click()
    expect(toasts(page)).to_contain_text("Recorded 2 payments on Whop.")

    expect(still(page)).to_have_count(0)
    expect(todo(page)).to_contain_text(EMPTY)
    expect(todo(page).get_by_text("Done today (5)")).to_be_visible()
    items = today_store.items_of(engine.db(), "2026-11-03")
    assert {(i.kind, i.done_how) for i in items} == {
        (kind, "app") for kind in ("drafts", "submit", "views", "decide", "payout")
    }
    timings = today_store.timings_between(engine.db(), at(0, 0).timestamp(), at(0, 0, day=4).timestamp())
    assert {t.kind: t.seconds for t in timings} == {
        "drafts": 40.0,
        "submit": 20.0,
        "views": 15.0,
        "decide": 10.0,
        "payout": 45.0,
    }
    paid = {s.post_id: s.status for s in campaigns.list_submissions(engine.db(), beast.id)}
    assert paid == {counted.id: "paid", won.post_id: "paid", sending.id: "submitted"}
