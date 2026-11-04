"""The Campaigns page and a campaign's own page (SPEC 14.9, 14.11 and 10.10, phase 5b): the sheet with a row
per campaign, its money in the campaign's currency, a totals line per currency and headers that sort; New
campaign from pasted rules, with each field Claude filled marked until it is edited and a tick needed before
Save; the campaign page with its rules, its links opened by macOS ``open``, Mark ended, Delete and its
submissions moved along with their views; the Campaign picker on a clipping channel's dialog and on a
project's page; a linked draft's card with its rules checked and its hand rules ticked, and Unlink from
campaign; and Settings' Marketplaces. Nothing leaves 127.0.0.1: Claude is a fake and ``open`` is recorded,
never run."""

from __future__ import annotations

import re
import shutil
import time
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from clipforge import repo
from clipforge.campaigns import parse, store
from clipforge.campaigns.models import Metric, Submission
from clipforge.campaigns.rules import hand_key
from clipforge.post import store as post_store
from clipforge.watch import drafts
from clipforge.watch import store as watch_store
from tests.fakes import Clock, FakeProvider, FakePublisher, fake_engine
from tests.posting import PACK, add_account, add_campaign, at, bare_post, linked_clip, posted
from tests.ui.conftest import live_server
from tests.ui.seed import picture, seed_project
from tests.ui.test_post_dialog import confirm_button, rows, start, to_review
from tests.ui.test_shell import opened, toasts
from tests.watching import add_channel, add_check, watched

NOW = at(7, 10)  # 7:10 am on Tue 3 Nov 2026 in Sydney
RULES = "Earn $1.50 per 1,000 views on TikTok. Tag #MrBeast."
SUBMIT = "https://whop.com/beast/submit"
LIVE = "https://www.tiktok.com/@t1/video/7300000000000000001"
EMPTY = "No campaigns yet. Paste a campaign's rules to make one."
TICK_FIRST = "Tick I've checked the filled fields first."
TALK = "Why we wait"
HAND = "No reaction content"
WITHDRAW_WRONG = "A Withdraw link must be one web address starting with https:// or http://."
PICKY_REFUSES = "The campaign Picky clips doesn't allow footage from this channel."


def answer(req):
    """Claude's reading of RULES: the name, the rate and the hashtag, every other field null."""
    given = {"name": "Beast clips", "rate_per_1000": 1.5, "hashtags": ["#MrBeast"]}
    return {name: None for name in parse.RulesAnswer.model_fields} | given


@pytest.fixture
def engine():
    publishers = {"tiktok:browser": FakePublisher()}
    e = fake_engine(now=Clock(NOW), publishers=publishers, provider=FakeProvider({"campaign": answer}))
    yield e
    e.close()


@pytest.fixture
def window(engine, page, monkeypatch):
    """The window over ``engine`` with nothing running, its clock at the engine's."""
    monkeypatch.setattr(engine.runner, "start", lambda: None)
    page.clock.set_fixed_time(NOW)
    with live_server(engine) as live:
        yield live


def opened_links(engine) -> list[list[str]]:
    return [c for c in engine.run_cmd.calls if c[:1] == ["open"]]


def submission(engine, campaign, views: int | None = None, status: str = "to_submit") -> Submission:
    """A submission to ``campaign`` of a bare posted post, with a hand count of ``views``."""
    now = time.time()
    post_id = bare_post(engine.db())
    made = Submission(
        id=repo.new_id("sb"),
        post_id=post_id,
        campaign_id=campaign.id,
        status=status,
        created_at=now,
        updated_at=now,
    )
    store.add_submission(engine.db(), made)
    if views is not None:
        store.add_metric(engine.db(), Metric(post_id=post_id, captured_at=now, source="hand", views=views))
    return made


def sheet_row(page: Page, name: str):
    return page.get_by_role("row").filter(has=page.get_by_role("link", name=name, exact=True))


def read_rules(page: Page):
    """New campaign opened, RULES pasted and read: the dialog."""
    page.get_by_role("button", name="New campaign").click()
    dialog = page.get_by_role("dialog", name="New campaign")
    dialog.get_by_label("Campaign rules").fill(RULES)
    dialog.get_by_role("button", name="Read the rules").click()
    expect(dialog.get_by_label("Name", exact=True)).to_have_value("Beast clips")
    return dialog


