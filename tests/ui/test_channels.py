"""The Channels page (SPEC 14.9 and 10.9, phase 5a): New clipping channel (also from ``?new=1``) with its
name, sources, accounts, options and schedule; one card per clipping channel with its sources, accounts,
last check and next one, Pause and Resume, Check now and when it opens again, Add source, History with why
each video was skipped, Edit and Delete, which names the drafts that go. Nothing leaves 127.0.0.1: YouTube
is a fake and the engine's jobs never start here."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from clipforge import repo
from clipforge.post import store as post_store
from clipforge.watch import store
from clipforge.watch.models import ChannelListing, WatchItem
from tests.fakes import Clock, FakeYouTube, fake_engine
from tests.posting import add_account, at
from tests.ui.conftest import live_server
from tests.ui.test_shell import opened, toasts
from tests.watching import add_channel, add_check, watched

NOW = at(7, 10)  # 7:10 am on Tue 3 Nov 2026 in Sydney
UC = "UCabcdefghijklmnopqrstuv"
LISTING = ChannelListing(
    channel_id=UC,
    title="A Podcast",
    url=f"https://www.youtube.com/channel/{UC}/videos",
    entries=[("bbbbbbbbbb2", "Episode 2"), ("aaaaaaaaaa1", "Episode 1")],
)
NOT_A_CHANNEL = (
    "That isn't a YouTube channel or video link. Paste a link like youtube.com/@name or one of its videos."
)


@pytest.fixture
def youtube():
    yt = FakeYouTube(channel_id=UC, channel="A Podcast", channel_url=f"https://www.youtube.com/channel/{UC}")
    yt.listing = LISTING
    return yt


@pytest.fixture
def engine(youtube):
    e = fake_engine(now=Clock(NOW), youtube=youtube)
    yield e
    e.close()


@pytest.fixture
def window(engine, page, monkeypatch):
    """The window over ``engine`` with nothing running, its clock at the engine's."""
    monkeypatch.setattr(engine.runner, "start", lambda: None)
    page.clock.set_fixed_time(NOW)
    with live_server(engine) as live:
        yield live


def card(page: Page, name: str):
    return page.get_by_role("article", name=name, exact=True)


def test_new_channel_with_a_source(page, engine, window, youtube):
    t0 = add_account(engine, "tiktok", "t0")
    add_account(engine, "instagram", "i0")
    page.goto(window.url + "/channels?new=1")  # the dialog opens over the rail, so not opened()
    dialog = page.get_by_role("dialog", name="New clipping channel")
    expect(dialog).to_be_visible()
    expect(page).to_have_url(re.compile(r"/channels$"))  # a reload doesn't open it again

    dialog.get_by_role("button", name="Save").click()
    expect(dialog.get_by_text("Give the clipping channel a name.")).to_be_visible()
    assert store.list_channels(engine.db()) == []

    dialog.get_by_label("Name").fill("Podcasts")
    dialog.get_by_label("YouTube channels").fill("https://www.youtube.com/@apodcast")
    dialog.get_by_label("Also clip the latest").select_option("2")
    dialog.get_by_role("checkbox", name="@t0").check()
    expect(dialog.get_by_role("checkbox", name="@i0")).not_to_be_checked()
    dialog.get_by_label("Hashtags").fill("#talk podcast")
    dialog.get_by_role("radio", name="I have permission").check()
    dialog.get_by_label("Check time").fill("08:00")
    dialog.get_by_label("New videos a day").select_option("3")
    dialog.get_by_role("radio", name="Economy models").check()
    dialog.get_by_role("button", name="Save").click()
    expect(dialog).to_have_count(0)
    expect(toasts(page)).to_contain_text("Podcasts saved")

    (channel,) = store.list_channels(engine.db())
    assert channel.name == "Podcasts"
    assert channel.account_ids == [t0.id]
    assert channel.options.hashtags == ["talk", "podcast"]
    assert (channel.rights_basis, channel.check_time) == ("permission", "08:00")
    assert (channel.daily_video_limit, channel.on_limit) == (3, "economy")
    (source,) = store.list_sources(engine.db(), channel.id)
    assert source.title == "A Podcast"
    assert youtube.called("list_channel")[0][2] == 2
    backfilled = [i.title for i in store.items_for(engine.db(), channel.id) if i.source_id == source.id]
    assert backfilled == ["Episode 2", "Episode 1"]  # the latest two, for the next check

    shown = card(page, "Podcasts")
    expect(shown).to_contain_text("A Podcast")
    expect(shown).to_contain_text("Episode 2")  # its newest upload
    expect(shown).to_contain_text("@t0")
    expect(shown).to_contain_text("Next check at 8:00 am today")
    expect(shown.get_by_text("Paused")).to_have_count(0)


