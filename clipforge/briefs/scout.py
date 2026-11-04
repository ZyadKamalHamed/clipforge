"""HeyClicky's Campaign Scout file (SPEC 10.11): the campaigns.csv its agent keeps on this Mac, read only.
ClipForge reads this one file and nothing else of HeyClicky's, never writes there and makes no request for
it. Every cell is untrusted text another program read from public pages, so each one is cleaned, cut to
size and kept as data. Research 08 profiles the file's columns and formats."""

from __future__ import annotations

import csv
import io
import math
import os
import re
import stat
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import ValidationError

from clipforge.briefs.models import TEXT_MAX, BriefIn, Footage
from clipforge.campaigns.models import Platform
from clipforge.untrusted import clean_lines, clean_text

SCOUT_FILE = (
    Path.home() / "Library/Application Support/Clicky/projects/agents/campaign-scout/output/campaigns.csv"
)
COLUMNS = (
    "checked_date",
    "platform",
    "brand",
    "campaign_name",
    "campaign_url",
    "campaign_status",
    "rate_usd_per_1k",
    "budget_total_usd",
    "budget_spent_usd",
    "budget_remaining_usd",
    "end_date",
    "allowed_platforms",
    "source_footage_type",
    "public_brief_requirements",
    "automation_or_bot_language",
    "fee_model",
    "verdict",
    "judgment",
    "estimate_10k_views_usd",
    "estimate_100k_views_usd",
    "estimate_basis",
    "max_per_clip_usd",
    "baseline_note",
)
NOTES = ("verdict", "judgment", "automation_or_bot_language")  # shown as Campaign Scout's notes
MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 5_000
NOTE_MAX = 2_000
LINK_MAX = 2_000
Problem = Literal["missing", "unreadable", "reshaped"]

_AMOUNT = r"([0-9][0-9,]*(?:\.[0-9]+)?)"
_MONEY = re.compile(r"\$\s*" + _AMOUNT)
_CPM = re.compile(r"\$\s*" + _AMOUNT + r"\s*CPM\b", re.I)
_PER_VIEWS = re.compile(r"\$\s*" + _AMOUNT + r"\s*per\s*" + _AMOUNT + r"\s*([km])?\s*views\b", re.I)
_SITES = r"(tiktok|instagram|youtube|x|twitter|facebook|snapchat|threads)"
_NAMED = re.compile(r"^\s*" + _SITES + r"\b", re.I)
_SITE_WORDS = re.compile(r"\b" + _SITES + r"\b", re.I)
_OURS: tuple[Platform, ...] = ("tiktok", "instagram")
# "$990.00/$1,000 spent" says how much went; "budget spent" or "exhausted" says the campaign stopped
_GONE = re.compile(r"\b(?:paused|exhausted|closed|ended)\b|\b(?:budget|fully|all)\s+spent\b", re.I)
_ISO_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class ScoutRead:
    """What one read of the file found. ``problem`` set means the source stops for the day with nothing
    found; ``skipped`` counts the rows left out (no usable name or link, a link seen above, only other
    platforms, or past ``MAX_ROWS``)."""

    found: list[BriefIn]
    problem: Problem | None
    skipped: int


def scout_file() -> Path:
    """Where the file is: ``CLIPFORGE_SCOUT_FILE`` when set (every test sets it), else HeyClicky's own."""
    override = os.environ.get("CLIPFORGE_SCOUT_FILE")
    return Path(override).expanduser() if override else SCOUT_FILE


def _clean(text: str | None) -> str:
    return _SPACES.sub(" ", clean_text(text or "") or "").strip()


def _number(text: str) -> float | None:
    value = float(text.replace(",", ""))
    return value if math.isfinite(value) else None


def money(text: str | None) -> float | None:
    """The first US$ amount in ``text`` ("$1,335.66" gives 1335.66); None when it gives none, as "Not
    publicly shown" does."""
    found = _MONEY.search(_clean(text))
    return _number(found.group(1)) if found else None


def _per_1000(text: str) -> float | None:
    per_views = _PER_VIEWS.search(text)
    if per_views:
        amount, views = _number(per_views.group(1)), _number(per_views.group(2))
        if amount is None or views is None:
            return None
        views *= {"k": 1_000, "m": 1_000_000}.get((per_views.group(3) or "").lower(), 1)
        return round(amount * 1_000 / views, 6) if views > 0 else None
    cpm = _CPM.search(text)
    return _number(cpm.group(1)) if cpm else None


def _by_platform(
    text: str | None, value: Callable[[str], float | None]
) -> tuple[float | None, dict[Platform, float]]:
    """``value`` of each "<Platform> ..." part split on ";", kept for TikTok and Instagram, with the highest
    of them; a text naming no platform gives its one value for all."""
    cleaned = _clean(text)
    named, per = False, {}
    for part in cleaned.split(";"):
        site = _NAMED.match(part)
        if site is None:
            continue
        named = True
        platform = site.group(1).lower()
        amount = value(part)
        if platform in _OURS and amount is not None:
            per[platform] = amount
    if named:
        return (max(per.values()) if per else None), per
    return value(cleaned), {}


def rates(text: str | None) -> tuple[float | None, dict[Platform, float]]:
    """A rate per 1,000 views and the per-platform rates: "$1.00 CPM across listed platforms" gives 1.0 and
    {}; "TikTok $0.50 CPM; Instagram $1.50 CPM" gives 1.5 and both; "$9.00 per 10K views" gives 0.9; a
    per-post payout gives None."""
    return _by_platform(text, _per_1000)


def caps(text: str | None) -> tuple[float | None, dict[Platform, float]]:
    """The most one clip can earn, per platform ("TikTok: $150.00 gross; ...") and the highest of them."""
    return _by_platform(text, money)