def test_new_campaign_from_pasted_rules(page, engine, window):
    opened(page, window.url + "/campaigns")
    expect(page.get_by_text(EMPTY)).to_be_visible()
    dialog = read_rules(page)
    expect(dialog.get_by_label("Rate per 1,000 views")).to_have_value("1.5")
    expect(dialog.get_by_label("Hashtags every caption needs")).to_have_value("#MrBeast")
    marks = dialog.get_by_text("Check this", exact=True)
    expect(marks).to_have_count(3)  # the name, the rate and the hashtag
    expect(dialog.get_by_label("Marketplace")).to_have_value("")

    dialog.get_by_label("Name", exact=True).fill("Beast clips TikTok")
    expect(marks).to_have_count(2)  # an edited field is checked
    dialog.get_by_label("Marketplace").fill("Whop")
    expect(marks).to_have_count(2)  # one Claude didn't fill never had a mark
    dialog.get_by_label("Submit page link").fill(SUBMIT)
    dialog.get_by_role("checkbox", name="I've checked the filled fields").check()
    dialog.get_by_role("button", name="Save").click()
    expect(dialog).to_have_count(0)
    expect(toasts(page)).to_contain_text("Beast clips TikTok saved")

    (campaign,) = store.list_campaigns(engine.db())
    assert (campaign.name, campaign.marketplace, campaign.rate_per_1000) == (
        "Beast clips TikTok",
        "Whop",
        1.5,
    )
    assert campaign.required.hashtags == ["MrBeast"]
    assert campaign.submit_url == SUBMIT
    assert campaign.rules_text == RULES
    row = sheet_row(page, "Beast clips TikTok")
    expect(row).to_contain_text("Whop")
    expect(row).to_contain_text("US$1.50")
    expect(page.get_by_text(EMPTY)).to_have_count(0)


def test_save_needs_the_check_tick(page, engine, window):
    opened(page, window.url + "/campaigns")
    dialog = read_rules(page)
    dialog.get_by_label("Marketplace").fill("Whop")
    dialog.get_by_role("button", name="Save").click()
    expect(dialog.get_by_text(TICK_FIRST)).to_be_visible()
    assert store.list_campaigns(engine.db()) == []

    dialog.get_by_role("checkbox", name="I've checked the filled fields").check()
    expect(dialog.get_by_text(TICK_FIRST)).to_have_count(0)
    dialog.get_by_role("button", name="Save").click()
    expect(dialog).to_have_count(0)
    assert [c.name for c in store.list_campaigns(engine.db())] == ["Beast clips"]


def test_a_field_the_engine_refuses_says_so_beside_it(page, engine, window, console):
    """Filled by hand, with nothing read, Save needs no tick, and the engine's sentence sits by its field."""
    console.allow(r"status of 422")  # the refused rate and the empty paste, which Chrome logs
    opened(page, window.url + "/campaigns")
    page.get_by_role("button", name="New campaign").click()
    dialog = page.get_by_role("dialog", name="New campaign")
    expect(dialog.get_by_role("checkbox", name="I've checked the filled fields")).to_have_count(0)
    dialog.get_by_label("Name", exact=True).fill("Hand made")
    dialog.get_by_label("Marketplace").fill("Vyro")
    dialog.get_by_label("Rate per 1,000 views").fill("a lot")
    dialog.get_by_role("button", name="Save").click()
    expect(dialog.get_by_text("The rate per 1,000 views must be a number of 0 or more.")).to_be_visible()
    assert store.list_campaigns(engine.db()) == []
    dialog.get_by_role("button", name="Read the rules").click()
    expect(dialog.get_by_text("Paste the campaign's rules first.")).to_be_visible()


