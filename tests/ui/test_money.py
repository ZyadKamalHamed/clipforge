"""The Money page in the window (SPEC 14.12, phase 5e): the rail's link after Campaigns, the empty page that
says what fills it, the totals a line a currency for the period picked, the timeline whose bars read their
figures on hover, focus and to a screen reader, and the accounts table with its totals row. Seeded with
campaign posts on a fixed clock; nothing here posts, logs in or reaches past 127.0.0.1."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from clipforge.post import store as post_store
from tests.fakes import Clock, fake_engine
from tests.posting import add_account, add_campaign, at, campaign_post
from tests.ui.conftest import live_server
from tests.ui.test_shell import opened

NOW = at(7, 10)  # 7:10 am on Tue 3 Nov 2026 in Sydney
EMPTY_TITLE = "No campaign posts yet"
EMPTY_TEXT = (
    "Money fills in once a post on a campaign goes up: its views, what it should earn and what you're paid."
)
AS_IF = "No exchange rate for EUR, so it counts as if in US$. Add one in Settings."
BAR = re.compile(r"^\d{1,2} [A-Z][a-z]{2}: ")
CSV_HEAD = (
    "Posted,Account,Platform,Campaign,Marketplace,Hook,Views at 2 days,Views at 7 days,Views,Status,Reason,"
    "Currency,Estimated,Paid"
)


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


def body_rows(page: Page, table: str) -> list[list[str]]:
    rows = page.get_by_role("table", name=table).locator("tbody tr")
    return [row.locator("th, td").all_inner_texts() for row in rows.all()]


def foot_row(page: Page, table: str) -> list[str]:
    return page.get_by_role("table", name=table).locator("tfoot tr th, tfoot tr td").all_inner_texts()


def test_rail_has_money_after_campaigns(page, window):
    opened(page, window.url)
    rail = page.get_by_role("navigation", name="Pages")
    names = rail.get_by_role("link").all_inner_texts()
    assert names[names.index("Campaigns") + 1] == "Money"
    rail.get_by_role("link", name="Money", exact=True).click()
    expect(page).to_have_url(re.compile(r"/money$"))
    expect(page.get_by_role("heading", name="Money", level=1)).to_be_visible()


def test_money_empty_says_what_fills_it(page, window):
    opened(page, window.url + "/money")
    expect(page.get_by_role("heading", name=EMPTY_TITLE)).to_be_visible()
    expect(page.get_by_text(EMPTY_TEXT)).to_be_visible()
    expect(page.get_by_role("table")).to_have_count(0)
    page.get_by_role("link", name="Find a campaign on the Briefs tab").click()
    expect(page).to_have_url(re.compile(r"/campaigns\?tab=briefs$"))
    expect(page.get_by_role("tab", name="Briefs")).to_have_attribute("aria-selected", "true")


def test_money_totals_follow_the_period(page, engine, window):
    beast = add_campaign(engine, rate_per_1000=2.0)
    drama = add_campaign(engine, name="Drama nights", marketplace="Vyro", currency="AUD", rate_per_1000=1.0)
    t1 = add_account(engine, "tiktok", "t1")
    campaign_post(engine, beast, t1, days_ago=3, counts=((1, 5000),), title="Recent")
    campaign_post(
        engine, drama, t1, days_ago=20, counts=((1, 3000),), status="paid", paid=12.0, title="Older"
    )
    opened(page, window.url + "/money")
    expect(page.get_by_role("radio", name="30 days")).to_be_checked()
    expect(page.get_by_role("table", name="Totals")).to_be_visible()
    assert [line[0] for line in body_rows(page, "Totals")] == ["AUD", "USD"]
    assert body_rows(page, "Totals") == [
        ["AUD", "A$12.00", "None", "None", "3,000", "1"],
        ["USD", "US$0.00", "None", "US$10.00", "5,000", "1"],
    ]

    page.get_by_role("radio", name="7 days").check()
    expect(page.get_by_role("table", name="Totals").locator("tbody tr")).to_have_count(1)
    assert body_rows(page, "Totals") == [["USD", "US$0.00", "None", "US$10.00", "5,000", "1"]]

    page.get_by_role("radio", name="All time").check()
    expect(page.get_by_role("table", name="Totals").locator("tbody tr")).to_have_count(2)


def test_timeline_bars_show_their_figures(page, engine, window):
    whop = add_campaign(engine, rate_per_1000=2.0)
    euro = add_campaign(engine, name="Euro clips", marketplace="Vyro", currency="EUR", rate_per_1000=1.0)
    t1 = add_account(engine, "tiktok", "t1")
    i1 = add_account(engine, "instagram", "i1")
    campaign_post(engine, whop, t1, days_ago=1, counts=((0.5, 6200),), title="Fresh")
    campaign_post(engine, euro, i1, days_ago=1, counts=((0.5, 5000),), title="Euro")
    opened(page, window.url + "/money")
    expect(page.get_by_role("heading", name="Earnings by day")).to_be_visible()
    expect(page.get_by_text("In US$ through your exchange rates.")).to_be_visible()
    expect(page.get_by_text(AS_IF)).to_be_visible()
    expect(page.get_by_role("button", name=BAR)).to_have_count(30)

    bar = page.get_by_role("button", name=re.compile(r"^2 Nov: "))
    expect(bar).to_have_accessible_name(
        "2 Nov: US$17.40 estimated; Whop US$12.40 estimated, Vyro US$5.00 estimated"
    )
    expect(page.get_by_role("button", name="1 Nov: nothing earned")).to_have_count(1)
    bar.focus()
    tip = page.get_by_role("tooltip")
    expect(tip).to_contain_text("US$17.40 estimated")
    expect(tip).to_contain_text("Whop US$12.40 estimated")
    expect(tip).to_contain_text("Vyro US$5.00 estimated")

    legend = page.get_by_role("list", name="Series")
    expect(legend.get_by_role("listitem")).to_have_text(["Whop", "Vyro"])
    expect(page.get_by_text("Solid is paid, faded is estimated.")).to_be_visible()  # true in both themes
    page.get_by_role("radio", name="Account", exact=True).check()
    expect(legend.get_by_role("listitem")).to_have_text(["@t1", "@i1"])
    expect(page.get_by_role("button", name=re.compile(r"^2 Nov: "))).to_have_accessible_name(
        "2 Nov: US$17.40 estimated; @t1 US$12.40 estimated, @i1 US$5.00 estimated"
    )


def test_nothing_earned_says_so(page, engine, window):
    beast = add_campaign(engine, rate_per_1000=2.0)
    campaign_post(engine, beast, add_account(engine, "tiktok", "t1"), days_ago=2, status="rejected")
    opened(page, window.url + "/money")
    expect(page.get_by_text("Nothing earned in this period yet.")).to_be_visible()
    expect(page.get_by_role("button", name=BAR)).to_have_count(30)


def test_accounts_table_and_totals(page, engine, window):
    beast = add_campaign(engine, rate_per_1000=2.0)
    t1 = add_account(engine, "tiktok", "t1", niche_tags=["gaming"])
    add_account(engine, "instagram", "i1")
    campaign_post(engine, beast, t1, days_ago=2, counts=((1, 4000),), title="One")
    campaign_post(
        engine, beast, t1, days_ago=4, counts=((1, 1000), (3, 2000)), status="paid", paid=3.0, title="Two"
    )
    opened(page, window.url + "/money")
    expect(page.get_by_role("table", name="Accounts")).to_be_visible()
    head = page.get_by_role("table", name="Accounts").locator("thead th").all_inner_texts()
    assert head == [
        "Platform",
        "Account",
        "Niche",
        "Posts",
        "Views",
        "Median 7-day views",
        "Estimated",
        "Pending",
        "Paid",
        "Best post",
    ]
    assert body_rows(page, "Accounts") == [
        ["TikTok", "@t1", "gaming", "2", "6,000", "None yet", "US$8.00", "None", "US$3.00", "One"],
        ["Instagram", "@i1", "No niche", "0", "None yet", "None yet", "None", "None", "None", "None"],
    ]
    assert foot_row(page, "Accounts") == ["Total", "2", "6,000", "", "US$8.00", "None", "US$3.00", ""]


def seed_posts(engine):
    """Four posts on one campaign: three on @t1 (one turned down) and one on @i1, 2 to 5 days old."""
    beast = add_campaign(engine, rate_per_1000=2.0)
    t1 = add_account(engine, "tiktok", "t1", niche_tags=["gaming"])
    i1 = add_account(engine, "instagram", "i1", niche_tags=["drama"])
    big = campaign_post(engine, beast, t1, days_ago=2, counts=((1, 9000),), title="Big hook")
    post_store.update_post(engine.db(), big.id, url="https://www.tiktok.com/@t1/video/1")
    campaign_post(engine, beast, t1, days_ago=3, counts=((1, 1000),), title="Small hook")
    campaign_post(engine, beast, i1, days_ago=4, counts=((1, 4000),), title="Insta hook")
    campaign_post(
        engine,
        beast,
        t1,
        days_ago=5,
        counts=((1, 500),),
        status="rejected",
        reason="Not original enough",
        title="Turned down",
    )


def test_posts_filter_sort_and_best(page, engine, window):
    seed_posts(engine)
    opened(page, window.url + "/money")
    expect(page.get_by_role("heading", name="Posts", level=2)).to_be_visible()
    sheet = page.get_by_role("table", name="Posts")
    hooks = sheet.locator("tbody tr > :nth-child(5)")
    expect(hooks).to_have_text(["Big hook", "Small hook", "Insta hook", "Turned down"])
    expect(sheet.get_by_role("link", name="Big hook")).to_have_attribute(
        "href", "https://www.tiktok.com/@t1/video/1"
    )

    most = page.get_by_role("list", name="Most views").get_by_role("listitem")
    expect(most).to_have_count(4)
    expect(most.first).to_contain_text("Big hook")
    expect(most.first).to_contain_text("@t1")
    expect(most.first).to_contain_text("9,000")
    earned = page.get_by_role("list", name="Earned the most").get_by_role("listitem")
    expect(earned).to_have_count(3)  # the one turned down earned nothing
    expect(earned.first).to_contain_text("US$18.00")
    assert body_rows(page, "By platform") == [
        ["TikTok", "3", "10,500", "US$20.00", "US$0.00"],
        ["Instagram", "1", "4,000", "US$8.00", "US$0.00"],
    ]
    assert body_rows(page, "By niche") == [
        ["gaming", "3", "10,500", "US$20.00", "US$0.00"],
        ["drama", "1", "4,000", "US$8.00", "US$0.00"],
    ]

    views = sheet.get_by_role("columnheader", name="Views", exact=True)
    views.get_by_role("button").click()
    expect(views).to_have_attribute("aria-sort", "descending")
    expect(hooks).to_have_text(["Big hook", "Insta hook", "Small hook", "Turned down"])
    views.get_by_role("button").click()
    expect(views).to_have_attribute("aria-sort", "ascending")
    expect(hooks).to_have_text(["Turned down", "Small hook", "Insta hook", "Big hook"])

    page.get_by_label("Platform", exact=True).select_option(label="Instagram")
    expect(hooks).to_have_text(["Insta hook"])
    expect(most).to_have_count(1)  # Best follows the filters
    page.get_by_label("Platform", exact=True).select_option(label="All platforms")
    page.get_by_label("Status", exact=True).select_option(label="Rejected")
    expect(hooks).to_have_text(["Turned down"])
    expect(sheet.locator("tbody tr")).to_contain_text(["Not original enough"])

    page.get_by_label("Search posts").fill("nothing like this")
    expect(page.get_by_text("No posts match these filters.")).to_be_visible()
    page.get_by_role("button", name="Clear filters").click()
    expect(hooks).to_have_count(4)
    page.get_by_label("From", exact=True).fill("2026-10-31")
    expect(hooks).to_have_text(["Small hook", "Big hook"])


def test_export_csv_saves_the_shown_rows(page, engine, window):
    beast = add_campaign(engine, rate_per_1000=2.0)
    t1 = add_account(engine, "tiktok", "t1")
    i1 = add_account(engine, "instagram", "i1")
    campaign_post(engine, beast, t1, days_ago=1, counts=((0.5, 2000),), title='=HYPERLINK("x")')
    campaign_post(engine, beast, i1, days_ago=2, counts=((1, 3000),), title="Kept out")
    opened(page, window.url + "/money")
    page.get_by_label("Platform", exact=True).select_option(label="TikTok")
    expect(page.get_by_role("table", name="Posts").locator("tbody tr")).to_have_count(1)
    with page.expect_download() as saved:
        page.get_by_role("button", name="Export CSV").click()
    download = saved.value
    assert download.suggested_filename == "clipforge-money-2026-11-03.csv"
    lines = Path(download.path()).read_bytes().decode("utf-8").split("\r\n")
    assert lines[0] == chr(0xFEFF) + CSV_HEAD
    assert lines[1:] == [
        """2026-11-02,'@t1,TikTok,Beast clips,Whop,"'=HYPERLINK(""x"")",,,2000,Submitted,,USD,4.00,""",
        "",
    ]


