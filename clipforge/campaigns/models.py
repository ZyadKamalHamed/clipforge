"""Paid clipping campaigns, their submissions and the view counts of posts (SPEC 10.10, 15). A campaign
holds a marketplace campaign's rules and links, as Zyad checked them; money is in the campaign's currency.
Times are epoch seconds; days are dates in the posting time zone, YYYY-MM-DD."""

from __future__ import annotations

import re
from datetime import date
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator

# What ClipForge controls in a render and a rule can ban (10.10), with how the rule reads
BANNED: dict[str, str] = {
    "gameplay": "Gameplay split",
    "music": "Music",
    "sfx": "Sound effects",
    "emoji": "Emoji",
    "hook": "Hook title",
    "cards": "Fact cards",
    "progress_bar": "Progress bar",
    "punch_ins": "Punch-ins",
    "credit_line": "On-screen credit line",
}
Banned = Literal[
    "gameplay", "music", "sfx", "emoji", "hook", "cards", "progress_bar", "punch_ins", "credit_line"
]
Platform = Literal["tiktok", "instagram"]
SubmissionStatus = Literal["to_submit", "submitted", "approved", "rejected", "paid"]
URL_MAX = 2000
UC_ID = re.compile(r"^UC[\w-]{22}$")
VIDEO_ID = re.compile(r"^[\w-]{11}$")
MENTION = re.compile(r"^@[A-Za-z0-9._]{1,30}$")
CURRENCY = re.compile(r"^[A-Z]{3}$")
_UNSAFE = re.compile(r"[\s\x00-\x1f\x7f]")


def web_url(value: str) -> str:
    """``value`` trimmed, when it is a web link ClipForge may open: http or https, a host, no user or
    password and nothing that isn't printable. ValueError otherwise."""
    text = value.strip()
    if len(text) > URL_MAX or _UNSAFE.search(text):
        raise ValueError("A link must be one web address with no spaces in it.")
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("A link must start with https:// or http://.")
    if parts.username is not None or parts.password is not None:
        raise ValueError("A link can't have a user name or password in it.")
    return text


def day(value: str) -> str:
    """``value`` when it is a real date written YYYY-MM-DD. ValueError otherwise."""
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            date.fromisoformat(value)
            return value
    except ValueError:
        pass
    raise ValueError("A date must be a real day written like 2026-11-30.")


class FootageChannel(BaseModel):
    yt_channel_id: Annotated[str, Field(pattern=UC_ID.pattern)]
    title: Annotated[str, Field(max_length=300)] = ""
    url: str


class Footage(BaseModel):
    """What a campaign lets you clip: these YouTube channels and videos, and files and Drive links you add.
    All empty is no footage rule."""

    channels: Annotated[list[FootageChannel], Field(max_length=20)] = []
    videos: Annotated[list[Annotated[str, Field(pattern=VIDEO_ID.pattern)]], Field(max_length=50)] = []
    files: bool = False

    @property
    def any(self) -> bool:
        return not (self.channels or self.videos or self.files)

    def allows(self, video_id: str | None = None, yt_channel_id: str | None = None) -> bool:
        """Whether you may clip this YouTube video, of this channel: any footage when there is no rule."""
        return (
            self.any
            or (video_id is not None and video_id in self.videos)
            or any(c.yt_channel_id == yt_channel_id for c in self.channels)
        )


class Required(BaseModel):
    """What every caption of the campaign must carry."""

    hashtags: Annotated[list[Annotated[str, Field(min_length=1, max_length=100)]], Field(max_length=10)] = []
    mentions: Annotated[list[Annotated[str, Field(pattern=MENTION.pattern)]], Field(max_length=10)] = []
    text: Annotated[list[Annotated[str, Field(min_length=1, max_length=200)]], Field(max_length=5)] = []


class Campaign(BaseModel):
    id: str
    name: Annotated[str, Field(min_length=1, max_length=80)]
    marketplace: Annotated[str, Field(min_length=1, max_length=40)]
    campaign_url: str | None = None
    submit_url: str | None = None
    currency: str = "USD"
    rate_per_1000: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    min_views: Annotated[int, Field(ge=0)] | None = None
    max_payout: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    platforms: Annotated[list[Platform], Field(min_length=1)] = ["tiktok", "instagram"]
    footage: Footage = Footage()
    required: Required = Required()
    banned: list[Banned] = []
    hand_rules: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=200)]], Field(max_length=20)
    ] = []
    min_s: Annotated[float, Field(ge=5, le=600)] | None = None
    max_s: Annotated[float, Field(ge=5, le=600)] | None = None
    ends_on: str | None = None
    budget_left: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    budget_entered_on: str | None = None
    notes: Annotated[str, Field(max_length=4000)] = ""
    rules_text: Annotated[str, Field(max_length=20_000)] = ""
    status: Literal["active", "ended"] = "active"
    brief_id: str | None = None  # the brief Use this campaign started it from (SPEC 10.11)
    joined_at: float | None = None  # when you said you joined it on the marketplace
    created_at: float
    updated_at: float

    @field_validator("name", "marketplace", mode="before")
    @classmethod
    def _strip(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("campaign_url", "submit_url")
    @classmethod
    def _link(cls, value: str | None) -> str | None:
        return web_url(value) if value else None

    @field_validator("currency", mode="before")
    @classmethod
    def _currency(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip().upper()
            if not CURRENCY.fullmatch(value):
                raise ValueError("A currency is a three letter code like USD or AUD.")
        return value

    @field_validator("ends_on", "budget_entered_on")
    @classmethod
    def _day(cls, value: str | None) -> str | None:
        return day(value) if value else None

    @field_validator("platforms", "banned")
    @classmethod
    def _once(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def _lengths(self) -> Campaign:
        if self.min_s is not None and self.max_s is not None and self.min_s > self.max_s:
            raise ValueError("The shortest clip can't be longer than the longest.")
        return self


class RuleCheck(BaseModel):
    """One of a campaign's rules on a post: checked by ClipForge (auto) or ticked by you once checked (hand).
    ``sentence`` says what is wrong, empty when it passes."""

    key: str  # length, platform, footage, hashtag:<tag>, mention:<@name>, text:<n>, banned:<name>, ...
    label: str
    kind: Literal["auto", "hand"]
    ok: bool
    sentence: str = ""


class Submission(BaseModel):
    id: str
    post_id: str
    campaign_id: str
    status: SubmissionStatus = "to_submit"
    submitted_at: float | None = None
    decided_at: float | None = None
    reason: Annotated[str, Field(max_length=300)] | None = None
    paid_amount: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    paid_on: str | None = None
    created_at: float
    updated_at: float


class Metric(BaseModel):
    """One count of a post's views (and what else came with it), from insights or entered by hand."""

    post_id: str
    captured_at: float
    source: Literal["insights", "hand"]
    views: Annotated[int, Field(ge=0)]
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    saves: int | None = None
