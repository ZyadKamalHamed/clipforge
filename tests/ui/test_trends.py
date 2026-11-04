"""The Trends page (SPEC 13 and 14): the filters, a block per Instagram account linked through Facebook with
its trending sounds and when they were read, the accounts Instagram gives no list, the TikTok shortlist
pasted, changed and removed with a Stale chip after 14 days, the four weekly lists that open in your browser
and put out the New list out badge, the feed switch, and the hashtag sets made, copied, changed and
deleted. Nothing leaves 127.0.0.1: every open is recorded, never run, and Check now is never pressed."""

from __future__ import annotations

import time

import pytest
from playwright.sync_api import Page, expect

from clipforge import repo
from clipforge.post import store as post_store
from clipforge.trends import feed
from clipforge.trends import store as trends_store
from clipforge.trends.models import HashtagSet, TrendRead
from clipforge.trends.shortlist import SHORT_LINK
from tests.fakes import Clock, fake_engine
from tests.posting import add_account, at
from tests.ui.conftest import live_server
from tests.ui.test_shell import opened, toasts
from tests.ui.test_today import CLIPBOARD, READ_CLIPBOARD
from tests.unit.test_trends_store import shortlist, trending

NOW = at(7, 10)  # 7:10 am on Tue 3 Nov 2026 in Sydney
DAY = 86400
LINK = "https://www.tiktok.com/music/Late-Train-7234567890123456789"
NO_FACEBOOK = "No Instagram account is linked through Facebook yet."
NO_SHORTLIST = "Paste a tiktok.com/music link to start your shortlist."
NO_SETS = "No hashtag sets yet."
NO_MATCH = "No sounds match the filters."
PING = "Check SocialPilot's feed once a week for a new list"
MODE_B = "Instagram doesn't offer trending sounds to accounts linked through Instagram Login: @gaming.clips."


@pytest.fixture
def engine():
    e = fake_engine(now=Clock(NOW))
    yield e
    e.close()


@pytest.fixture
def window(engine, page, monkeypatch):
    """The window over ``engine`` with nothing running, its clock at the engine's."""
    monkeypatch.setattr(engine.runner, "start", lambda: None)
    page.clock.set_fixed_time(NOW)
    with live_server(engine) as live:
        yield live


def facebook_account(engine, handle: str):
    """An Instagram account linked through Facebook, the only kind Instagram lists trending sounds for."""
    a = add_account(engine, "instagram", handle)
    return post_store.update_account(engine.db(), a.id, ig_mode="facebook")


def listed(engine, account, now: float, *rows: tuple[str, str, str | None, float | None]):
    """Instagram's lists for ``account`` as read at ``now``: (id, type, artist, length) per sound."""
    for audio_type in ("music", "original_sound"):
        sounds = [
            trending(account.id, external_id, rank).model_copy(
                update={
                    "artist": artist,
                    "duration_s": length,
                    "url": f"https://www.instagram.com/reels/audio/{external_id}/",
                    "created_at": now,
                }
            )
            for rank, (external_id, kind, artist, length) in enumerate(rows)
            if kind == audio_type
        ]
        trends_store.replace_trending(engine.db(), account.id, audio_type, sounds, now)
    trends_store.save_read(engine.db(), TrendRead(id=f"ig:{account.id}", read_at=now, result="ok"))


def a_set(engine, name: str, niche: str, *tags: str) -> HashtagSet:
    now = time.time()
    made = HashtagSet(
        id=repo.new_id("hs"), niche=niche, name=name, tags=list(tags), created_at=now, updated_at=now
    )
    return trends_store.put_set(engine.db(), made)


def section(page: Page, name: str):
    return page.get_by_role("region", name=name, exact=True)


def block(page: Page, handle: str):
    return page.get_by_role("article", name=f"@{handle}", exact=True)


def rows(where):
    return where.locator("tbody tr")


def opens(engine) -> list[str]:
    return [c[1] for c in engine.run_cmd.calls if c[:1] == ["open"]]