# How the bar row sits in its scrolling box: the space after the newest bar, how far the box can scroll
# sideways and how far it has.
BAR_ROW = """(bar) => {
    let box = bar.parentElement;
    while (getComputedStyle(box).overflowX !== "auto") box = box.parentElement;
    return {
        gap: box.getBoundingClientRect().right - bar.getBoundingClientRect().right,
        room: box.scrollWidth - box.clientWidth,
        scrolled: box.scrollLeft,
    };
}"""


def test_timeline_fills_its_panel_and_opens_at_today(page, engine, window):
    beast = add_campaign(engine, rate_per_1000=2.0)
    campaign_post(engine, beast, add_account(engine, "tiktok", "t1"), days_ago=1, counts=((0.5, 4000),))
    page.set_viewport_size({"width": 1440, "height": 900})
    opened(page, window.url + "/money")
    newest = page.get_by_role("button", name=re.compile(r"^3 Nov: "))
    row = newest.evaluate(BAR_ROW)
    assert row["room"] == 0  # 30 days fit a wide window
    assert row["gap"] < 2, f"the bars stop {row['gap']} px short of the panel's end"

    page.set_viewport_size({"width": 820, "height": 1000})
    opened(page, window.url + "/money")
    row = newest.evaluate(BAR_ROW)
    assert row["room"] > 0  # too many to fit, so the row scrolls
    assert row["scrolled"] >= row["room"] - 1  # from the newest end
    expect(newest).to_be_in_viewport()
    page.get_by_role("button", name=re.compile(r"^2 Nov: ")).focus()
    expect(page.get_by_role("tooltip")).to_contain_text("2 Nov")


