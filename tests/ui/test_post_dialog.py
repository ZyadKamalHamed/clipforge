"""The Post dialog (SPEC 14.5): Post on a project's selected clips, or P, and in the editor; the accounts by
platform, with the ones that need a login shown but not pickable and saying why; Spread or Every, Every with
its acknowledgement; each post's caption, hashtags and switches, prefilled and editable with a live count;
Post now, Smart schedule or Custom; the review table with each post's warnings and the fix for a slot that
is taken; and Confirm and post, which sends once and opens the Queue. Nothing is written before it, and
nothing leaves 127.0.0.1: the engine's jobs never start here."""

from __future__ import annotations

import contextlib
import re

import pytest
from playwright.sync_api import Page, expect

from clipforge import repo
from clipforge.post import accounts as posting
from clipforge.post import queue, store
from clipforge.post.plan import NOT_CLEARED, RENDERS_VARIANT
from clipforge.render.styles import StyleKit
from clipforge.trends import store as trends_store
from tests.fakes import Clock, fake_engine
from tests.posting import add_account, add_clip, at, custom
from tests.ui.conftest import live_server
from tests.ui.seed import hook, picture, seed_project
from tests.ui.test_shell import opened, toasts
from tests.unit.test_trends_store import shortlist, trending

NOW = at(6)  # 6:00 am on Tue 3 Nov 2026 in Sydney
TIKTOK_CAPTION = "The deadline is coming. Would you start today?"
INSTAGRAM_CAPTION = "Why the deadline never scares him, in one minute."
NEEDS_LOGIN = "Press Log in to post here."
EVERY_ACK = "Posting the same clip to several accounts on one platform is what TikTok's spam rule describes"
PICK_FIRST = "Select the clips to post first."
PROMISE = "Nothing posts until you press Confirm and post."
ASSISTED = "Assisted: you add the sound"


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


def counts(engine) -> dict[str, int]:
    conn = engine.db()
    return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("batches", "posts", "jobs")}


def open_project(page: Page, window, pid: str) -> None:
    opened(page, f"{window.url}/projects/{pid}")
    expect(page.get_by_role("article", name=hook(0), exact=True)).to_be_visible()


def pick(page: Page, *ks: int) -> None:
    for k in ks:
        page.get_by_role("article", name=hook(k), exact=True).get_by_role("checkbox").check()


def post_dialog(page: Page):
    return page.get_by_role("dialog", name=re.compile(r"^Post \d+ clips?$"))


def start(page: Page, window, pid: str, *ks: int, accounts: tuple[str, ...] = ()):
    """The Post dialog on clips ``ks``, with ``accounts`` picked."""
    open_project(page, window, pid)
    pick(page, *ks)
    page.get_by_role("toolbar", name="Selected clips").get_by_role("button", name="Post", exact=True).click()
    dialog = post_dialog(page)
    expect(dialog).to_be_visible()
    for name in accounts:
        dialog.get_by_role("checkbox", name=name).check()
    return dialog


def on_step(dialog, name: str) -> None:
    expect(dialog.get_by_role("list", name="Steps").locator("[aria-current=step]")).to_have_text(
        re.compile(rf"^\d\. {name}$")
    )


def to_review(dialog) -> None:
    dialog.get_by_role("button", name="Next").click()
    on_step(dialog, "Details")
    dialog.get_by_role("button", name="Next").click()
    on_step(dialog, "Review")


def rows(dialog):
    return dialog.get_by_role("table", name="Posts").locator("tbody tr")


def confirm_button(dialog):
    return dialog.get_by_role("button", name="Confirm and post")


def test_post_needs_selected_clips(page, engine, window):
    pid = seed_project(engine, clips=3, rendered=3)
    add_account(engine, "tiktok", "tt_one")
    open_project(page, window, pid)
    expect(page.get_by_role("button", name="Post", exact=True)).to_have_count(0)
    page.keyboard.press("p")
    expect(toasts(page)).to_contain_text(PICK_FIRST)
    expect(post_dialog(page)).to_have_count(0)
    pick(page, 0)
    page.get_by_role("toolbar", name="Selected clips").get_by_role("button", name="Post", exact=True).click()
    expect(page.get_by_role("dialog", name="Post 1 clip")).to_be_visible()
    page.keyboard.press("Escape")
    expect(post_dialog(page)).to_have_count(0)