def test_trends_page_shows_every_block(page, engine, window):
    opened(page, window.url + "/trends")
    expect(page.get_by_role("heading", level=1, name="Trends")).to_be_visible()
    expect(page.get_by_role("link", name="Trends")).to_have_attribute("aria-current", "page")
    expect(section(page, "Trending on Instagram").get_by_text(NO_FACEBOOK)).to_be_visible()
    expect(section(page, "TikTok shortlist").get_by_text(NO_SHORTLIST)).to_be_visible()
    expect(section(page, "Hashtag sets").get_by_text(NO_SETS)).to_be_visible()
    expect(page.get_by_text(MODE_B)).to_have_count(0)

    drama = facebook_account(engine, "drama.clips")
    gaming = add_account(engine, "instagram", "gaming.clips")
    post_store.update_account(engine.db(), gaming.id, ig_mode="instagram")
    read_at = NOW.timestamp() - 2 * 3600
    listed(
        engine, drama, read_at, ("m0", "music", "The Band", 30.0), ("o0", "original_sound", "a.creator", None)
    )
    trends_store.put_shortlist(
        engine.db(),
        shortlist(
            title="Late Train", region="AU", mood_tags=["calm", "dark"], energy=2, created_at=NOW.timestamp()
        ),
    )
    a_set(engine, "Gaming core", "Gaming", "gaming", "clips")
    page.reload()

    instagram = block(page, "drama.clips")
    expect(instagram.get_by_text("Read 2 h ago.")).to_be_visible()
    expect(
        instagram.get_by_role("button", name="Check now")
    ).to_be_disabled()  # its token isn't in the Keychain
    expect(rows(instagram).locator("th")).to_have_text(["Song m0", "Song o0"])
    expect(rows(instagram).nth(0)).to_contain_text("The Band")
    expect(rows(instagram).nth(0)).to_contain_text("Music")
    expect(rows(instagram).nth(0)).to_contain_text("0:30")
    expect(rows(instagram).nth(1)).to_contain_text("Original sound")
    expect(instagram.get_by_role("button", name="Open Song m0")).to_be_visible()
    expect(page.get_by_text(MODE_B)).to_be_visible()
    expect(page.get_by_text(NO_FACEBOOK)).to_have_count(0)

    tiktok = section(page, "TikTok shortlist")
    (row,) = rows(tiktok).all()
    expect(row.locator("th")).to_have_text("Late Train")
    expect(row).to_contain_text("AU")
    expect(row).to_contain_text("Calm, Dark")
    expect(row).to_contain_text("2")
    expect(row.get_by_text("Stale")).to_have_count(0)
    expect(page.get_by_text(NO_SHORTLIST)).to_have_count(0)

    lists = section(page, "Weekly lists")
    expect(lists.get_by_role("button")).to_have_text(list(feed.LABELS.values()))
    expect(lists.get_by_text("New list out")).to_be_visible()  # never opened, so every list is new to you
    expect(lists.get_by_role("switch", name=PING)).to_be_checked()

    sets = section(page, "Hashtag sets")
    item = sets.get_by_role("listitem", name="Gaming core")
    expect(item).to_contain_text("Gaming")
    expect(item).to_contain_text("#gaming #clips")
    assert opens(engine) == []