def test_sheet_rows_sort_and_total_per_currency(page, engine, window):
    small = add_campaign(
        engine, name="Small", rate_per_1000=1.0, budget_left=500.0, budget_entered_on="2026-10-03"
    )
    big = add_campaign(engine, name="Big", marketplace="Vyro", rate_per_1000=2.0, ends_on="2026-11-30")
    aussie = add_campaign(engine, name="Aussie", marketplace="Vyro", currency="AUD", rate_per_1000=3.0)
    submission(engine, small, views=1000, status="submitted")
    submission(engine, big, views=5000, status="approved")
    submission(engine, aussie, views=2000, status="submitted")
    t0 = add_account(engine, "tiktok", "t0")
    add_channel(engine, [t0], "Beast channel", campaign_id=big.id)
    opened(page, window.url + "/campaigns")

    names = page.locator("tbody tr th")
    expect(names).to_have_text(
        ["Big", "Aussie", "Small"]
    )  # the highest estimated first, whatever its currency
    big_row = sheet_row(page, "Big")
    for shown in ("Vyro", "US$2.00", "30 Nov", "Beast channel", "5,000", "US$10.00"):
        expect(big_row).to_contain_text(shown)
    expect(sheet_row(page, "Small")).to_contain_text("entered 3 Oct")
    expect(sheet_row(page, "Aussie")).to_contain_text("A$6.00")

    page.get_by_role("button", name="Campaign", exact=True).click()  # the title's header: A to Z
    expect(names).to_have_text(["Aussie", "Big", "Small"])
    expect(page.get_by_role("columnheader", name="Campaign", exact=True)).to_have_attribute(
        "aria-sort", "ascending"
    )
    page.get_by_role("button", name="Campaign", exact=True).click()
    expect(names).to_have_text(["Small", "Big", "Aussie"])

    totals = page.locator("tfoot tr")
    expect(totals).to_have_count(2)  # one a currency, never added together
    expect(totals.nth(0)).to_contain_text("AUD")
    expect(totals.nth(0)).to_contain_text("A$6.00")
    expect(totals.nth(1)).to_contain_text("USD")
    expect(totals.nth(1)).to_contain_text("6,000")
    expect(totals.nth(1)).to_contain_text("US$11.00")
    expect(page.get_by_text("Estimates from your entries")).to_be_visible()

    sheet_row(page, "Big").get_by_role("link", name="Big").click()
    expect(page).to_have_url(re.compile(rf"/campaigns/{big.id}$"))
    expect(page.get_by_role("heading", name="Big", level=1)).to_be_visible()


def three_campaigns(engine):
    """Big on Vyro ends tomorrow (a to-do item of its own), Small on Whop has a live post to submit, and Old
    is over."""
    big = add_campaign(engine, name="Big", marketplace="Vyro", rate_per_1000=2.0, ends_on="2026-11-04")
    small = add_campaign(engine, name="Small", rate_per_1000=1.0, submit_url=SUBMIT)
    old = add_campaign(engine, name="Old", marketplace="Clipify", rate_per_1000=1.0, status="ended")
    submission(engine, big, views=5000, status="submitted")
    submission(engine, old, views=1000, status="submitted")
    live = posted(engine, linked_clip(engine, small), add_account(engine, "tiktok", "t1"))
    post_store.update_post(engine.db(), live.id, url=LIVE)


def test_sheet_search_and_filters(page, engine, window):
    three_campaigns(engine)
    opened(page, window.url + "/campaigns")
    names = page.locator("tbody tr th a")  # the names, without Old's "Ended"
    totals = page.locator("tfoot tr")
    expect(names).to_have_count(3)

    search = page.get_by_role("searchbox", name="Search campaigns")
    expect(search).to_have_attribute("placeholder", "Name, marketplace or channel")
    search.fill("big")
    expect(names).to_have_text(["Big"])
    expect(totals).to_have_count(1)
    expect(totals).to_contain_text("US$10.00")  # the totals add up the rows shown
    search.fill("")
    expect(names).to_have_count(3)

    market = page.get_by_role("combobox", name="Marketplace")
    expect(market.locator("option")).to_have_text(["All marketplaces", "Clipify", "Vyro", "Whop"])
    market.select_option("Whop")
    expect(names).to_have_text(["Small"])
    market.select_option(label="All marketplaces")

    state = page.get_by_role("combobox", name="State")
    expect(state.locator("option")).to_have_text(["Any state", "Active", "Ending within 3 days", "Ended"])
    state.select_option(label="Ending within 3 days")
    expect(names).to_have_text(["Big"])
    state.select_option(label="Ended")
    expect(names).to_have_text(["Old"])
    expect(totals).to_contain_text("US$1.00")
    state.select_option(label="Any state")

    page.get_by_role("checkbox", name="Needs me").check()
    expect(names).to_have_text(["Big", "Small"])  # Old has nothing on today's list