def test_p_opens_the_post_dialog(page, engine, window):
    pid = seed_project(engine, clips=3, rendered=2)
    add_account(engine, "tiktok", "tt_one")
    open_project(page, window, pid)
    pick(page, 0, 2)
    page.keyboard.press("p")
    dialog = page.get_by_role("dialog", name="Post 2 clips")
    expect(dialog).to_be_visible()
    # A key pressed in the dialog belongs to it: P there opens nothing more.
    page.keyboard.press("p")
    expect(post_dialog(page)).to_have_count(1)
    page.keyboard.press("Escape")
    expect(dialog).to_have_count(0)

    # The editor posts its own clip, from its Post button or P.
    opened(page, f"{window.url}/clips/{pid}_c2")
    expect(page.get_by_role("heading", level=1)).to_contain_text(hook(2))
    page.get_by_role("button", name="Post", exact=True).click()
    expect(page.get_by_role("dialog", name="Post 1 clip")).to_be_visible()
    page.keyboard.press("Escape")
    page.keyboard.press("p")
    expect(page.get_by_role("dialog", name="Post 1 clip")).to_be_visible()


def test_a_logged_out_account_logs_in_from_its_row(page, engine, window):
    """A Chrome account that needs a login has Log in on its row, so you needn't leave the dialog."""
    pid = seed_project(engine, clips=1, rendered=1)
    tt_two = add_account(engine, "tiktok", "tt_two")
    store.update_account(engine.db(), tt_two.id, status="needs_login")
    dialog = start(page, window, pid, 0)
    two = dialog.get_by_role("checkbox", name="@tt_two")
    expect(two).to_be_disabled()
    dialog.get_by_role("button", name="Log in").click()
    expect(toasts(page)).to_contain_text("Log in to TikTok in the Chrome window that opens.")
    (job,) = [j for j in engine.runner.jobs_for(tt_two.id) if j.kind == "account.login"]
    assert job.status == "queued"
    expect(dialog.get_by_text("Waiting for Chrome", exact=True)).to_be_visible()  # no window open yet
    queue.change_account(engine, tt_two.id, status="connected")
    expect(two).to_be_enabled()
    expect(dialog.get_by_role("button", name="Log in")).to_have_count(0)


def test_a_connected_account_logging_in_again_says_so_on_its_row(page, engine, window):
    """Its posts would wait behind the login in Chrome, so the row doesn't say Connected meanwhile."""
    pid = seed_project(engine, clips=1, rendered=1)
    tt = add_account(engine, "tiktok", "tt_one")
    posting.start(engine, "account.login", tt.id)
    dialog = start(page, window, pid, 0)
    row = dialog.get_by_role("group", name="TikTok")
    expect(row.get_by_text("Waiting for Chrome", exact=True)).to_be_visible()
    expect(row.get_by_text("Connected")).to_have_count(0)
    expect(row.get_by_role("checkbox", name="@tt_one")).to_be_enabled()


def test_accounts_step_groups_by_platform_and_explains_needs_login(page, engine, window):
    pid = seed_project(engine, clips=1, rendered=1)
    add_account(engine, "instagram", "ig_one")
    add_account(engine, "tiktok", "tt_one")
    tt_two = add_account(engine, "tiktok", "tt_two")
    store.update_account(engine.db(), tt_two.id, status="needs_login")
    dialog = start(page, window, pid, 0)
    on_step(dialog, "Accounts")
    expect(dialog.locator("fieldset[data-platform] > legend")).to_have_text(["TikTok", "Instagram"])
    tiktok, instagram = (
        dialog.get_by_role("group", name="TikTok"),
        dialog.get_by_role("group", name="Instagram"),
    )
    expect(tiktok.get_by_role("checkbox")).to_have_count(2)
    expect(instagram.get_by_role("checkbox")).to_have_count(1)
    one = tiktok.get_by_role("checkbox", name="@tt_one")
    expect(one).to_be_enabled()
    expect(tiktok.get_by_text("0 of 2 today")).to_have_count(2)
    expect(tiktok.get_by_text("Connected")).to_be_visible()
    two = tiktok.get_by_role("checkbox", name="@tt_two")
    expect(two).to_be_disabled()
    expect(two).to_have_accessible_description(re.compile(re.escape(NEEDS_LOGIN)))
    expect(tiktok.get_by_text("Needs login")).to_be_visible()
    expect(instagram.get_by_text("0 of 1 today")).to_be_visible()
    # Nothing picked, nothing to plan.
    expect(dialog.get_by_role("button", name="Next")).to_be_disabled()
    one.check()
    expect(dialog.get_by_role("button", name="Next")).to_be_enabled()