def test_edit_changes_only_what_it_shows(page, engine, window):
    """Edit opens on the clipping channel as it is, and Save keeps the options the dialog doesn't show (the
    engine lays what it sends over the channel's own, a key at a time)."""
    from clipforge.models import ProjectOptions
    from clipforge.watch.models import ChannelOptions

    t0 = add_account(engine, "tiktok", "t0")
    kept = ChannelOptions(project=ProjectOptions(quality="fast", clip_count=4), hashtags=["talk"])
    channel = add_channel(engine, [t0], "Podcasts", options=kept, check_time="08:00")
    opened(page, window.url + "/channels")
    card(page, "Podcasts").get_by_role("button", name="Edit").click()
    dialog = page.get_by_role("dialog", name="Edit Podcasts")
    expect(dialog.get_by_label("Name")).to_have_value("Podcasts")
    expect(dialog.get_by_label("Clips per video")).to_have_value("4")
    expect(dialog.get_by_label("Hashtags")).to_have_value("#talk")
    expect(dialog.get_by_role("checkbox", name="@t0")).to_be_checked()
    dialog.get_by_label("Name").fill("Talks")
    dialog.get_by_label("Check time").fill("09:30")
    dialog.get_by_role("button", name="Save").click()
    expect(dialog).to_have_count(0)
    expect(toasts(page)).to_contain_text("Talks saved")
    changed = store.get_channel(engine.db(), channel.id)
    assert (changed.name, changed.check_time) == ("Talks", "09:30")
    assert (changed.options.project.quality, changed.options.project.clip_count) == ("fast", 4)
    assert len(store.list_channels(engine.db())) == 1  # changed, not made again
    expect(card(page, "Talks")).to_contain_text("It checks every day at 9:30 am.")


def test_save_while_the_accounts_load_keeps_them(page, engine, window):
    """Saving before the accounts have loaded leaves the clipping channel's accounts as they were, rather
    than sending none ticked."""
    t0 = add_account(engine, "tiktok", "t0")
    channel = add_channel(engine, [t0], "Podcasts")
    held = []
    page.route("**/api/accounts", lambda route: held.append(route))  # the accounts don't come back yet
    opened(page, window.url + "/channels")
    card(page, "Podcasts").get_by_role("button", name="Edit").click()
    dialog = page.get_by_role("dialog", name="Edit Podcasts")
    expect(dialog).to_contain_text("Loading the accounts.")
    dialog.get_by_label("Name").fill("Talks")
    dialog.get_by_role("button", name="Save").click()
    expect(dialog).to_have_count(0)
    changed = store.get_channel(engine.db(), channel.id)
    assert (changed.name, changed.account_ids) == ("Talks", [t0.id])
    for route in held:
        route.abort()


def test_bad_link_says_why(page, engine, window, console):
    console.allow(r"status of 422")  # the refused link, which Chrome logs
    add_channel(engine, [add_account(engine, "tiktok", "t0")], "Podcasts")
    opened(page, window.url + "/channels")
    card(page, "Podcasts").get_by_role("button", name="Add source").click()
    dialog = page.get_by_role("dialog", name="Add a source to Podcasts")
    dialog.get_by_label("Link").fill("https://vimeo.com/123")
    dialog.get_by_role("button", name="Add").click()
    expect(dialog.get_by_role("alert")).to_have_text(NOT_A_CHANNEL)
    expect(dialog.get_by_label("Link")).to_have_attribute("aria-invalid", "true")
    expect(dialog.get_by_label("Link")).to_have_value("https://vimeo.com/123")  # kept, to be put right
    assert len(store.list_sources(engine.db(), store.list_channels(engine.db())[0].id)) == 1

    dialog.get_by_label("Link").fill("@apodcast")
    dialog.get_by_role("button", name="Add").click()
    expect(dialog).to_have_count(0)
    expect(card(page, "Podcasts")).to_contain_text("A Podcast")


def test_pause_and_resume(page, engine, window):
    channel = add_channel(engine, [add_account(engine, "tiktok", "t0")], "Podcasts", check_time="08:00")
    opened(page, window.url + "/channels")
    shown = card(page, "Podcasts")
    expect(shown).to_contain_text("Next check at 8:00 am today")
    shown.get_by_role("button", name="Pause watching").click()
    expect(shown.get_by_text("Paused", exact=True)).to_be_visible()
    expect(shown).to_contain_text("No checks while it is paused.")
    assert store.get_channel(engine.db(), channel.id).paused is True
    shown.get_by_role("button", name="Resume watching").click()
    expect(shown.get_by_text("Paused", exact=True)).to_have_count(0)
    expect(shown).to_contain_text("Next check at 8:00 am today")
    assert store.get_channel(engine.db(), channel.id).paused is False