def test_column_menu_hides_and_remembers(page, engine, window):
    three_campaigns(engine)
    opened(page, window.url + "/campaigns")
    views = page.get_by_role("columnheader", name="Views", exact=True)
    totals = page.locator("tfoot tr")
    expect(views).to_have_count(1)
    expect(totals).to_contain_text("6,000")

    page.get_by_role("button", name="Columns").click()
    item = page.get_by_role("menuitemcheckbox", name="Views")
    expect(item).to_be_checked()
    expect(page.get_by_role("menuitemcheckbox", name="Campaign")).to_have_count(0)  # the name always shows
    item.click()
    expect(views).to_have_count(0)
    expect(totals).not_to_contain_text("6,000")  # the totals row loses its views cell too
    expect(totals).to_contain_text("US$11.00")

    page.reload()
    expect(page.locator("tbody tr th")).to_have_count(3)
    expect(views).to_have_count(0)  # remembered in this browser
    page.get_by_role("button", name="Columns").click()
    page.get_by_role("menuitemcheckbox", name="Views").click()
    expect(views).to_have_count(1)
    expect(totals).to_contain_text("6,000")


def test_no_match_offers_clear_filters(page, engine, window):
    three_campaigns(engine)
    opened(page, window.url + "/campaigns")
    page.get_by_role("searchbox", name="Search campaigns").fill("zzz")
    expect(page.get_by_text("No campaigns match these filters.")).to_be_visible()
    expect(page.locator("tfoot")).to_have_count(0)
    page.get_by_role("button", name="Clear filters").click()
    expect(page.locator("tbody tr th")).to_have_count(3)
    expect(page.get_by_role("searchbox", name="Search campaigns")).to_have_value("")


def test_submission_moves_on_the_campaign_page(page, engine, window):
    campaign = add_campaign(engine, rate_per_1000=1.5, submit_url=SUBMIT)
    account = add_account(engine, "tiktok", "t0")
    post = posted(engine, linked_clip(engine, campaign), account)
    opened(page, window.url + f"/campaigns/{campaign.id}")
    (made,) = store.list_submissions(engine.db(), campaign.id)
    assert made.post_id == post.id
    row = page.get_by_role("article")
    expect(row).to_contain_text("To submit")
    expect(row).to_contain_text("@t0")

    row.get_by_role("button", name="Mark submitted").click()
    expect(row).to_contain_text("Submitted")
    expect(row.get_by_role("button", name="Back to To submit")).to_be_visible()
    assert store.get_submission(engine.db(), made.id).status == "submitted"

    row.get_by_role("button", name="Enter views").click()
    row.get_by_label("Views").fill("2,000")
    row.get_by_role("button", name="Save views").click()
    expect(row).to_contain_text("2,000 views")
    expect(row).to_contain_text("US$3.00")
    assert store.latest_metric(engine.db(), post.id).views == 2000

    row.get_by_role("button", name="Rejected").click()
    row.get_by_label("Reason").fill("Wrong hashtag")
    row.get_by_role("button", name="Mark rejected").click()
    expect(row).to_contain_text("Wrong hashtag")
    assert store.get_submission(engine.db(), made.id).reason == "Wrong hashtag"

    row.get_by_role("button", name="Approved").click()
    expect(row.get_by_role("button", name="Paid")).to_be_visible()
    row.get_by_role("button", name="Paid").click()
    row.get_by_role("button", name="Mark paid").click()
    expect(row.get_by_text("Enter the amount paid.")).to_be_visible()  # no amount, no move
    assert store.get_submission(engine.db(), made.id).status == "approved"
    row.get_by_label("Amount paid").fill("3")
    row.get_by_role("button", name="Mark paid").click()
    expect(row).to_contain_text("Paid US$3.00 on 3 Nov")
    moved = store.get_submission(engine.db(), made.id)
    assert (moved.status, moved.paid_amount, moved.paid_on) == ("paid", 3.0, "2026-11-03")

    show = page.get_by_role("group", name="Show")
    show.get_by_role("radio", name="To submit").check()
    expect(page.get_by_role("article")).to_have_count(0)
    expect(page.get_by_text("No submissions are To submit.")).to_be_visible()
    show.get_by_role("radio", name="Paid").check()
    expect(page.get_by_role("article")).to_have_count(1)


def test_open_submit_page_records_open(page, engine, window):
    campaign = add_campaign(engine, submit_url=SUBMIT)
    opened(page, window.url + f"/campaigns/{campaign.id}")
    expect(page.get_by_role("button", name="Open campaign page")).to_have_count(0)  # it has none
    page.get_by_role("button", name="Open submit page").click()
    expect(toasts(page)).to_contain_text("Opened the submit page in your browser.")
    assert opened_links(engine) == [["open", SUBMIT]]