def test_spread_and_every_with_the_acknowledgement(page, engine, window):
    pid = seed_project(engine, clips=2, rendered=2)
    add_account(engine, "tiktok", "tt_one")
    add_account(engine, "tiktok", "tt_two")
    dialog = start(page, window, pid, 0, 1, accounts=("@tt_one", "@tt_two"))
    expect(dialog.get_by_role("radio", name="Spread")).to_be_checked()
    expect(dialog.get_by_text(EVERY_ACK)).to_have_count(0)
    to_review(dialog)
    expect(rows(dialog)).to_have_count(2)  # one account each
    expect(rows(dialog).nth(0)).to_contain_text("@tt_one")
    expect(rows(dialog).nth(1)).to_contain_text("@tt_two")

    dialog.get_by_role("button", name="Back").click()
    dialog.get_by_role("button", name="Back").click()
    dialog.get_by_role("radio", name="Every clip to every account").check()
    ack = dialog.get_by_role("checkbox", name=re.compile("Post every clip to every account anyway"))
    expect(ack).to_be_visible()
    expect(ack).to_have_accessible_description(re.compile(re.escape(EVERY_ACK)))
    expect(dialog.get_by_role("button", name="Next")).to_be_disabled()
    ack.check()
    to_review(dialog)
    expect(rows(dialog)).to_have_count(4)
    expect(confirm_button(dialog)).to_be_enabled()


def test_details_waits_for_the_plan(page, engine, window):
    """While the posts are planned, Details says so beside a spinner and Next waits for them."""
    pid = seed_project(engine, clips=1, rendered=1)
    add_account(engine, "tiktok", "tt_one")
    held = []
    page.route("**/api/posts/plan", lambda route: held.append(route))  # the plan doesn't come back yet
    dialog = start(page, window, pid, 0, accounts=("@tt_one",))
    dialog.get_by_role("button", name="Next").click()
    planning = dialog.get_by_role("status").filter(has_text="Planning the posts.")
    expect(planning).to_be_visible()
    expect(planning.locator("svg.motion-safe\\:animate-spin")).to_have_count(1)
    expect(dialog.get_by_role("button", name="Next")).to_be_disabled()

    page.unroute("**/api/posts/plan")
    for route in held:
        with contextlib.suppress(Exception):  # one the dialog gave up on when its request changed
            route.continue_()
    expect(dialog.get_by_role("group", name=f"{hook(0)} to @tt_one")).to_be_visible()
    expect(planning).to_have_count(0)
    expect(dialog.get_by_role("button", name="Next")).to_be_enabled()