def test_paste_a_tiktok_link(page, console, engine, window):
    console.allow(r"status of 422")  # the refused short link, which Chrome logs
    opened(page, window.url + "/trends")
    form = page.get_by_role("form", name="Add a sound")
    form.get_by_label("Sound link").fill("https://vm.tiktok.com/ZMabc123/")
    form.get_by_role("button", name="Add").click()
    expect(form.get_by_text(SHORT_LINK)).to_be_visible()
    expect(form.get_by_label("Sound link")).to_have_attribute("aria-invalid", "true")
    assert trends_store.sounds(engine.db(), source="shortlist") == []

    form.get_by_label("Sound link").fill(LINK)
    expect(form.get_by_text(SHORT_LINK)).to_have_count(0)
    form.get_by_label("Region").select_option("AU")
    form.get_by_role("checkbox", name="Calm").check()
    form.get_by_label("Energy").select_option("2")
    form.get_by_role("button", name="Add").click()
    expect(toasts(page)).to_contain_text("Added to your shortlist")
    tiktok = section(page, "TikTok shortlist")
    expect(rows(tiktok).locator("th")).to_have_text(["Late Train"])  # the title the link carries
    expect(form.get_by_label("Sound link")).to_have_value("")
    expect(form.get_by_role("checkbox", name="Calm")).not_to_be_checked()
    (sound,) = trends_store.sounds(engine.db(), source="shortlist")
    assert (sound.title, sound.region, sound.mood_tags, sound.energy) == ("Late Train", "AU", ["calm"], 2)
    assert sound.url == LINK

    row = rows(tiktok).nth(0)
    row.get_by_role("button", name="Edit").click()
    dialog = page.get_by_role("dialog", name="Edit Late Train")
    dialog.get_by_label("Title").fill("Late Train (sped up)")
    dialog.get_by_label("Region").select_option("")
    dialog.get_by_label("Energy").select_option("4")
    dialog.get_by_role("button", name="Save").click()
    expect(dialog).to_have_count(0)
    expect(rows(tiktok).locator("th")).to_have_text(["Late Train (sped up)"])
    edited = trends_store.get_sound(engine.db(), sound.id)
    assert (edited.title, edited.region, edited.energy, edited.mood_tags) == (
        "Late Train (sped up)",
        None,
        4,
        ["calm"],
    )

    row.get_by_role("button", name="Open Late Train (sped up)").click()
    expect(toasts(page)).to_contain_text("Opened Late Train (sped up) in your browser.")
    assert opens(engine) == [LINK]

    row.get_by_role("button", name="Remove").click()
    ask = page.get_by_role("dialog", name="Remove this sound?")
    ask.get_by_role("button", name="Keep it").click()
    expect(row).to_be_visible()
    assert len(trends_store.sounds(engine.db(), source="shortlist")) == 1  # asked, not done
    row.get_by_role("button", name="Remove").click()
    page.get_by_role("dialog", name="Remove this sound?").get_by_role("button", name="Remove sound").click()
    expect(page.get_by_text(NO_SHORTLIST)).to_be_visible()
    assert trends_store.sounds(engine.db(), source="shortlist") == []


def test_badge_goes_when_a_list_opens(page, engine, window):
    opened(page, window.url + "/trends")
    lists = section(page, "Weekly lists")
    expect(lists.get_by_text("New list out")).to_be_visible()
    lists.get_by_role("button", name="SocialPilot: TikTok trends").click()
    expect(toasts(page)).to_contain_text("Opened SocialPilot: TikTok trends in your browser.")
    expect(lists.get_by_text("New list out")).to_have_count(0)
    assert opens(engine) == [feed.LISTS["socialpilot_tiktok"]]  # recorded by the runner, never run
    assert feed.badge(engine) is False

    # The feed shows a newer post, so the badge is back until a list is opened again
    seen = trends_store.read_of(engine.db(), feed.READ_ID)
    trends_store.save_read(engine.db(), seen.model_copy(update={"newest_at": seen.seen_at + 60}))
    page.reload()
    expect(lists.get_by_text("New list out")).to_be_visible()

    switch = lists.get_by_role("switch", name=PING)
    switch.uncheck()
    expect(toasts(page)).to_contain_text("Saved")
    assert engine.settings.trends.list_ping is False
    page.reload()
    expect(lists.get_by_role("switch", name=PING)).not_to_be_checked()