def test_mark_ended_and_delete(page, engine, window, console):
    console.allow(r"status of 409")  # the refused delete, which Chrome logs
    kept = add_campaign(engine, name="Kept")
    submission(engine, kept)
    opened(page, window.url + f"/campaigns/{kept.id}")
    page.get_by_role("button", name="Mark ended").click()
    expect(page.get_by_text("You marked Kept ended.")).to_be_visible()
    assert store.get_campaign(engine.db(), kept.id).status == "ended"
    page.get_by_role("button", name="Mark active").click()
    expect(page.get_by_text("You marked Kept ended.")).to_have_count(0)

    page.get_by_role("button", name="Delete").click()
    page.get_by_role("dialog").get_by_role("button", name="Delete").click()
    expect(toasts(page)).to_contain_text("This campaign has 1 submission, so mark it ended instead.")
    assert store.get_campaign(engine.db(), kept.id) is not None

    gone = add_campaign(engine, name="Gone")
    opened(page, window.url + f"/campaigns/{gone.id}")
    page.get_by_role("button", name="Delete").click()
    page.get_by_role("dialog").get_by_role("button", name="Delete").click()
    expect(page).to_have_url(re.compile(r"/campaigns$"))
    assert store.get_campaign(engine.db(), gone.id) is None
    expect(sheet_row(page, "Kept")).to_have_count(1)


def test_rail_has_campaigns_after_channels(page, engine, window):
    opened(page, window.url + "/campaigns")
    names = page.get_by_role("navigation", name="Pages").get_by_role("link").all_inner_texts()
    assert names[names.index("Channels") + 1] == "Campaigns"
    link = page.get_by_role("link", name="Campaigns", exact=True)
    expect(link).to_have_attribute("aria-current", "page")
    expect(page.get_by_role("heading", name="Campaigns", level=1)).to_be_visible()


def campaign_draft(engine, campaign, video: Path | None = None):
    """Podcasts, linked to ``campaign`` with one TikTok account, its check's one video clipped and the clip
    drafted to the account: the draft. With ``video``, the clip's render is that video, which Chrome plays
    once the pointer is on the card."""
    engine.thumb_fetch = picture
    t0 = add_account(engine, "tiktok", "t0")
    channel = add_channel(
        engine, [t0], "Podcasts", campaign_id=campaign.id, rights_basis="clipping_programme"
    )
    packaging = PACK | {"hook_title": TALK}
    pid, (clip,) = watched(engine, channel, add_check(engine, channel), [90], packaging=packaging)
    repo.update_project(engine.db(), pid, campaign_id=campaign.id, rights_basis="clipping_programme")
    for render in repo.list_renders(engine.db(), clip.id) if video else []:  # where the media route serves it
        served = engine.paths.home / "projects" / pid / "clips" / clip.id / f"v{render.version}" / "clip.mp4"
        served.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(video, served)
        engine.db().execute("UPDATE renders SET path = ? WHERE id = ?", (str(served), render.id))
    drafts.settle(engine, pid)
    (draft,) = [p for p in drafts.live_drafts(engine) if p.clip_id == clip.id]
    return draft


def draft_card(page: Page):
    return page.get_by_role("article", name=f"{TALK} to @t0", exact=True)


def patched(page: Page, path: str):
    return page.expect_response(lambda r: r.url.endswith(path) and r.request.method == "PATCH")


def test_link_a_channel_from_its_dialog(page, engine, window):
    beast = add_campaign(engine)
    add_campaign(engine, name="Old clips", status="ended")
    channel = add_channel(
        engine, [add_account(engine, "tiktok", "t0")], "Podcasts", rights_basis="permission"
    )
    opened(page, window.url + "/channels")
    card = page.get_by_role("article", name="Podcasts", exact=True)
    expect(card).not_to_contain_text("Beast clips")
    card.get_by_role("button", name="Edit").click()
    dialog = page.get_by_role("dialog", name="Edit Podcasts")
    picker = dialog.get_by_label("Campaign", exact=True)
    expect(picker).to_have_value("")
    expect(picker.locator("option")).to_have_text(["None", "Beast clips"])  # an ended campaign isn't offered
    picker.select_option(label="Beast clips")
    dialog.get_by_role("button", name="Save").click()
    expect(dialog).to_have_count(0)
    linked = watch_store.get_channel(engine.db(), channel.id)
    assert (linked.campaign_id, linked.rights_basis) == (beast.id, "clipping_programme")
    expect(card).to_contain_text("For Beast clips")

    # Saving it again leaves the link as it is, and None unlinks it.
    card.get_by_role("button", name="Edit").click()
    expect(picker).to_have_value(beast.id)
    sent: list[str] = []
    page.on("request", lambda r: sent.append(r.post_data or "") if r.method == "PATCH" else None)
    dialog.get_by_label("Name").fill("Talks")
    dialog.get_by_role("button", name="Save").click()
    expect(dialog).to_have_count(0)
    assert sent and all("campaign_id" not in body for body in sent)
    page.get_by_role("article", name="Talks", exact=True).get_by_role("button", name="Edit").click()
    dialog = page.get_by_role("dialog", name="Edit Talks")
    dialog.get_by_label("Campaign", exact=True).select_option(label="None")
    dialog.get_by_role("button", name="Save").click()
    expect(dialog).to_have_count(0)
    unlinked = watch_store.get_channel(engine.db(), channel.id)
    assert (unlinked.campaign_id, unlinked.rights_basis) == (None, "not_cleared")
    expect(page.get_by_role("article", name="Talks", exact=True)).not_to_contain_text("Beast clips")