def test_details_prefilled_from_packaging_and_editable(page, engine, window):
    pid = seed_project(engine, clips=1, rendered=1)
    tt = add_account(engine, "tiktok", "tt_one")
    ig = add_account(engine, "instagram", "ig_one")
    dialog = start(page, window, pid, 0, accounts=("@tt_one", "@ig_one"))  # Spread: one post per platform
    dialog.get_by_role("button", name="Next").click()
    tiktok = dialog.get_by_role("group", name=f"{hook(0)} to @tt_one")
    instagram = dialog.get_by_role("group", name=f"{hook(0)} to @ig_one")
    caption = tiktok.get_by_label("Caption")
    expect(caption).to_have_value(re.compile("^" + re.escape(TIKTOK_CAPTION)))
    expect(tiktok.get_by_label("Hashtags")).to_have_value("#deadlines")
    expect(tiktok.get_by_text(re.compile(r"^\d+ of 4000$"))).to_be_visible()
    expect(tiktok.get_by_label("Who can watch")).to_have_value("everyone")
    for name in ("Comments", "Duet", "Stitch"):
        expect(tiktok.get_by_role("switch", name=name)).to_be_checked()
    expect(tiktok.get_by_role("switch", name="Content disclosure")).not_to_be_checked()
    expect(tiktok.get_by_role("switch", name="Share to feed")).to_have_count(0)
    expect(instagram.get_by_label("Caption")).to_have_value(re.compile("^" + re.escape(INSTAGRAM_CAPTION)))
    expect(instagram.get_by_text(re.compile(r"^\d+ of 2200$"))).to_be_visible()
    expect(instagram.get_by_role("switch", name="Share to feed")).to_be_checked()
    expect(instagram.get_by_role("switch", name="Duet")).to_have_count(0)

    caption.fill("My own words")
    tiktok.get_by_label("Hashtags").fill("#mine #clips")
    expect(tiktok.get_by_text("26 of 4000")).to_be_visible()  # "My own words\n\n#mine #clips"
    tiktok.get_by_role("switch", name="Duet").uncheck()
    tiktok.get_by_label("Who can watch").select_option("friends")
    dialog.get_by_role("button", name="Next").click()
    expect(rows(dialog).filter(has_text="@tt_one")).to_contain_text("My own words")
    confirm_button(dialog).click()
    expect(page).to_have_url(re.compile(r"/queue$"))
    (posted,) = store.list_posts(engine.db(), account_id=tt.id)
    assert posted.caption == "My own words"
    assert posted.hashtags == ["mine", "clips"]
    assert posted.options.duet is False and posted.options.visibility == "friends"
    assert posted.options.stitch is True
    (other,) = store.list_posts(engine.db(), account_id=ig.id)
    assert other.caption.startswith(INSTAGRAM_CAPTION) and other.options.share_to_feed is True


def test_trial_reel_switches(page, engine, window):
    """An Instagram post through Meta's API offers a trial reel, and while it is on, how it reaches followers
    takes Share to feed's place; a TikTok post offers neither."""
    pid = seed_project(engine, clips=1, rendered=1)
    add_account(engine, "tiktok", "tt_one")
    ig = add_account(engine, "instagram", "ig_one")
    dialog = start(page, window, pid, 0, accounts=("@tt_one", "@ig_one"))
    dialog.get_by_role("button", name="Next").click()
    tiktok = dialog.get_by_role("group", name=f"{hook(0)} to @tt_one")
    instagram = dialog.get_by_role("group", name=f"{hook(0)} to @ig_one")
    graduate = instagram.get_by_role("switch", name="Share with followers if it does well")
    expect(instagram.get_by_role("switch", name="Trial reel")).not_to_be_checked()
    expect(instagram.get_by_role("switch", name="Share to feed")).to_be_checked()
    expect(graduate).to_have_count(0)
    for name in ("Trial reel", "Share with followers if it does well"):
        expect(tiktok.get_by_role("switch", name=name)).to_have_count(0)

    instagram.get_by_role("switch", name="Trial reel").check()
    expect(
        instagram.get_by_text("Shown only to people who don't follow this account at first.")
    ).to_be_visible()
    expect(instagram.get_by_role("switch", name="Share to feed")).to_have_count(0)
    expect(graduate).not_to_be_checked()
    expect(instagram.get_by_text("Off: you share it from Instagram when you choose.")).to_be_visible()
    graduate.check()
    expect(instagram.get_by_text("Trial reel on", exact=True)).to_be_visible()  # its summary, closed or open
    dialog.get_by_role("button", name="Next").click()
    expect(rows(dialog).filter(has_text="@ig_one")).to_contain_text(
        "Trial reel: people who don't follow this account see it first. Instagram shares it with followers "
        "if it does well."
    )
    expect(rows(dialog).filter(has_text="@tt_one")).not_to_contain_text("Trial reel")
    confirm_button(dialog).click()
    expect(page).to_have_url(re.compile(r"/queue$"))
    (posted,) = store.list_posts(engine.db(), account_id=ig.id)
    assert posted.options.trial_reel is True and posted.options.trial_graduation == "auto"