def test_filters_and_stale(page, engine, window):
    now = NOW.timestamp()
    drama = facebook_account(engine, "drama.clips")
    listed(engine, drama, now, ("m0", "music", "The Band", 30.0))
    trends_store.put_shortlist(
        engine.db(), shortlist(title="Late Train", region="AU", mood_tags=["calm"], energy=2, created_at=now)
    )
    trends_store.put_shortlist(
        engine.db(),
        shortlist(
            "7234567890123456780",
            title="Arcade Rush",
            region="US",
            mood_tags=["hype"],
            created_at=now - 15 * DAY,
        ),
    )
    opened(page, window.url + "/trends")
    tiktok = section(page, "TikTok shortlist")
    instagram = block(page, "drama.clips")
    expect(rows(tiktok).locator("th")).to_have_text(["Late Train", "Arcade Rush"])
    expect(rows(tiktok).nth(0).get_by_text("Stale")).to_have_count(0)
    expect(rows(tiktok).nth(1).get_by_text("Stale")).to_be_visible()  # added 15 days ago

    filters = page.get_by_role("group", name="Filter sounds")
    filters.get_by_role("switch", name="Hide stale").check()
    expect(rows(tiktok).locator("th")).to_have_text(["Late Train"])
    expect(rows(instagram).locator("th")).to_have_text(["Song m0"])
    filters.get_by_role("switch", name="Hide stale").uncheck()

    filters.get_by_label("Mood").select_option("hype")
    expect(rows(tiktok).locator("th")).to_have_text(["Arcade Rush"])
    expect(instagram.get_by_text(NO_MATCH)).to_be_visible()  # Instagram says no sound's mood
    filters.get_by_label("Region").select_option("AU")
    expect(rows(tiktok)).to_have_count(0)
    expect(tiktok.get_by_text(NO_MATCH)).to_be_visible()
    filters.get_by_label("Mood").select_option("")
    expect(rows(tiktok).locator("th")).to_have_text(["Late Train"])
    filters.get_by_label("Region").select_option("")

    filters.get_by_label("Platform").select_option("instagram")
    expect(page.get_by_role("heading", name="TikTok shortlist")).to_have_count(0)
    expect(rows(instagram).locator("th")).to_have_text(["Song m0"])
    filters.get_by_label("Platform").select_option("tiktok")
    expect(page.get_by_role("heading", name="Trending on Instagram")).to_have_count(0)
    expect(rows(section(page, "TikTok shortlist"))).to_have_count(2)
    expect(section(page, "Weekly lists")).to_be_visible()  # the lists and the sets aren't sounds to filter
    expect(section(page, "Hashtag sets")).to_be_visible()


def test_hashtag_sets(page, console, engine, window):
    console.allow(r"status of 422")  # the refused sets, which Chrome logs
    page.context.grant_permissions(CLIPBOARD, origin=window.url)
    opened(page, window.url + "/trends")
    form = page.get_by_role("form", name="New hashtag set")
    form.get_by_role("button", name="Add set").click()
    expect(form.get_by_text("Pick a niche for the set.")).to_be_visible()
    assert trends_store.sets(engine.db()) == []

    form.get_by_label("Niche").select_option("Gaming")
    form.get_by_label("Name").fill("Gaming core")
    form.get_by_label("Hashtags").fill("#gaming clips, #FYP")
    form.get_by_role("button", name="Add set").click()
    expect(toasts(page)).to_contain_text("Added the set")
    item = section(page, "Hashtag sets").get_by_role("listitem", name="Gaming core")
    expect(item).to_contain_text("#gaming #clips #FYP")
    expect(form.get_by_label("Name")).to_have_value("")
    (made,) = trends_store.sets(engine.db())
    assert (made.niche, made.name, made.tags) == ("Gaming", "Gaming core", ["gaming", "clips", "FYP"])

    item.get_by_role("button", name="Copy").click()
    expect(toasts(page)).to_contain_text("Copied 3 hashtags")
    assert page.evaluate(READ_CLIPBOARD) == "#gaming #clips #FYP"

    item.get_by_role("button", name="Edit").click()
    dialog = page.get_by_role("dialog", name="Edit Gaming core")
    expect(dialog.get_by_label("Hashtags")).to_have_value("#gaming #clips #FYP")
    dialog.get_by_label("Hashtags").fill("")
    dialog.get_by_role("button", name="Save").click()
    expect(dialog.get_by_text("Add at least one hashtag.")).to_be_visible()
    dialog.get_by_label("Hashtags").fill("#gaming #minecraft")
    dialog.get_by_role("button", name="Save").click()
    expect(dialog).to_have_count(0)
    expect(item).to_contain_text("#gaming #minecraft")
    assert trends_store.get_set(engine.db(), made.id).tags == ["gaming", "minecraft"]

    item.get_by_role("button", name="Delete").click()
    page.get_by_role("dialog", name="Delete this set?").get_by_role("button", name="Delete set").click()
    expect(section(page, "Hashtag sets").get_by_text(NO_SETS)).to_be_visible()
    assert trends_store.sets(engine.db()) == []