def test_check_now_then_too_soon(page, engine, window):
    channel = add_channel(engine, [add_account(engine, "tiktok", "t0")], "Podcasts", check_time="08:00")
    opened(page, window.url + "/channels")
    shown = card(page, "Podcasts")
    check_now = shown.get_by_role("button", name="Check now")
    check_now.click()
    expect(toasts(page)).to_contain_text("Checking Podcasts now.")
    expect(shown).to_contain_text("7:10 am: checking now")
    (check,) = store.checks_for(engine.db(), channel.id)
    assert check.manual is True

    # Once it finishes, Check now waits 10 minutes from its start, and says when it opens.
    store.update_check(engine.db(), check.id, finished_at=NOW.timestamp(), found=0, drafts=0)
    engine.events.publish("clip_channel", {"id": channel.id})
    expect(shown).to_contain_text("7:10 am: no new videos, no drafts")
    expect(check_now).to_be_disabled()
    expect(shown).to_contain_text("Check now opens at 7:20 am.")


def test_history_shows_reasons(page, engine, window):
    channel = add_channel(engine, [add_account(engine, "tiktok", "t0")], "Podcasts")
    check = add_check(engine, channel)
    watched(engine, channel, check, [80])
    (source,) = store.list_sources(engine.db(), channel.id)
    store.add_item(
        engine.db(),
        WatchItem(
            id=repo.new_id("wi"),
            source_id=source.id,
            clip_channel_id=channel.id,
            check_id=check.id,
            video_id="ccccccccccc",
            title="A Short one",
            status="skipped",
            reason="short",
            first_seen_at=0,
            updated_at=0,
        ),
    )
    store.update_check(engine.db(), check.id, found=2, skipped=1, clipped=1, drafts=0)
    opened(page, window.url + "/channels")
    card(page, "Podcasts").get_by_role("button", name="History").click()
    dialog = page.get_by_role("dialog", name="Podcasts history")
    expect(dialog).to_contain_text("7:10 am: 2 new videos, 1 skipped as a Short, no drafts")
    short = dialog.get_by_role("listitem").filter(has_text="A Short one")
    expect(short).to_contain_text("Skipped")
    expect(short).to_contain_text("A Short.")
    expect(short.get_by_role("link", name="A Short one")).to_have_attribute(
        "href", "https://www.youtube.com/watch?v=ccccccccccc"
    )
    expect(dialog.get_by_role("listitem")).to_have_count(2)
    expect(dialog.get_by_role("listitem").filter(has_text="Untitled video")).to_contain_text("Clipping")


def test_delete_names_its_drafts(page, engine, window):
    t0 = add_account(engine, "tiktok", "t0", daily_cap=2)
    channel = add_channel(engine, [t0], "Podcasts")
    pid, _ = watched(engine, channel, add_check(engine, channel), [90, 80])
    from clipforge.watch import drafts

    drafts.settle(engine, pid)
    made = drafts.live_drafts(engine)
    assert len(made) == 2
    opened(page, window.url + "/channels")
    card(page, "Podcasts").get_by_role("button", name="Delete").click()
    ask = page.get_by_role("dialog", name="Delete Podcasts?")
    expect(ask).to_contain_text("Its 2 drafts are discarded.")
    ask.get_by_role("button", name="Cancel").click()
    assert store.get_channel(engine.db(), channel.id) is not None

    card(page, "Podcasts").get_by_role("button", name="Delete").click()
    page.get_by_role("dialog", name="Delete Podcasts?").get_by_role("button", name="Delete").click()
    expect(card(page, "Podcasts")).to_have_count(0)
    assert store.get_channel(engine.db(), channel.id) is None
    assert {post_store.get_post(engine.db(), p.id).status for p in made} == {"discarded"}
    expect(page.get_by_role("heading", name="No clipping channels yet")).to_be_visible()


def test_no_accounts_links_to_accounts(page, window):
    opened(page, window.url + "/channels")
    expect(page.get_by_role("heading", name="No clipping channels yet")).to_be_visible()
    page.get_by_role("button", name="New clipping channel").first.click()
    dialog = page.get_by_role("dialog", name="New clipping channel")
    expect(dialog).to_contain_text("No accounts are linked yet.")
    dialog.get_by_role("link", name="Accounts").click()
    expect(page).to_have_url(re.compile(r"/accounts$"))