def test_post_dialog_picks_an_instagram_sound(page, engine, window):
    pid = seed_project(engine, clips=1, rendered=1)
    ig = add_account(engine, "instagram", "ig_one")
    ig = store.update_account(engine.db(), ig.id, ig_mode="facebook")  # Instagram adds sounds only this way
    listed = [trending(ig.id, "m0", 0), trending(ig.id, "m1", 1)]
    trends_store.replace_trending(engine.db(), ig.id, "music", listed, NOW.timestamp())
    dialog = start(page, window, pid, 0, accounts=("@ig_one",))
    dialog.get_by_role("button", name="Next").click()
    post = dialog.get_by_role("group", name=f"{hook(0)} to @ig_one")
    sound = post.get_by_role("combobox", name="Sound")
    expect(sound).to_have_value("")
    expect(sound.locator("option")).to_have_text(["None", "Song m0", "Song m1"])
    expect(post.get_by_role("slider", name="Sound level")).to_have_count(0)
    sound.select_option(label="Song m1")
    level = post.get_by_role("slider", name="Sound level")
    expect(level).to_have_value("15")
    level.fill("40")
    expect(post.get_by_text(ASSISTED)).to_have_count(0)  # Instagram adds it, so nothing waits for you
    dialog.get_by_role("button", name="Next").click()
    row = rows(dialog).filter(has_text="@ig_one")
    expect(row).to_contain_text("Sound: Song m1")
    expect(row.locator("[data-col=warnings]")).to_have_text("None")
    confirm_button(dialog).click()
    expect(page).to_have_url(re.compile(r"/queue$"))
    (posted,) = store.list_posts(engine.db(), account_id=ig.id)
    assert posted.options.sound.model_dump() == {
        "source": "ig_trending",
        "external_id": "m1",
        "title": "Song m1",
        "artist": None,
        "url": None,
        "volume": 40,
    }
    assert posted.options.assisted is False


def test_tiktok_sound_marks_the_post_assisted(page, engine, window):
    pid = seed_project(engine, clips=1, rendered=1)
    tt = add_account(engine, "tiktok", "tt_one")
    calm = trends_store.put_shortlist(engine.db(), shortlist(title="Late Train", mood_tags=["calm"]))
    trends_store.put_shortlist(engine.db(), shortlist("7234567890123456780", title="Arcade Rush"))
    dialog = start(page, window, pid, 0, accounts=("@tt_one",))
    dialog.get_by_role("button", name="Next").click()
    post = dialog.get_by_role("group", name=f"{hook(0)} to @tt_one")
    sound = post.get_by_role("combobox", name="Sound")
    # the shortlisted sounds that suit the clip, then the rest of the shortlist
    expect(sound.locator("option")).to_have_text(["None", "Late Train", "Arcade Rush"])
    expect(post.get_by_text(ASSISTED)).to_have_count(0)
    sound.select_option(label="Late Train")
    expect(post.get_by_text(ASSISTED)).to_be_visible()
    expect(post.get_by_role("slider", name="Sound level")).to_have_count(0)  # set in TikTok's window
    dialog.get_by_role("button", name="Next").click()
    row = rows(dialog).filter(has_text="@tt_one")
    expect(row).to_contain_text("Sound: Late Train")
    expect(row.locator("[data-col=warnings]")).to_contain_text(
        "You add this sound yourself in TikTok's window: Late Train."
    )
    confirm_button(dialog).click()
    expect(page).to_have_url(re.compile(r"/queue$"))
    (posted,) = store.list_posts(engine.db(), account_id=tt.id)
    assert posted.options.assisted is True
    assert (posted.options.sound.source, posted.options.sound.external_id, posted.options.sound.title) == (
        "shortlist",
        calm.external_id,
        "Late Train",
    )


def test_a_long_step_keeps_its_buttons_in_sight(page, engine, window):
    """Six posts of fields in a 1440 by 900 window: the fields scroll, Back and Next stay put."""
    pid = seed_project(engine, clips=3, rendered=3)
    add_account(engine, "tiktok", "tt_one")
    add_account(engine, "instagram", "ig_one")
    page.set_viewport_size({"width": 1440, "height": 900})
    dialog = start(page, window, pid, 0, 1, 2, accounts=("@tt_one", "@ig_one"))
    dialog.get_by_role("button", name="Next").click()
    expect(dialog.get_by_role("group", name=f"{hook(2)} to @ig_one")).to_be_attached()  # the plan is in
    for name in ("Back", "Next"):
        expect(dialog.get_by_role("button", name=name)).to_be_in_viewport(ratio=1)