def test_an_ended_campaign_says_why_on_its_channel(page, engine, window):
    old = add_campaign(engine, name="Old clips", status="ended")
    channel = add_channel(engine, [add_account(engine, "tiktok", "t0")], "Podcasts")
    watch_store.update_channel(engine.db(), channel.id, campaign_id=old.id, rights_basis="clipping_programme")
    opened(page, window.url + "/channels")
    card = page.get_by_role("article", name="Podcasts", exact=True)
    expect(card).to_contain_text("For Old clips")
    expect(card).to_contain_text(
        "You marked Old clips ended. It won't check again until you link it to another"
    )
    card.get_by_role("button", name="Edit").click()
    picker = page.get_by_role("dialog", name="Edit Podcasts").get_by_label("Campaign", exact=True)
    expect(picker).to_have_value(old.id)  # the ended campaign it is linked to stays shown
    expect(picker.locator("option")).to_have_text(["None", "Old clips (ended)"])


def test_link_a_project_from_its_page(page, engine, window, console):
    console.allow(r"status of 409")  # the refused link, which Chrome logs
    beast = add_campaign(engine)
    add_campaign(engine, name="Picky clips", footage={"videos": ["zzzzzzzzzzz"]})
    pid = seed_project(engine)
    opened(page, f"{window.url}/projects/{pid}")
    picker = page.get_by_label("Campaign", exact=True)
    expect(picker).to_have_value("")
    with patched(page, f"/api/projects/{pid}"):
        picker.select_option(label="Picky clips")
    expect(page.get_by_text(PICKY_REFUSES)).to_be_visible()
    expect(picker).to_have_value("")  # back as it is
    assert engine.project(pid).campaign_id is None

    with patched(page, f"/api/projects/{pid}"):
        picker.select_option(label="Beast clips")
    expect(page.get_by_text(PICKY_REFUSES)).to_have_count(0)
    expect(picker).to_have_value(beast.id)
    linked = engine.project(pid)
    assert (linked.campaign_id, linked.rights_basis) == (beast.id, "clipping_programme")
    with patched(page, f"/api/projects/{pid}"):
        picker.select_option(label="None")
    assert engine.project(pid).campaign_id is None


def test_draft_card_lists_rules_and_ticks_one(page, engine, window, video):
    beast = add_campaign(engine, required={"hashtags": ["Beast"]}, hand_rules=[HAND])
    draft = campaign_draft(engine, beast, video)
    opened(page, window.url)
    card = draft_card(page)
    expect(card).to_contain_text("For Beast clips")
    rules = card.get_by_role("list", name="Beast clips rules")
    expect(rules.get_by_role("listitem")).to_have_count(3)  # the hashtag, disclosure on TikTok, the hand rule
    expect(rules).to_contain_text("#Beast in the hashtags")
    hand = rules.get_by_role("checkbox", name=HAND)
    expect(hand).not_to_be_checked()
    expect(card.get_by_text("Check this yourself, then tick it.")).to_have_count(1)  # said once, by the rule

    with patched(page, f"/api/posts/{draft.id}"):
        hand.check()
    expect(hand).to_be_checked()
    assert post_store.get_post(engine.db(), draft.id).state["rule_ticks"] == [hand_key(HAND)]
    expect(card.get_by_text("Check this yourself, then tick it.")).to_have_count(0)
    page.reload()
    expect(draft_card(page).get_by_role("checkbox", name=HAND)).to_be_checked()

    with patched(page, f"/api/posts/{draft.id}"):
        draft_card(page).get_by_role("checkbox", name=HAND).uncheck()
    assert "rule_ticks" not in post_store.get_post(engine.db(), draft.id).state