def test_accounts_table_fits_a_wide_window(page, engine, window):
    beast = add_campaign(engine, rate_per_1000=2.0)
    drama = add_campaign(engine, name="Drama nights", marketplace="Vyro", currency="AUD", rate_per_1000=1.5)
    euro = add_campaign(engine, name="Euro clips", marketplace="Clipify", currency="EUR", rate_per_1000=1.0)
    t1 = add_account(engine, "tiktok", "levelupclips", niche_tags=["gaming"])
    for campaign, days_ago in ((beast, 1), (drama, 2), (euro, 3)):
        campaign_post(
            engine, campaign, t1, days_ago=days_ago, counts=((1, 9500),), title="A long enough hook"
        )
        campaign_post(
            engine, campaign, t1, days_ago=days_ago + 5, counts=((1, 8000),), status="approved", title="Two"
        )
        campaign_post(
            engine, campaign, t1, days_ago=days_ago + 9, counts=((1, 7000),), status="paid", paid=21.3
        )
    page.set_viewport_size({"width": 1440, "height": 900})
    opened(page, window.url + "/money")
    table = page.get_by_role("table", name="Accounts")
    expect(table.locator("tfoot")).to_contain_text("A$")
    assert table.evaluate("(t) => t.parentElement.scrollWidth - t.parentElement.clientWidth") <= 0
    assert foot_row(page, "Accounts")[5] == "A$12.00 + 8.00 EUR + US$16.00"  # Pending, still one line