def test_the_steps_and_the_promise_stay_in_sight(page, engine, window):
    """However far a step scrolls, the step row and the promise stay under the title."""
    pid = seed_project(engine, clips=3, rendered=3)
    add_account(engine, "tiktok", "tt_one")
    add_account(engine, "instagram", "ig_one")
    page.set_viewport_size({"width": 1440, "height": 640})
    dialog = start(page, window, pid, 0, 1, 2, accounts=("@tt_one", "@ig_one"))
    dialog.get_by_role("button", name="Next").click()
    expect(dialog.get_by_role("group", name=f"{hook(2)} to @ig_one")).to_be_attached()
    dialog.evaluate("d => d.scrollTo(0, d.scrollHeight)")
    assert dialog.evaluate("d => d.scrollTop") > 0
    expect(dialog.get_by_role("list", name="Steps")).to_be_in_viewport(ratio=1)
    expect(dialog.get_by_text(PROMISE)).to_be_in_viewport(ratio=1)


def test_every_tiktok_post_changes_together_and_a_post_keeps_its_own(page, engine, window):
    """One block per platform sets the switches on all its posts. Each post is one line under its clip that
    opens on a click, and a switch changed there stays that post's."""
    pid = seed_project(engine, clips=2, rendered=2)
    t1 = add_account(engine, "tiktok", "tt_one")
    add_account(engine, "tiktok", "tt_two")
    add_account(engine, "instagram", "ig_one")
    dialog = start(page, window, pid, 0, 1, accounts=("@tt_one", "@tt_two", "@ig_one"))
    dialog.get_by_role("radio", name="Every clip to every account").check()
    dialog.get_by_role("checkbox", name=re.compile("Post every clip to every account anyway")).check()
    dialog.get_by_role("button", name="Next").click()
    one = dialog.get_by_role("group", name=f"{hook(1)} to @tt_one")
    expect(one).to_be_visible()
    expect(one.get_by_label("Caption")).to_be_hidden()  # six posts, so each is one line until opened
    expect(dialog.get_by_role("region", name=hook(0)).get_by_role("group")).to_have_count(3)
    tiktok = dialog.get_by_role("group", name="Every TikTok post")
    tiktok.get_by_label("Who can watch").select_option("only_me")
    tiktok.get_by_role("switch", name="Comments").uncheck()
    every_ig = dialog.get_by_role("group", name="Every Instagram post")
    expect(every_ig.get_by_role("switch", name="Share to feed")).to_be_checked()
    expect(every_ig.get_by_role("switch", name="Comments")).to_have_count(0)

    one.locator("summary").click()
    expect(one.get_by_label("Caption")).to_be_visible()
    expect(one.get_by_label("Who can watch")).to_have_value("only_me")
    own = one.get_by_role("switch", name="Comments")
    expect(own).not_to_be_checked()
    own.check()
    one.locator("summary").click()  # shut again, its line says what differs from the block
    expect(one.locator("summary")).to_contain_text("Comments on")
    expect(dialog.get_by_role("group", name=f"{hook(0)} to @tt_one").locator("summary")).not_to_contain_text(
        "Comments"
    )
    dialog.get_by_role("button", name="Next").click()
    on_step(dialog, "Review")
    confirm_button(dialog).click()
    expect(page).to_have_url(re.compile(r"/queue$"))
    posts = store.list_posts(engine.db())
    assert len(posts) == 6
    for p in posts:
        if p.platform == "instagram":
            assert p.options.share_to_feed is True and p.options.visibility == "everyone"
            continue
        assert p.options.visibility == "only_me" and p.options.duet is True
        assert p.options.comments is (p.clip_title == hook(1) and p.account_id == t1.id)


def test_review_says_a_whole_video_warning_once_and_names_each_platform(page, engine, window):
    """The rights warning comes once, above the table, and each row says which platform its account is on,
    so the same handle on TikTok and Instagram doesn't read as a duplicate."""
    pid = seed_project(engine, clips=2, rendered=2)  # its rights aren't cleared
    add_account(engine, "tiktok", "same")
    add_account(engine, "instagram", "same")
    dialog = start(page, window, pid, 0, 1)
    for platform in ("TikTok", "Instagram"):
        dialog.get_by_role("group", name=platform).get_by_role("checkbox", name="@same").check()
    to_review(dialog)
    expect(rows(dialog)).to_have_count(4)  # each clip on each platform
    expect(dialog.get_by_text(NOT_CLEARED)).to_have_count(1)
    expect(rows(dialog).filter(has_text="Not cleared")).to_have_count(0)
    accounts = rows(dialog).locator("td[data-col=account]")
    expect(accounts.filter(has_text="TikTok")).to_have_count(2)
    expect(accounts.filter(has_text="Instagram")).to_have_count(2)
    expect(rows(dialog).first.locator("td[data-col=warnings]")).to_have_text("None")