def platforms(text: str | None) -> list[Platform] | None:
    """TikTok and Instagram as the text names them; [] when it names only other platforms (the row is left
    out), None when it names none (both, then)."""
    named = {word.lower() for word in _SITE_WORDS.findall(_clean(text))}
    if not named:
        return None
    return [p for p in _OURS if p in named]


def marketplace_of(text: str | None) -> str:
    """The marketplace a row's platform cell names: "Whop" for Whop's Content Rewards, "Vyro", else the
    cell's own words."""
    cleaned = _clean(text)
    folded = cleaned.casefold()
    if folded.startswith("whop") or "content rewards" in folded:
        return "Whop"
    if "vyro" in folded:
        return "Vyro"
    return cleaned[:40] or "Unknown"


def _link(text: str | None) -> str | None:
    """The cell as an http or https link with a host and no user name, else None."""
    link = (text or "").strip()
    if not link or len(link) > LINK_MAX or any(c.isspace() or not c.isprintable() for c in link):
        return None
    try:
        parts = urlsplit(link)
        if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
            return None
        if parts.username is not None or parts.password is not None:
            return None
        parts.port  # noqa: B018 (a port that isn't a number raises here)
    except ValueError:
        return None
    return link


def _day(text: str | None) -> str | None:
    cell = (text or "").strip()
    if not _ISO_DAY.fullmatch(cell):
        return None
    try:
        return date.fromisoformat(cell).isoformat()
    except ValueError:
        return None


def _brief(cells: dict[str, str], written_at: float) -> BriefIn | None:
    """One row as a brief, or None when it is left out."""
    title = _clean(cells["campaign_name"])[:200]
    url = _link(cells["campaign_url"])
    allowed = platforms(cells["allowed_platforms"])
    if not title or url is None or allowed == []:
        return None
    stated = ["title", "url"]
    brand = _clean(cells["brand"])[:200] or None
    rate, per_rate = rates(cells["rate_usd_per_1k"])
    top, per_cap = caps(cells["max_per_clip_usd"])
    total, spent = money(cells["budget_total_usd"]), money(cells["budget_spent_usd"])
    left = money(cells["budget_remaining_usd"])
    if left is None and total is not None and spent is not None:
        left = max(total - spent, 0.0)
    ends_on = _day(cells["end_date"])
    checked = _day(cells["checked_date"])
    text = (clean_lines(cells["public_brief_requirements"]) or "").strip()[:TEXT_MAX]
    values = {
        "brand": brand,
        "rate_per_1000": rate,
        "rates": per_rate,
        "max_payout": top,
        "caps": per_cap,
        "budget_total": total,
        "budget_spent": spent,
        "budget_left": left,
        "ends_on": ends_on,
        "platforms": allowed,
        "text": text,
    }
    stated += [name for name, value in values.items() if value not in (None, {}, "")]
    if checked:
        stated.append("budget_read_at")
    notes = {name: note for name in NOTES if (note := _clean(cells[name])[:NOTE_MAX])}
    gone = bool(_GONE.search(_clean(cells["campaign_status"]))) or left == 0
    try:
        return BriefIn(
            external_id=url,
            url=url,
            marketplace=marketplace_of(cells["platform"]),
            title=title,
            currency="USD",
            budget_read_at=datetime.combine(date.fromisoformat(checked), datetime.min.time()).timestamp()
            if checked
            else written_at,
            footage=Footage(kind=_clean(cells["source_footage_type"])[:300] or None),
            notes=notes,
            stated=stated,
            gone=gone,
            **values,
        )
    except ValidationError:
        return None


def _stamp(fd: int) -> tuple[int, int]:
    """The open file's size and when it was last written, to tell a file written to during the read."""
    st = os.fstat(fd)
    return st.st_size, st.st_mtime_ns


def _bytes(path: Path) -> tuple[bytes, float] | Problem:
    """The whole file and when it was written, or why it can't be read. It opens without waiting, so a
    pipe or a device at the path can't hold the morning up."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unreadable"
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_BYTES:
            return "unreadable"
        before = _stamp(fd)
        with os.fdopen(os.dup(fd), "rb") as f:
            raw = f.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES or _stamp(fd) != before:
            return "unreadable"
        return raw, st.st_mtime
    except OSError:
        return "unreadable"
    finally:
        os.close(fd)


def read_scout(path: Path) -> ScoutRead:
    """Reads the file at ``path`` as UTF-8 (its byte order mark dropped). A missing file, one that is not
    UTF-8 text, over 5 MB, half written or written to while it was read, or one whose header lacks any of
    the 23 columns gives its problem and no briefs. A link seen in an earlier row keeps that row."""
    got = _bytes(path)
    if isinstance(got, str):
        return ScoutRead([], got, 0)
    raw, written_at = got
    try:
        rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True))
    except (UnicodeDecodeError, csv.Error):
        return ScoutRead([], "unreadable", 0)
    if not rows:
        return ScoutRead([], "unreadable", 0)
    header = [name.strip() for name in rows[0]]
    if not set(COLUMNS) <= set(header):
        return ScoutRead([], "reshaped", 0)
    where = {name: header.index(name) for name in COLUMNS}
    body = [r for r in rows[1:] if any(cell.strip() for cell in r)]
    if any(len(r) != len(header) for r in body):
        return ScoutRead([], "unreadable", 0)
    found: list[BriefIn] = []
    links: set[str] = set()
    skipped = max(len(body) - MAX_ROWS, 0)
    for cells in body[:MAX_ROWS]:
        brief = _brief({name: cells[i] for name, i in where.items()}, written_at)
        if brief is None or brief.url in links:
            skipped += 1
            continue
        links.add(brief.url)
        found.append(brief)
    return ScoutRead(found, None, skipped)