def test_posts_tables_fit_a_narrow_window(page, engine, window):
    seed_posts(engine)
    page.set_viewport_size({"width": 820, "height": 1000})
    opened(page, window.url + "/money")
    for name in ("By platform", "By niche"):
        table = page.get_by_role("table", name=name)
        expect(table).to_be_visible()
        room = table.evaluate("(t) => t.parentElement.scrollWidth - t.parentElement.clientWidth")
        assert room <= 0, f"{name} scrolls sideways by {room} px"


CAMPAIGN_AR = "مقاطع النجوم!"
MARKET_AR = "سوق المقاطع"
NICHE_AR = "ألعاب"
HOOK_AR = "أفضل لحظة في الحلقة!"
HANDLE_AR = "نجم"
# Each text node in the page that holds Arabic, unless the element right round it reads it as dir="auto".
UNDIRECTED = """() => {
  const walk = document.createTreeWalker(document.querySelector("main"), NodeFilter.SHOW_TEXT);
  const loose = [];
  for (let node = walk.nextNode(); node; node = walk.nextNode()) {
    if (!/[\\u0600-\\u06FF]/.test(node.data)) continue;
    const holder = node.parentElement;
    if (holder.getAttribute("dir") !== "auto") loose.push(`${holder.tagName}: ${node.data}`);
  }
  return loose;
}"""


def test_money_text_keeps_its_own_direction(page, engine, window):
    """Campaign, marketplace and account names, niches and hooks come from outside and can be Arabic: every
    element on Money that holds one reads it in its own direction, as the rest of the window does."""
    clips = add_campaign(engine, name=CAMPAIGN_AR, marketplace=MARKET_AR, rate_per_1000=2.0)
    star = add_account(engine, "instagram", HANDLE_AR, niche_tags=[NICHE_AR])
    live = campaign_post(engine, clips, star, days_ago=2, counts=((1, 9000),), title=HOOK_AR)
    post_store.update_post(engine.db(), live.id, url="https://www.instagram.com/reel/1")
    campaign_post(engine, clips, star, days_ago=3, counts=((1, 1000),), title=f"{HOOK_AR} 2")
    opened(page, window.url + "/money")
    expect(page.get_by_role("table", name="Posts")).to_be_visible()
    expect(page.get_by_role("table", name="Accounts").get_by_text(NICHE_AR)).to_be_visible()
    assert page.evaluate(UNDIRECTED) == []
