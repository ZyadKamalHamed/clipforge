"""Brief sources and the briefs they give (SPEC 10.11, 15). Money is in the brief's currency; a
per-platform figure is kept in ``rates`` and ``caps`` and the highest of them in ``rate_per_1000`` and
``max_payout``. Times are epoch seconds; days are dates written YYYY-MM-DD."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from clipforge.campaigns.models import Platform

Reader = Literal["campaign_scout", "general", "paste"]
Result = Literal["ok", "blocked", "sign_in", "changed", "error"]
Status = Literal["new", "seen", "hidden", "used", "gone"]
Filled = Literal["stated", "claude", "guess"]  # who gave a field: the source, Claude or the keyword guess
TEXT_MAX = 20_000
FIXED = {  # why a brief that became a campaign, or went from its marketplace, can't change
    "used": "This brief is a campaign now, so it stays with that campaign.",
    "gone": "This brief has gone from its marketplace.",
}
MISSING = "That brief isn't in ClipForge any more."
Money = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class BriefSource(BaseModel):
    id: str
    name: Annotated[str, Field(min_length=1, max_length=60)]
    marketplace: Annotated[str, Field(min_length=1, max_length=40)]
    list_url: str | None = None
    reader: Reader
    enabled: bool = True
    terms_checked_on: str | None = None
    terms_note: str | None = None
    last_run_at: float | None = None
    last_result: Result | None = None
    error: str | None = None
    created_at: float
    updated_at: float


class Footage(BaseModel):
    """What a brief says may be clipped: its own words for the kind, and the YouTube https links it gives."""

    kind: Annotated[str, Field(max_length=300)] | None = None
    links: Annotated[list[str], Field(max_length=10)] = []


class BriefIn(BaseModel):
    """A brief as a reader found it, before it is merged into the rows. ``stated`` names the fields the
    source gave; ``platforms`` None means the source didn't say, so both."""

    external_id: Annotated[str, Field(min_length=1, max_length=2000)]
    url: str | None = None
    marketplace: Annotated[str, Field(min_length=1, max_length=40)]
    brand: Annotated[str, Field(max_length=200)] | None = None
    title: Annotated[str, Field(min_length=1, max_length=200)]
    currency: str = "USD"
    rate_per_1000: Money | None = None
    rates: dict[Platform, Money] = {}
    min_views: Annotated[int, Field(ge=0)] | None = None
    max_payout: Money | None = None
    caps: dict[Platform, Money] = {}
    budget_total: Money | None = None
    budget_spent: Money | None = None
    budget_left: Money | None = None
    budget_read_at: float | None = None
    ends_on: str | None = None
    platforms: list[Platform] | None = None
    footage: Footage = Footage()
    text: Annotated[str, Field(max_length=TEXT_MAX)] = ""
    notes: dict[str, str] = {}
    stated: list[str] = []
    gone: bool = False


class Brief(BaseModel):
    id: str
    source_id: str
    external_id: Annotated[str, Field(min_length=1, max_length=2000)]
    url: str | None = None
    marketplace: Annotated[str, Field(min_length=1, max_length=40)]
    brand: Annotated[str, Field(max_length=200)] | None = None
    title: Annotated[str, Field(min_length=1, max_length=200)]
    niche: str | None = None
    language: str | None = None
    currency: str = "USD"
    rate_per_1000: Money | None = None
    rates: dict[Platform, Money] = {}
    min_views: Annotated[int, Field(ge=0)] | None = None
    max_payout: Money | None = None
    caps: dict[Platform, Money] = {}
    budget_total: Money | None = None
    budget_spent: Money | None = None
    budget_left: Money | None = None
    budget_read_at: float | None = None
    ends_on: str | None = None
    platforms: Annotated[list[Platform], Field(min_length=1)] = ["tiktok", "instagram"]
    footage: Footage = Footage()
    text: Annotated[str, Field(max_length=TEXT_MAX)] = ""
    fields: dict[str, Filled] = {}
    notes: dict[str, str] = {}
    text_hash: str = ""
    status: Status = "new"
    campaign_id: str | None = None
    first_seen_at: float
    last_seen_at: float