def test_post_dialog_ticks_a_hand_rule(page, engine, window):
    """A project you started by hand, linked to a campaign: Review lists each post's rules, the one you
    check yourself as a tick box that holds Confirm and post back until it is ticked, and the tick is kept
    on the post it makes."""
    beast = add_campaign(engine, hand_rules=[HAND])
    pid = seed_project(engine, clips=1, rendered=1)
    repo.update_project(engine.db(), pid, campaign_id=beast.id, rights_basis="clipping_programme")
    add_account(engine, "tiktok", "tt_one")
    dialog = start(page, window, pid, 0, accounts=("@tt_one",))
    to_review(dialog)
    row = rows(dialog).first
    rules = row.get_by_role("list", name="Beast clips rules")
    expect(rules.get_by_role("listitem")).to_have_count(2)  # disclosure on TikTok, the hand rule
    hand = rules.get_by_role("checkbox", name=HAND)
    expect(hand).not_to_be_checked()
    expect(row.get_by_text("Check this yourself, then tick it.")).to_have_count(1)  # said once, by the rule
    expect(confirm_button(dialog)).to_be_disabled()
    expect(dialog.get_by_text("Tick the rules you checked to post.")).to_be_visible()  # nothing is red
    hand.check()
    expect(hand).to_be_checked()
    expect(row.get_by_text("Check this yourself, then tick it.")).to_have_count(0)
    expect(confirm_button(dialog)).to_be_enabled()
    confirm_button(dialog).click()
    expect(page).to_have_url(re.compile(r"/queue$"))
    (made,) = post_store.list_posts(engine.db())
    assert made.state == {"rule_ticks": [hand_key(HAND)], "campaign_id": beast.id}


def test_unlink_makes_an_ordinary_draft(page, engine, window, video):
    beast = add_campaign(engine, hand_rules=[HAND])
    draft = campaign_draft(engine, beast, video)
    opened(page, window.url)
    card = draft_card(page)
    expect(card.get_by_role("list", name="Beast clips rules")).to_be_visible()
    with patched(page, f"/api/posts/{draft.id}"):
        card.get_by_role("button", name="Unlink from campaign").click()
    expect(toasts(page)).to_contain_text("Unlinked from Beast clips")
    expect(card.get_by_role("list", name="Beast clips rules")).to_have_count(0)
    expect(card).not_to_contain_text("Beast clips")
    assert post_store.get_post(engine.db(), draft.id).state.get("off_campaign") is True


def test_marketplace_settings_save(page, engine, window, console):
    console.allow(r"status of 422")  # the refused link, which Chrome logs
    add_campaign(engine)  # on Whop
    add_campaign(engine, name="Vyro clips", marketplace="Vyro")
    add_campaign(engine, name="More clips", marketplace="whop")  # the same marketplace, spelt another way
    opened(page, window.url + "/settings")
    section = page.get_by_role("region", name="Marketplaces")
    expect(section.get_by_role("group")).to_have_count(2)
    whop = section.get_by_role("group", name="Whop")
    link = whop.get_by_label("Withdraw link")
    link.fill("whop dot com")
    link.press("Enter")
    expect(whop.get_by_text(WITHDRAW_WRONG)).to_be_visible()
    assert "Whop" not in engine.settings.marketplaces

    link.fill("https://whop.com/wallet")
    with page.expect_response(lambda r: r.url.endswith("/api/settings") and r.request.method == "PUT"):
        link.press("Enter")
    expect(whop.get_by_text(WITHDRAW_WRONG)).to_have_count(0)
    minimum = whop.get_by_label("Minimum payout")
    minimum.fill("25")
    with page.expect_response(lambda r: r.url.endswith("/api/settings") and r.request.method == "PUT"):
        minimum.press("Enter")
    saved = engine.settings.marketplaces["Whop"]
    assert (saved.withdraw_url, saved.min_payout) == ("https://whop.com/wallet", 25.0)
    assert "Vyro" not in engine.settings.marketplaces  # a row left alone saves nothing

    page.reload()
    whop = page.get_by_role("region", name="Marketplaces").get_by_role("group", name="Whop")
    expect(whop.get_by_label("Withdraw link")).to_have_value("https://whop.com/wallet")
    expect(whop.get_by_label("Minimum payout")).to_have_value("25")