def test_a_style_kit_account_says_its_version_renders_first(page, engine, window, console):
    pid = seed_project(engine, clips=1, rendered=1)
    add_account(engine, "tiktok", "tt_kit", style_kit=StyleKit(accent="#00E5FF"))
    dialog = start(page, window, pid, 0, accounts=("@tt_kit",))
    to_review(dialog)
    note = rows(dialog).first.locator("td[data-col=warnings]").get_by_text(RENDERS_VARIANT)
    expect(note).to_be_visible()
    expect(note).to_have_class(re.compile(r"\btext-muted\b"))  # a note, nothing to fix
    expect(confirm_button(dialog)).to_be_enabled()


def test_over_limit_caption_blocks_confirm(page, engine, window, console):
    pid = seed_project(engine, clips=1, rendered=1)
    add_account(engine, "instagram", "ig_one")
    dialog = start(page, window, pid, 0, accounts=("@ig_one",))
    dialog.get_by_role("button", name="Next").click()
    caption = dialog.get_by_label("Caption")
    caption.fill("x" * 2300)
    count = dialog.get_by_text(re.compile(r"^2\d{3} of 2200$"))
    expect(count).to_be_visible()
    expect(count).to_have_class(re.compile("text-bad"))
    dialog.get_by_role("button", name="Next").click()
    expect(rows(dialog).first).to_contain_text("over the 2200 Instagram takes. Shorten it to post.")
    expect(confirm_button(dialog)).to_be_disabled()
    expect(dialog.get_by_text("Fix the warnings in red to post.")).to_be_visible()
    dialog.get_by_role("button", name="Back").click()
    dialog.get_by_label("Caption").fill("Short and sweet")
    dialog.get_by_role("button", name="Next").click()
    expect(rows(dialog).first).not_to_contain_text("Shorten it to post.")
    expect(confirm_button(dialog)).to_be_enabled()


def test_timing_now_smart_and_custom(page, engine, window):
    pid = seed_project(engine, clips=2, rendered=2)
    add_account(engine, "tiktok", "tt_one")
    dialog = start(page, window, pid, 0, 1, accounts=("@tt_one",))
    dialog.get_by_role("button", name="Next").click()
    expect(dialog.get_by_role("radio", name="Smart schedule")).to_be_checked()
    dialog.get_by_role("button", name="Next").click()
    times = rows(dialog).locator("td[data-col=when]")
    expect(times).to_have_count(2)
    expect(times.nth(0)).to_have_text(re.compile(r"^Today \d{1,2}:\d{2} (am|pm)$"))

    dialog.get_by_role("button", name="Back").click()
    dialog.get_by_role("radio", name="Post now").check()
    dialog.get_by_role("button", name="Next").click()
    expect(times.nth(0)).to_have_text(re.compile(r"^Today 6:\d{2} am$"))

    dialog.get_by_role("button", name="Back").click()
    dialog.get_by_role("radio", name="Custom").check()
    dialog.get_by_label("Start").fill("2026-11-03T18:00")
    dialog.get_by_label("Minutes between posts").fill("200")
    dialog.get_by_role("button", name="Next").click()
    expect(times.nth(0)).to_contain_text("Today 6:00 pm")
    expect(times.nth(1)).to_contain_text("Today 9:20 pm")
    # A time of its own for one post.
    rows(dialog).nth(1).get_by_label(re.compile("^Time for")).fill("2026-11-04T07:30")
    expect(times.nth(1)).to_contain_text("Tomorrow 7:30 am")
    expect(confirm_button(dialog)).to_be_enabled()


