"""HeyClicky's Campaign Scout file (SPEC 10.11): each column maps to a brief, every cell is untrusted text,
a missing, unreadable or reshaped file stops the source, and the merge keeps one brief per link."""

import csv
import os
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from clipforge.briefs import merge, scout, store
from tests.fakes import fake_engine

FIXTURE = Path(__file__).parent.parent / "fixtures" / "campaign_scout.csv"
MTIME = 1_761_500_000.0


@pytest.fixture
def scout_csv(tmp_path):
    path = tmp_path / "scout" / "campaigns.csv"
    path.parent.mkdir()
    shutil.copy(FIXTURE, path)
    os.utime(path, (MTIME, MTIME))
    return path


@pytest.fixture
def engine():
    eng = fake_engine()
    yield eng
    eng.close()


def write(path: Path, rows: list[dict], columns=scout.COLUMNS) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        out = csv.writer(f)
        out.writerow(columns)
        for row in rows:
            out.writerow([row.get(c, "") for c in columns])
    return path


def row(**cells) -> dict:
    base = {
        "platform": "Whop Content Rewards",
        "campaign_name": "A campaign",
        "campaign_url": "https://contentrewards.com/c/a",
        "campaign_status": "Accepting clips",
        "rate_usd_per_1k": "$1.00 CPM",
        "allowed_platforms": "TikTok, Instagram",
    }
    return base | cells


def by_title(read: scout.ScoutRead) -> dict:
    return {b.title: b for b in read.found}


def test_scout_maps_each_column(scout_csv):
    read = scout.read_scout(scout_csv)
    assert read.problem is None
    assert read.skipped == 3  # YouTube only, the duplicate link, the empty name
    found = by_title(read)
    assert list(found) == [
        "Northwind Podcast clips",
        "Pixel Arena highlights",
        "Summit Fitness",
        "Daily Laughs",
        "Old Beats",
        "Open brief",
        "Alpha Streams",
        "Nearly spent",
        "Closed campaign",
    ]

    north = found["Northwind Podcast clips"]
    assert (north.external_id, north.url) == ("https://contentrewards.com/c/northwind",) * 2
    assert (north.marketplace, north.brand, north.currency) == ("Whop", "Northwind", "USD")
    assert (north.rate_per_1000, north.rates) == (1.0, {})
    assert (north.budget_total, north.budget_spent, north.budget_left) == (1000.0, 187.5, 812.5)
    assert north.caps == {"tiktok": 150.0, "instagram": 100.0} and north.max_payout == 150.0
    assert north.platforms == ["tiktok", "instagram"]
    assert north.ends_on is None and north.min_views is None
    assert north.budget_read_at == datetime(2026, 10, 27).timestamp()
    assert north.footage.kind == "The brand's own podcast episodes" and north.footage.links == []
    assert north.text == "Clip the best moments of the Northwind podcast.\nTag the show."
    assert north.notes == {
        "verdict": "Maybe",
        "judgment": "Fits a podcast page.",
        "automation_or_bot_language": "No bot language shown.",
    }
    assert not north.gone
    assert {"title", "url", "rate_per_1000", "max_payout", "caps", "budget_left", "platforms", "text"} <= set(
        north.stated
    )
    assert not {"min_views", "rates", "ends_on"} & set(north.stated)

    pixel = found["Pixel Arena highlights"]
    assert (pixel.rate_per_1000, pixel.rates) == (1.5, {"tiktok": 0.5, "instagram": 1.5})
    assert pixel.platforms == ["tiktok", "instagram"]
    assert (pixel.budget_total, pixel.budget_spent, pixel.budget_left) == (2000.0, 500.0, 1500.0)
    assert pixel.ends_on == "2026-11-30"
    assert pixel.caps == {"tiktok": 200.0, "instagram": 250.0} and pixel.max_payout == 250.0
    assert pixel.budget_read_at == MTIME  # no checked date: when the file was written
    assert {"rates", "ends_on"} <= set(pixel.stated)

    summit = found["Summit Fitness"]
    assert (summit.marketplace, summit.rate_per_1000, summit.platforms) == ("Vyro", 0.9, ["tiktok"])
    assert (summit.budget_total, summit.budget_spent, summit.budget_left) == (None, None, 12000.0)
    assert (summit.caps, summit.max_payout) == ({"tiktok": 75.0}, 75.0)

    laughs = found["Daily Laughs"]
    assert (laughs.rate_per_1000, laughs.rates, laughs.platforms) == (None, {}, ["instagram"])
    assert laughs.budget_left == 250.0
    assert (laughs.max_payout, laughs.caps) == (None, {})
    assert not {"rate_per_1000", "max_payout"} & set(laughs.stated)

    assert found["Old Beats"].gone and found["Closed campaign"].gone
    assert not found["Nearly spent"].gone  # "$990.00/$1,000 spent" is how much went, not a closed campaign

    open_brief = found["Open brief"]
    assert (open_brief.rate_per_1000, open_brief.platforms) == (2.0, None)
    assert "platforms" not in open_brief.stated
    assert found["Alpha Streams"].marketplace == "Clipping Alpha"


