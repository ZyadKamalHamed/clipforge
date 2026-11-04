"""Clipping channels, the YouTube channels they watch, the videos each check saw and the checks themselves
(SPEC 10.9, 15). Times are epoch seconds; a check's day is a date in the posting time zone."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator

from clipforge.config import Clock
from clipforge.models import ProjectOptions

RightsBasis = Literal["not_cleared", "permission", "clipping_programme"]
OnLimit = Literal["wait", "economy", "basic"]
ItemStatus = Literal["new", "skipped", "waiting", "clipping", "drafted", "failed"]
NAME_MAX = 60
TITLE_MAX = 300
FOOTAGE = "campaign:"  # the yt_channel_id of a source holding a campaign's own footage videos
# A clipping channel's on_limit, as its projects' on_limit and economy (SPEC 10.9)
LIMIT_OPTIONS: dict[str, dict[str, object]] = {
    "wait": {"on_limit": "wait", "economy": None},
    "economy": {"on_limit": "wait", "economy": True},
    "basic": {"on_limit": "basic", "economy": None},
}


class ChannelOptions(BaseModel):
    project: ProjectOptions = ProjectOptions()  # every new video's project gets these
    hashtags: Annotated[list[str], Field(max_length=10)] = []  # first on every draft, before the plan's


class ClipChannel(BaseModel):
    id: str
    name: Annotated[str, Field(min_length=1, max_length=NAME_MAX)]
    account_ids: list[str] = []
    options: ChannelOptions = ChannelOptions()
    rights_basis: RightsBasis = "not_cleared"
    check_time: Clock = "06:00"
    daily_video_limit: Annotated[int, Field(ge=1, le=10)] = 3
    clips_per_day: Annotated[int, Field(ge=1, le=50)] | None = None  # None: what the accounts can post
    on_limit: OnLimit = "wait"
    paused: bool = False
    last_check_on: str | None = None  # the day of the last daily check, YYYY-MM-DD
    campaign_id: str | None = None  # the paid campaign its projects are linked to (SPEC 10.10)
    created_at: float
    updated_at: float

    @field_validator("name", mode="before")
    @classmethod
    def _strip(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    def project_options(self) -> ProjectOptions:
        """The options each new video's project gets: the clipping channel's, with what to do at a usage
        limit."""
        return self.options.project.model_copy(update=LIMIT_OPTIONS[self.on_limit])


class WatchedSource(BaseModel):
    id: str
    clip_channel_id: str
    yt_channel_id: str  # UC...
    title: str = ""
    url: str  # the channel's videos tab
    avatar_path: str | None = None
    start_published_at: float  # uploads before this aren't new
    newest_video_id: str | None = None
    newest_title: str | None = None
    newest_published_at: float | None = None
    last_feed_at: float | None = None
    error: str | None = None  # one sentence from the last check, or None
    created_at: float

    @property
    def is_footage(self) -> bool:
        """Whether it holds a campaign's footage videos rather than watching a YouTube channel: it has no
        feed and is never listed (SPEC 10.10)."""
        return self.yt_channel_id.startswith(FOOTAGE)


def footage_key(campaign_id: str) -> str:
    """The yt_channel_id of the source that holds the campaign's footage videos."""
    return f"{FOOTAGE}{campaign_id}"


class WatchItem(BaseModel):
    id: str
    source_id: str
    clip_channel_id: str
    check_id: str | None = None  # the check that clipped it
    video_id: str
    title: str = ""
    published_at: float | None = None
    status: ItemStatus
    reason: str | None = None  # a SKIP_SENTENCES code
    project_id: str | None = None
    first_seen_at: float
    updated_at: float


class WatchCheck(BaseModel):
    id: str
    clip_channel_id: str
    check_on: str
    manual: bool = False  # Check now, which doesn't count as the day's check
    started_at: float
    finished_at: float | None = None
    found: int = 0
    skipped: int = 0
    clipped: int = 0
    drafts: int | None = None  # None until its projects have settled
    error: str | None = None  # a code: rate_limited, or None
    drafted_at: float | None = None


class FeedEntry(BaseModel):
    video_id: str
    title: str
    published_at: float
    is_short: bool = False


class ChannelListing(BaseModel):
    channel_id: str
    title: str
    url: str
    entries: list[tuple[str, str]] = []  # (video id, title), newest first


# Why a video wasn't clipped, by the code a watch item keeps in ``reason``.
SKIP_SENTENCES: dict[str, str] = {
    "short": "A Short.",
    "live": "A live stream that hasn't finished.",
    "upcoming": "A premiere that hasn't started.",
    "processing": "Still processing on YouTube.",
    "too_short": "Under {min} minutes.",
    "too_long": "Over {max} hours.",
    "private": "A private video.",
    "members": "For channel members only.",
    "age": "Age restricted.",
    "unavailable": "Not available.",
    "expired": "Waited 3 days without a turn.",
    "over_limit": "Over today's limit of {n}; it waits for the next check.",
    "not_allowed": "The campaign {name} doesn't allow footage from this channel.",
}