def test_marketplaces_without_campaigns_say_so(page, window):
    opened(page, window.url + "/settings")
    section = page.get_by_role("region", name="Marketplaces")
    expect(section).to_contain_text("Marketplaces show here once a campaign names one.")
    expect(section.get_by_role("group")).to_have_count(0)


# The triangle (SPEC 14.11): what a campaign waits on today, on the sheet, its page and the rail


def triangle(page: Page, row_name: str):
    return sheet_row(page, row_name).get_by_role("button", name=re.compile(r"^Needs you: "))


def test_triangle_lists_what_waits(page, engine, window):
    beast = add_campaign(engine, rate_per_1000=2.0, submit_url=SUBMIT)
    add_campaign(engine, name="Quiet clips", rate_per_1000=1.0)
    campaign_draft(engine, beast)
    live = posted(engine, linked_clip(engine, beast), add_account(engine, "tiktok", "t1"))
    post_store.update_post(engine.db(), live.id, url=LIVE)
    opened(page, window.url + "/campaigns")
    expect(page.get_by_role("row").nth(1)).to_contain_text("Beast clips")  # a row with a triangle goes first
    expect(triangle(page, "Quiet clips")).to_have_count(0)
    mark = triangle(page, "Beast clips")
    expect(mark).to_have_accessible_name("Needs you: 2 things")
    mark.hover()
    shown = page.get_by_role("dialog", name="Beast clips needs you")
    lines = shown.get_by_role("listitem")  # in today's list's order, which goes by what each is worth
    expect(lines).to_have_count(2)
    review = lines.filter(has_text="Approve 1 draft").get_by_role("link", name="Review drafts")
    expect(review).to_have_attribute("href", "/#review")
    lines.filter(has_text="Submit 1 live post").get_by_role("link", name="Submit").click()
    expect(page).to_have_url(window.url + "/#submit")
    submit = page.get_by_role("region", name="Submit", exact=True)
    expect(submit).to_be_in_viewport()
    submit.get_by_role("article", name=live.clip_title).get_by_role("button", name="Mark submitted").click()
    expect(submit).to_have_count(0)

    page.get_by_role("navigation", name="Pages").get_by_role("link", name=re.compile("^Campaigns")).click()
    expect(triangle(page, "Beast clips")).to_have_accessible_name("Needs you: 1 thing")
    triangle(page, "Beast clips").focus()  # focusing it opens it too
    shown = page.get_by_role("dialog", name="Beast clips needs you")
    expect(shown.get_by_role("listitem")).to_have_count(1)
    expect(shown).not_to_contain_text("Submit 1 live post")
    page.keyboard.press("Escape")
    expect(shown).to_have_count(0)
    sheet_row(page, "Beast clips").get_by_role("link", name="Beast clips", exact=True).click()
    waits = page.get_by_role("region", name="Needs you")
    expect(waits.get_by_role("listitem")).to_have_count(1)
    expect(waits).to_contain_text("Approve 1 draft")
    expect(waits).not_to_contain_text("Submit 1 live post")


def test_rail_counts_campaigns_that_need_you(page, engine, window):
    for name in ("Beast clips", "Drama"):
        campaign = add_campaign(engine, name=name, submit_url=SUBMIT)
        live = posted(engine, linked_clip(engine, campaign), add_account(engine, "tiktok", f"t{len(name)}"))
        post_store.update_post(engine.db(), live.id, url=f"{LIVE}{len(name)}")
    add_campaign(engine, name="Quiet clips")
    opened(page, window.url + "/accounts")
    link = page.get_by_role("navigation", name="Pages").get_by_role("link", name="Campaigns, 2 need you")
    expect(link).to_contain_text("2")


def test_no_triangle_when_nothing_waits(page, engine, window):
    quiet = add_campaign(engine, name="Quiet clips", rate_per_1000=1.0)
    opened(page, window.url + "/campaigns")
    expect(sheet_row(page, "Quiet clips")).to_have_count(1)
    expect(page.get_by_role("button", name=re.compile(r"^Needs you: "))).to_have_count(0)
    # No count beside the name (the link also holds the narrow rail's hover tip, the name again)
    expect(page.get_by_role("link", name="Campaigns", exact=True)).not_to_contain_text(re.compile(r"\d"))
    opened(page, window.url + f"/campaigns/{quiet.id}")
    expect(page.get_by_role("heading", name="Quiet clips", level=1)).to_be_visible()
    expect(page.get_by_role("region", name="Needs you")).to_have_count(0)