@pytest.mark.parametrize(
    ("text", "rate", "per"),
    [
        ("$1.00 CPM across listed platforms", 1.0, {}),
        ("TikTok $0.50 CPM; Instagram $1.50 CPM; YouTube $0.50 CPM", 1.5, {"tiktok": 0.5, "instagram": 1.5}),
        ("YouTube $2.00 CPM; X $1.00 CPM", None, {}),
        ("$9.00 per 10K views", 0.9, {}),
        ("$1 per 1,000 views", 1.0, {}),
        ("$500 per 1M views", 0.5, {}),
        ("Per-post payout; view rate not shown", None, {}),
        ("$5.00 per clip", None, {}),
        ("", None, {}),
        ("$" + "9" * 400 + " CPM", None, {}),
    ],
)
def test_rates(text, rate, per):
    assert scout.rates(text) == (rate, per)


@pytest.mark.parametrize(
    ("text", "amount"),
    [
        ("$1,335.66", 1335.66),
        ("$12,000 (public page display)", 12000.0),
        ("$250.00 nominal (API); public page showed $200", 250.0),
        ("Not publicly shown", None),
        ("", None),
    ],
)
def test_money(text, amount):
    assert scout.money(text) == amount


@pytest.mark.parametrize(
    ("text", "platforms"),
    [
        ("Instagram, TikTok, YouTube", ["tiktok", "instagram"]),
        ("TikTok, YouTube, Instagram; languages: en", ["tiktok", "instagram"]),
        ("Instagram", ["instagram"]),
        ("YouTube, X", []),
        ("", None),
        ("Any platform", None),
    ],
)
def test_platforms(text, platforms):
    assert scout.platforms(text) == platforms


@pytest.mark.parametrize(
    ("text", "marketplace"),
    [
        ("Whop Content Rewards", "Whop"),
        ("Vyro", "Vyro"),
        ("Clipping Alpha", "Clipping Alpha"),
        ("", "Unknown"),
    ],
)
def test_marketplace_of(text, marketplace):
    assert scout.marketplace_of(text) == marketplace


def test_scout_cells_are_untrusted(tmp_path):
    path = write(
        tmp_path / "scout.csv",
        [
            row(campaign_name='=HYPERLINK("x")', campaign_url="https://contentrewards.com/c/1"),
            row(
                campaign_name="Turned\u202e around",
                brand="Ring\u0007bell",
                judgment="Line\nbreak",
                campaign_url="https://contentrewards.com/c/2",
                public_brief_requirements="x" * 10_000,
            ),
            row(
                campaign_name="Long",
                campaign_url="https://contentrewards.com/c/3",
                public_brief_requirements="y" * 25_000,
            ),
            row(campaign_name="Script link", campaign_url="javascript:void(0)"),
            row(campaign_name="Hidden user", campaign_url="https://contentrewards.com@evil.example/c"),
            row(campaign_name="Spaced link", campaign_url="https://contentrewards.com/c/a b"),
            row(campaign_name="Plain http", campaign_url="http://contentrewards.com/c/plain"),
            row(campaign_name="Twice", campaign_url="https://contentrewards.com/c/1"),
            row(campaign_name="Lots of text " * 40, campaign_url="https://contentrewards.com/c/4"),
        ],
    )
    read = scout.read_scout(path)
    assert read.problem is None and read.skipped == 4
    found = by_title(read)
    assert '=HYPERLINK("x")' in found  # kept as plain text, never a formula
    turned = found["Turned around"]
    assert (turned.brand, turned.notes["judgment"]) == ("Ring bell", "Line break")
    assert turned.text == "x" * 10_000
    assert found["Long"].text == "y" * 20_000
    assert found["Plain http"].url == "http://contentrewards.com/c/plain"
    assert [b.url for b in read.found].count("https://contentrewards.com/c/1") == 1
    long_title = next(t for t in found if t.startswith("Lots of text"))
    assert len(long_title) == 200