def test_move_to_the_next_free_slot_fixes_a_cap_warning(page, engine, window):
    pid = seed_project(engine, clips=1, rendered=1)
    tt = add_account(engine, "tiktok", "tt_one")
    for when in (at(7, 30), at(12, 15)):  # its daily cap of 2 is used today
        custom(engine, add_clip(engine), tt, when)
    dialog = start(page, window, pid, 0, accounts=("@tt_one",))
    expect(dialog.get_by_role("checkbox", name="@tt_one")).to_have_accessible_description(
        re.compile("2 of 2 today")
    )
    dialog.get_by_role("button", name="Next").click()
    dialog.get_by_role("radio", name="Custom").check()
    dialog.get_by_label("Start").fill("2026-11-03T19:00")
    dialog.get_by_role("button", name="Next").click()
    row = rows(dialog).first
    expect(row).to_contain_text("@tt_one already has 2 posts on Tue 3 Nov, its daily cap.")
    expect(confirm_button(dialog)).to_be_disabled()
    move = row.get_by_role("button", name=re.compile(r"^Move to Tomorrow \d{1,2}:\d{2} am$"))
    when = move.inner_text().removeprefix("Move to ")
    move.click()
    expect(row.locator("td[data-col=when]")).to_contain_text(when)
    expect(row).not_to_contain_text("its daily cap")
    expect(confirm_button(dialog)).to_be_enabled()
    confirm_button(dialog).click()
    expect(page).to_have_url(re.compile(r"/queue$"))
    assert len(store.list_posts(engine.db(), account_id=tt.id)) == 3


def test_confirm_and_post_opens_the_queue(page, engine, window):
    pid = seed_project(engine, clips=1, rendered=1)
    tt = add_account(engine, "tiktok", "tt_one")
    dialog = start(page, window, pid, 0, accounts=("@tt_one",))
    to_review(dialog)
    before = counts(engine)
    assert before["batches"] == 0 and before["posts"] == 0
    confirm_button(dialog).click()
    expect(page).to_have_url(re.compile(r"/queue$"))
    expect(post_dialog(page)).to_have_count(0)
    expect(toasts(page)).to_contain_text("1 post scheduled")
    (posted,) = store.list_posts(engine.db())
    assert counts(engine)["batches"] == 1
    assert posted.account_id == tt.id and posted.clip_id == f"{pid}_c0" and posted.status == "scheduled"


def test_double_click_confirm_makes_one_batch(page, engine, window):
    pid = seed_project(engine, clips=2, rendered=2)
    add_account(engine, "tiktok", "tt_one")
    sent: list[str] = []
    page.on(
        "request",
        lambda r: sent.append(r.url) if r.method == "POST" and "/api/posts/confirm" in r.url else None,
    )
    dialog = start(page, window, pid, 0, 1, accounts=("@tt_one",))
    to_review(dialog)
    before = counts(engine)
    confirm_button(dialog).dblclick()
    expect(page).to_have_url(re.compile(r"/queue$"))
    assert len(sent) == 1
    after = counts(engine)
    assert after["batches"] == before["batches"] + 1 and after["posts"] == before["posts"] + 2


def test_nothing_posts_without_confirm(page, engine, window):
    pid = seed_project(engine, clips=2, rendered=1)  # the second clip would render first
    add_account(engine, "tiktok", "tt_one")
    add_account(engine, "instagram", "ig_one")
    planned: list[str] = []
    page.on(
        "request", lambda r: planned.append(r.url) if r.method == "POST" and "/api/posts/" in r.url else None
    )
    before = counts(engine)
    dialog = start(page, window, pid, 0, 1, accounts=("@tt_one", "@ig_one"))
    to_review(dialog)
    expect(rows(dialog)).to_have_count(4)
    expect(rows(dialog).filter(has_text=hook(1)).first).to_contain_text("renders it before it posts")
    dialog.get_by_role("button", name="Back").click()
    dialog.get_by_role("radio", name="Post now").check()
    dialog.get_by_role("button", name="Next").click()
    expect(rows(dialog)).to_have_count(4)
    dialog.get_by_role("button", name="Cancel").click()
    expect(post_dialog(page)).to_have_count(0)
    assert planned and all(url.endswith("/api/posts/plan") for url in planned)
    assert counts(engine) == before
    assert repo.get_clip(engine.db(), f"{pid}_c1").current_render_id is None
