"""Accounts, posts and the plan Confirm and post works from (SPEC 10, 15). Times are epoch seconds, as
everywhere else in ClipForge; the window formats them."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from clipforge.campaigns.models import RuleCheck
from clipforge.config import Window
from clipforge.render.styles import StyleKit
from clipforge.trends.models import PostSound

Platform = Literal["tiktok", "instagram"]
Method = Literal["browser", "api"]
AccountStatus = Literal["connected", "needs_login", "token_expiring", "error"]
PostStatus = Literal[
    "scheduled",
    "rendering",
    "uploading",
    "processing",
    "scheduled_on_tiktok",
    "posted",
    "needs_you",
    "failed",
    "cancelled",
    "exported",
    "draft",
    "discarded",
]

POST_STATUSES: tuple[str, ...] = (
    "scheduled",
    "rendering",
    "uploading",
    "processing",
    "scheduled_on_tiktok",  # uploaded, TikTok Studio putting it up at its slot; its link is read after (10.3)
    "posted",
    "needs_you",
    "failed",
    "cancelled",
    "exported",
    "draft",  # a clipping channel's, waiting for approval; it takes no slot and has no job (SPEC 10.9)
    "discarded",  # a draft let go; Undo brings it back
)
ACTIVE = ("scheduled", "rendering", "uploading", "processing", "scheduled_on_tiktok")
FINAL = ("posted", "cancelled", "exported")


class AccountSettings(BaseModel):
    niche_tags: list[str] = []  # up to 10, lower case
    assisted: bool = False  # TikTok: ClipForge fills everything and the person presses Post
    hashtags: list[str] = []  # added to every post, up to 10
    cta: str = ""  # up to 150 characters
    daily_cap: int | None = None  # None: the platform's default
    windows: list[Window] | None = None
    pair_id: str | None = None  # the account on the other platform that gets the same clips
    creator_rewards: bool = False  # TikTok: clips over 60 s only (call 16)
    style_kit: StyleKit = StyleKit()  # the account's look, over each clip's own for its posts (SPEC 11)


class Account(BaseModel):
    id: str
    platform: Platform
    method: Method
    label: str = ""
    handle: str | None = None
    display_name: str | None = None
    avatar_path: str | None = None
    status: AccountStatus = "needs_login"
    paused: bool = False
    status_note: str | None = None
    profile_dir: str | None = None
    ig_mode: Literal["facebook", "instagram"] | None = None
    ig_user_id: str | None = None
    fb_page_id: str | None = None
    token_ref: str | None = None  # the Keychain item, never the token
    token_expires_at: float | None = None
    token_refreshed_at: float | None = None
    settings: AccountSettings = AccountSettings()
    created_at: float
    last_checked_at: float | None = None
    deleted_at: float | None = None


class PostOptions(BaseModel):
    visibility: Literal["everyone", "friends", "only_me"] = "everyone"  # TikTok
    comments: bool = True  # TikTok
    duet: bool = True  # TikTok
    stitch: bool = True  # TikTok
    disclosure: bool = False  # TikTok content disclosure
    share_to_feed: bool = True  # Instagram
    trial_reel: bool = False  # Instagram through Meta's API: shown at first only to non-followers
    trial_graduation: Literal["manual", "auto"] = "manual"  # auto: Instagram shares it if it does well
    assisted: bool = False  # copied from the account at confirm
    sound: PostSound | None = None  # picked for this post alone, kept whatever its list does after


class Post(BaseModel):
    id: str
    batch_id: str
    clip_id: str | None
    clip_title: str
    render_id: str | None
    account_id: str
    platform: str
    method: str
    caption: str
    hashtags: list[str]
    options: PostOptions
    scheduled_at: float
    status: PostStatus
    attempts: int = 0
    external_id: str | None = None
    url: str | None = None
    error: str | None = None
    error_code: str | None = None
    details: str | None = None
    note: str | None = None
    state: dict[str, Any] = {}  # resumable steps: container, offset, poll_started, publish_requested_at
    log_path: str | None = None
    posted_at: float | None = None
    video_key: str  # with start_s and end_s, the already-posted guard after the clip is deleted
    start_s: float
    end_s: float
    created_at: float
    updated_at: float


class Capabilities(BaseModel):
    local_file: bool
    needs_public_url: bool
    caption: bool
    schedules_server_side: bool
    needs_human_finish: bool
    max_duration_s: float
    max_size_mb: int
    daily_cap: int


class Health(BaseModel):
    status: AccountStatus
    sentence: str
    details: str | None = None


class PublishResult(BaseModel):
    external_id: str | None
    url: str | None
    warnings: list[str] = []
    scheduled: bool = False  # TikTok Studio holds the post to put up at its slot; it isn't up yet


class PostEdit(BaseModel):
    caption: str | None = None
    hashtags: list[str] | None = None
    options: PostOptions | None = None  # only the switches sent change; the rest come from the plan
    # A linked post's hand rules you ticked (each a "hand:" key from its rule's words), and whether it
    # is unlinked from its campaign
    rule_ticks: Annotated[list[Annotated[str, Field(max_length=20)]], Field(max_length=20)] | None = None
    off_campaign: bool | None = None


class PlanRequest(BaseModel):
    clip_ids: list[str] = []  # 1 to 100, in the order they were selected; none with draft ids
    account_ids: list[str] = []  # 1 to 24; none with draft ids
    mode: Literal["spread", "every"] = "spread"
    timing: Literal["now", "smart", "custom"] = "smart"
    custom_start: float | None = None  # custom: a start plus an interval
    custom_interval_min: int | None = None
    custom_times: dict[str, float] = {}  # or a time per "<clip_id>:<account_id>"
    edits: dict[str, PostEdit] = {}  # per "<clip_id>:<account_id>"
    # Details' "Every TikTok post": the switches sent go on every post there, under a post's own edit
    platform_options: dict[Literal["tiktok", "instagram"], PostOptions] = {}
    acknowledged_every: bool = False
    # The review step on a clipping channel's drafts (SPEC 10.9): the drafts, with their clips, accounts,
    # words and slots, stand in for the clips and accounts, and Confirm schedules those very posts
    draft_ids: list[str] = []


class PlannedPost(BaseModel):
    key: str
    clip_id: str
    account_id: str
    platform: str
    caption: str
    hashtags: list[str]
    options: PostOptions
    scheduled_at: float
    warnings: list[str]  # codes into Plan.warnings
    rules: list[RuleCheck] = []  # its campaign's rules, checked; none when it has none
    rule_ticks: list[str] = []
    off_campaign: bool = False
    campaign_id: str | None = None  # the campaign it is planned for, which its submission will be for


class PlanWarning(BaseModel):
    code: str
    sentence: str
    key: str | None = None
    blocking: bool = False
    move_to: float | None = None


class Plan(BaseModel):
    plan_id: str
    posts: list[PlannedPost]
    warnings: list[PlanWarning]
    blocked: bool
    renders: list[str]