def test_scout_problems_stop_the_source(tmp_path, monkeypatch):
    assert scout.read_scout(tmp_path / "nothing.csv") == scout.ScoutRead([], "missing", 0)

    bad = tmp_path / "bad.csv"
    bad.write_bytes(b"checked_date,platform\n\xff\xfe\x00 not text")
    assert scout.read_scout(bad).problem == "unreadable"

    assert scout.read_scout(tmp_path).problem == "unreadable"  # a folder, not a file

    empty = tmp_path / "empty.csv"
    empty.write_bytes(b"")
    assert scout.read_scout(empty).problem == "unreadable"

    big = write(tmp_path / "big.csv", [row()])
    with open(big, "a") as f:
        f.write(" " * scout.MAX_BYTES)
    assert scout.read_scout(big).problem == "unreadable"

    renamed = [("link" if c == "campaign_url" else c) for c in scout.COLUMNS]
    reshaped = write(tmp_path / "reshaped.csv", [row()], columns=renamed)
    assert scout.read_scout(reshaped) == scout.ScoutRead([], "reshaped", 0)

    half = write(tmp_path / "half.csv", [row()])
    with open(half, "a", encoding="utf-8") as f:
        f.write('2026-10-27,Whop Content Rewards,Acme,"Half writ')
    assert scout.read_scout(half) == scout.ScoutRead([], "unreadable", 0)

    short = write(tmp_path / "short.csv", [row()])
    with open(short, "a", encoding="utf-8") as f:
        f.write("2026-10-27,Whop Content Rewards,Acme\r\n")
    assert scout.read_scout(short).problem == "unreadable"

    whole = write(tmp_path / "whole.csv", [row()])
    assert len(scout.read_scout(whole).found) == 1
    stamps = iter([(1, 1), (2, 2)])
    monkeypatch.setattr(scout, "_stamp", lambda fd: next(stamps))
    assert scout.read_scout(whole) == scout.ScoutRead([], "unreadable", 0)  # written to while it was read


def test_scout_reads_at_most_five_thousand_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(scout, "MAX_ROWS", 3)
    path = write(
        tmp_path / "many.csv", [row(campaign_url=f"https://contentrewards.com/c/{n}") for n in range(5)]
    )
    read = scout.read_scout(path)
    assert (len(read.found), read.skipped) == (3, 2)


def test_merge_new_changed_gone(engine, scout_csv):
    conn = engine.db()
    source = store.get_source(conn, "bs_scout")
    first = scout.read_scout(scout_csv).found
    made = merge.merge(engine, source, first, 100.0, complete=True)
    # Old Beats and Closed campaign were gone when first seen, so they are never stored
    assert (len(made.new), made.changed, made.gone) == (7, [], [])
    titles = {b.title: b for b in store.briefs(conn)}
    assert "Old Beats" not in titles
    north, pixel, laughs, alpha = (
        titles[t]
        for t in ("Northwind Podcast clips", "Pixel Arena highlights", "Daily Laughs", "Alpha Streams")
    )
    assert (north.status, north.first_seen_at, north.last_seen_at, north.source_id) == (
        "new",
        100.0,
        100.0,
        "bs_scout",
    )
    assert north.fields["rate_per_1000"] == "stated" and "min_views" not in north.fields
    assert north.text_hash == merge.text_hash(north.text) != ""
    assert titles["Open brief"].platforms == ["tiktok", "instagram"]
    assert "platforms" not in titles["Open brief"].fields

    claude = {"niche": "claude", "min_views": "claude", "language": "guess"}
    for b in (north, pixel):
        store.update_brief(
            conn, b.id, niche="Podcast", language="en", min_views=10_000, fields=b.fields | claude
        )
    store.update_brief(conn, laughs.id, status="hidden")
    store.update_brief(conn, pixel.id, status="seen")

    second = [
        b.model_copy(update={"text": "New rules for the clips."})
        if b.title == "Northwind Podcast clips"
        else b
        for b in first
        if b.title not in ("Alpha Streams", "Daily Laughs")
    ]
    made = merge.merge(engine, source, second, 200.0, complete=True)
    assert made.new == [] and made.changed == [north.id] and made.gone == [alpha.id]

    north = store.get_brief(conn, north.id)
    assert (north.text, north.niche, north.language, north.min_views) == (
        "New rules for the clips.",
        None,
        None,
        None,
    )
    assert not {"niche", "min_views", "language"} & set(north.fields)
    assert north.fields["rate_per_1000"] == "stated"
    assert (north.status, north.first_seen_at, north.last_seen_at) == ("new", 100.0, 200.0)

    pixel = store.get_brief(conn, pixel.id)
    assert (pixel.status, pixel.niche, pixel.min_views, pixel.fields["niche"]) == (
        "seen",
        "Podcast",
        10_000,
        "claude",
    )
    assert store.get_brief(conn, laughs.id).status == "hidden"  # not in the file, but you hid it
    assert store.get_brief(conn, alpha.id).status == "gone"

    third = [b.model_copy(update={"gone": True}) if b.title == "Pixel Arena highlights" else b for b in first]
    made = merge.merge(engine, source, third, 300.0, complete=False)
    assert made.new == [alpha.id] and made.gone == [pixel.id]
    assert store.get_brief(conn, alpha.id).status == "new"  # live again
    assert store.get_brief(conn, laughs.id).status == "hidden"

    made = merge.merge(engine, source, [], 400.0, complete=False)
    assert (made.new, made.changed, made.gone) == ([], [], [])
    assert store.get_brief(conn, north.id).status == "new"  # a partial read leaves the rest alone


def test_tests_never_read_the_real_file(tmp_path):
    assert scout.scout_file().is_relative_to(tmp_path)
    real = (
        Path.home() / "Library/Application Support/Clicky/projects/agents/campaign-scout/output/campaigns.csv"
    )
    assert real == scout.SCOUT_FILE
