"""Settings and their defaults (SPEC 19), plus which model each brain pass uses. A change from the Settings
page goes over the current settings and is checked as a whole (``changed``); a problem is one sentence
about the field it is in."""

from __future__ import annotations

import re
import types
from typing import Annotated, Any, Literal, Union, get_args, get_origin
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from clipforge.campaigns.models import CURRENCY, web_url
from clipforge.models import ProjectOptions

ProviderName = Literal["claude_cli", "anthropic", "gemini", "openai_compat", "heuristic"]
PASSES = ("discover", "rerank", "package", "review")  # the brain's passes, each with a model of its own
MODEL_NAME = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,99}"
)  # never a leading dash: names become arguments

NO_SUCH = "ClipForge has no setting called that."
NO_SUCH_OPTION = "The Create page has no option called that."
NO_SUCH_POSTING = "Posting has no setting called that."
NO_SUCH_WATCH = "Clipping channels have no setting called that."
NO_SUCH_TODAY = "Today has no setting called that."
NO_SUCH_BRIEFS = "Briefs have no setting called that."
NO_SUCH_TRENDS = "Trends have no setting called that."
CHOICE = "Pick one of the choices."
SWITCH = "That setting is on or off."
UNREADABLE = "ClipForge couldn't read that value."
EVERY_PASS = "Pick a model for every pass, or one model for all of them."
MODEL_NAME_WRONG = (
    "A model's name starts with a letter or number and has only letters, numbers and . _ : / @ + - in it."
)
PASS_WRONG = "Models can be set for discover, rerank, package and review, or * for all."
PROVIDER_WRONG = "Models can be set for Claude Code, the Anthropic API, Gemini and a local server."
PROBLEMS = {
    "llm_parallel": "AI calls at once must be a whole number from 1 to 10.",
    "llm_timeout_s": "The AI's time limit must be from 30 to 1800 seconds.",
    "llm_models": EVERY_PASS,
    "openai_compat_base_url": "The local AI server's address must start with http:// or https://.",
    "asr_language": "The speech language must be Auto or a code like en or es.",
    "full_download_max_gb": "The whole-video limit must be from 0 to 100 GB.",
    "min_free_gb": "The free space to keep must be 0 GB or more.",
    "own_channels": "List up to 50 of your channels, like @yourname.",
    "max_source_hours": "The longest video must be from 0.1 to 24 hours.",
    "delete_sources_after_days": "Keep source videos for 0 to 365 days (0 keeps them for good).",
    "create_options": "The Create page's options must be a set of choices.",
    "accent_color": "Accent colour must look like #FFD60A.",
    "posting": "Posting's settings must be a set of choices.",
    "watch": "Clipping channels' settings must be a set of choices.",
    "today": "Today's settings must be a set of choices.",
    "marketplaces": "Each marketplace has a Withdraw link and a minimum payout.",
}
OPTION_PROBLEMS = {
    "clip_count": "The number of clips must be a whole number.",
    "prompt": "What the clips should be about must be text.",
}
WINDOW_WRONG = "Each posting window has a start and an end like 07:00, and ends after it starts."
POSTING_PROBLEMS = {
    "windows": WINDOW_WRONG,
    "timezone": "Pick a time zone like Australia/Sydney.",
    "tiktok_daily_cap": "TikTok's daily cap must be a whole number from 1 to 10.",
    "instagram_daily_cap": "Instagram's daily cap must be a whole number from 1 to 10.",
    "tiktok_spacing_h": "The time between TikTok posts must be from 0 to 24 hours.",
    "instagram_spacing_h": "The time between Instagram posts must be from 0 to 24 hours.",
    "platform_gap_min": "The gap between platforms must be from 0 to 240 minutes.",
    "jitter_min": "The random shift must be from 0 to 60 minutes.",
    "graph_version": "The Graph API version must look like v25.0.",
    "public_base_url": "The public address must start with https://.",
    "away_min": "The wait for you must be from 1 to 120 minutes.",
    "telegram_target": "The Telegram chat must be a chat ID or an @name.",
}

WATCH_PROBLEMS = {
    "min_minutes": "The shortest video to clip must be from 0.5 to 60 minutes.",
    "max_hours": "The longest video to clip must be from 0.5 to 12 hours.",
}

RATES_WRONG = (
    "Each exchange rate is a currency code like AUD and what one of it is worth in US$, more than 0."
)
TODAY_PROBLEMS = {
    "notify_at": "The daily notification's time must look like 07:30.",
    "starting_views": "The starting guess must be a whole number of views from 1 to 10,000,000.",
    "rates": RATES_WRONG,
}
RATES_MAX = 20
NICHES_WRONG = (
    "Each niche has a name of up to 40 characters, a language like en and 1 to 12 tags "
    "of up to 40 characters."
)
NICHES_MAX = 30
BRIEF_PROBLEMS = {
    "read_at": "The time to read briefs must look like 06:00.",
    "read_per_morning": "The briefs Claude reads each morning must be a whole number from 0 to 1,000.",
    "niches": NICHES_WRONG,
}

Channel = Annotated[str, Field(min_length=1, max_length=200)]
Clock = Annotated[str, Field(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")]


class Window(BaseModel):
    """A posting window in wall-clock time, "HH:MM", ending after it starts on the same day."""

    start: Clock
    end: Clock

    @model_validator(mode="after")
    def _ends_after_start(self) -> Window:
        if self.end <= self.start:
            raise ValueError(WINDOW_WRONG)
        return self


DEFAULT_WINDOWS = [("07:00", "09:00"), ("12:00", "13:00"), ("18:00", "22:00")]


class PostingSettings(BaseModel):
    """Settings.posting: when and how often each platform gets a post (SPEC 10.4, call 8)."""

    paused: bool = False  # the kill switch, kept across restarts
    timezone: str = "Australia/Sydney"
    windows: Annotated[list[Window], Field(max_length=8)] = [
        Window(start=a, end=b) for a, b in DEFAULT_WINDOWS
    ]
    tiktok_daily_cap: Annotated[int, Field(ge=1, le=10)] = 2
    instagram_daily_cap: Annotated[int, Field(ge=1, le=10)] = 1
    tiktok_spacing_h: Annotated[float, Field(ge=0, le=24)] = 3.0
    instagram_spacing_h: Annotated[float, Field(ge=0, le=24)] = 6.0
    platform_gap_min: Annotated[int, Field(ge=0, le=240)] = 15
    jitter_min: Annotated[int, Field(ge=0, le=60)] = 10
    graph_version: Annotated[str, Field(pattern=r"^v[0-9]{2}\.[0-9]$")] = "v25.0"
    public_base_url: Annotated[str, Field(pattern=r"^https://[^\s/]+(/\S*)?$", max_length=300)] | None = (
        None  # Mode B: the https address in front of this Mac
    )
    away_min: Annotated[int, Field(ge=1, le=120)] = 10  # how long a window waits for the person (call 15)
    tiktok_schedule: bool = True  # set TikTok Studio's own schedule on an assisted post (10.3)
    telegram_alerts: bool = False
    telegram_target: Annotated[str, Field(pattern=r"^(-?[0-9]{1,20}|@[A-Za-z0-9_]{1,64})$")] | None = None

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(POSTING_PROBLEMS["timezone"]) from None
        return value

    @field_validator("public_base_url", mode="before")
    @classmethod
    def _base_url(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip().rstrip("/")
            return value or None
        return value


class WatchSettings(BaseModel):
    """Settings.watch: which new uploads a clipping channel skips for their length (SPEC 10.9)."""

    min_minutes: Annotated[float, Field(ge=0.5, le=60)] = 4.0
    max_hours: Annotated[float, Field(ge=0.5, le=12)] = 4.0


class TodaySettings(BaseModel):
    """Settings.today: the Daily dashboard (SPEC 14.10, 18, 10.11). The daily notification goes at
    ``notify_at``; the others, one for each post, draft and project, only once ``event_notifications`` is on.
    ``starting_views`` is the 7-day views guessed for an account with too few counted posts, and ``rates``
    say what one unit of a currency is worth in US$, so amounts in different currencies can be compared.
    ``guide`` shows the path from nothing to your first payout, its steps on the list as they open."""

    notify_at: Clock = "07:30"
    daily_notification: bool = True
    event_notifications: bool = False
    starting_views: Annotated[int, Field(ge=1, le=10_000_000)] = 1000
    rates: dict[str, float] = {}
    guide: bool = True

    @field_validator("rates", mode="before")
    @classmethod
    def _rates(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            raise ValueError(RATES_WRONG)
        if len(value) > RATES_MAX:
            raise ValueError(f"Keep it to {RATES_MAX} exchange rates or fewer.")
        for code, rate in value.items():
            if not isinstance(code, str) or not CURRENCY.match(code):
                raise ValueError(RATES_WRONG)
            if isinstance(rate, bool) or not isinstance(rate, int | float) or not 0 < rate < 1e6:
                raise ValueError(RATES_WRONG)
        return {code: float(rate) for code, rate in value.items()}


MARKETPLACES_MAX = 20


class Marketplace(BaseModel):
    """Settings.marketplaces[name]: where you withdraw a marketplace's earnings and the least it pays out
    (SPEC 10.10, 14.9). ClipForge only opens the link; it never logs in there."""

    withdraw_url: str | None = None
    min_payout: float | None = None

    @field_validator("withdraw_url")
    @classmethod
    def _link(cls, value: str | None) -> str | None:
        if not value or not value.strip():
            return None
        try:
            return web_url(value)
        except ValueError:
            raise ValueError(
                "A Withdraw link must be one web address starting with https:// or http://."
            ) from None

    @field_validator("min_payout", mode="before")
    @classmethod
    def _payout(cls, value: Any) -> Any:
        if value is None or value == "":
            return None
        if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value < 1e9:
            raise ValueError("A minimum payout must be a number of 0 or more.")
        return float(value)


class Niche(BaseModel):
    """A niche briefs and advice are sorted into: its name, its language and the tags that mark an account,
    or a brief's words, as being in it (SPEC 10.11). Tags are kept in lower case, each once."""

    name: Annotated[str, Field(min_length=1, max_length=40)]
    language: Annotated[str, Field(pattern=r"^[a-z]{2,8}$")] = "en"
    tags: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=40)]], Field(min_length=1, max_length=12)
    ]

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, value: Any) -> Any:
        return " ".join(value.split()) if isinstance(value, str) else value

    @field_validator("tags", mode="before")
    @classmethod
    def _tags(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        kept: list[Any] = []
        for tag in value:
            tag = " ".join(tag.split()).lower() if isinstance(tag, str) else tag
            if tag not in kept:
                kept.append(tag)
        return kept


def _niche(name: str, *tags: str) -> Niche:
    return Niche(name=name, tags=list(tags))


DEFAULT_NICHES = [
    _niche("Reality TV and drama", "reality", "drama", "reality tv", "love island", "bachelor"),
    _niche("Gaming", "gaming", "games", "gamer", "fortnite", "minecraft", "valorant", "roblox"),
    _niche("Streamers", "streamer", "streamers", "stream", "twitch", "kick"),
    _niche("Podcasts and interviews", "podcast", "podcasts", "interview", "interviews"),
    _niche("Sports", "sports", "football", "soccer", "nba", "nfl", "ufc", "boxing", "f1"),
    _niche("Comedy and entertainment", "comedy", "funny", "entertainment", "skits", "standup"),
    _niche("Music", "music", "song", "songs", "artist", "rapper", "album"),
    _niche(
        "Finance and business",
        "finance",
        "business",
        "money",
        "investing",
        "crypto",
        "trading",
        "entrepreneur",
    ),
    _niche("Tech", "tech", "technology", "ai", "software", "app", "gadgets"),
    _niche("Lifestyle and fitness", "lifestyle", "fitness", "gym", "health", "wellness", "beauty", "fashion"),
    _niche("Education", "education", "learning", "science", "history", "study"),
    _niche("News and politics", "news", "politics", "political", "election"),
]


class TrendSettings(BaseModel):
    """Settings.trends: ``list_ping`` reads one public feed once a week to say when the trend lists you open
    have a new edition (SPEC 13)."""

    list_ping: bool = True


class BriefSettings(BaseModel):
    """Settings.briefs: briefs are read once a day from ``read_at`` in the posting time zone, after the
    clipping channels' checks; Claude fills in up to ``read_per_morning`` of them; ``niches`` are what briefs
    and the advice are sorted into (SPEC 10.11, 22)."""

    read_at: Clock = "06:00"
    niches: list[Niche] = DEFAULT_NICHES
    read_per_morning: Annotated[int, Field(ge=0, le=1000)] = 200

    @field_validator("niches", mode="before")
    @classmethod
    def _niches(cls, value: Any) -> Any:
        if not isinstance(value, list):
            raise ValueError(NICHES_WRONG)
        if len(value) > NICHES_MAX:
            raise ValueError(f"Keep it to {NICHES_MAX} niches or fewer.")
        names = [" ".join(n.get("name", "").split()).casefold() for n in value if isinstance(n, dict)]
        if len(names) != len(set(names)):
            raise ValueError("Two niches can't share a name.")
        return value


class Settings(BaseModel):
    llm_provider: ProviderName = "claude_cli"
    llm_parallel: Annotated[int, Field(ge=1, le=10)] = 3
    llm_timeout_s: Annotated[float, Field(ge=30, le=1800)] = 180.0
    llm_economy: bool = False  # "Economy": Sonnet for every pass (SPEC 5, call 17)
    llm_models: dict[str, dict[str, str]] = {}  # provider -> {pass or "*": model}, beats the defaults
    on_llm_limit: Literal["wait", "basic"] = "wait"
    openai_compat_base_url: Annotated[str, Field(pattern=r"^https?://\S+$", max_length=300)] = (
        "http://127.0.0.1:11434/v1"
    )
    asr_engine: Literal["auto", "parakeet", "whisper", "captions"] = "auto"
    asr_language: Annotated[str, Field(pattern=r"^(auto|[a-z]{2,3})$")] = (
        "auto"  # a code beats the detected one
    )
    download_quality: Literal["best", "balanced", "fast"] = "best"
    full_download_max_gb: Annotated[float, Field(ge=0, le=100)] = 2.5
    comments: bool = True
    live_chat: bool = True
    auto_render: Literal["top5", "top10", "all", "none"] = "top10"
    allow_other_sites: bool = False
    min_free_gb: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 10.0
    own_channels: Annotated[list[Channel], Field(max_length=50)] = []
    max_source_hours: Annotated[float, Field(ge=0.1, le=24)] = 6.0
    delete_sources_after_days: Annotated[int, Field(ge=0, le=365)] = 14  # 0 keeps them for good
    create_options: ProjectOptions = ProjectOptions()  # the Create page's last options
    # The look of a render. Until style kits per account arrive (Phase 5) these are the one default.
    encoder: Literal["fast", "best"] = "fast"  # fast: VideoToolbox, best: libx264 (SPEC 7.10)
    fps_cap_30: bool = False
    caption_preset: Literal["bold_pop", "hormozi", "beast", "pill", "clean", "one_word", "neon"] = "bold_pop"
    accent_color: Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$")] | None = None  # None: the preset's own
    hook_whole_clip: bool = False
    punch_ins: bool = True
    pause_trim: bool = True
    filler_removal: bool = True
    auto_pace: bool = False
    credit_line: bool = True
    emoji: bool = True
    sfx: bool = True
    progress_bar: bool = False
    cold_open: bool = False  # start clips on their strongest line when one is found (SPEC 8.3)
    gameplay_mode: Literal["auto", "off", "always"] = "auto"
    theme: Literal["dark", "light", "system"] = "dark"  # the window's look (SPEC 14.7)
    posting: PostingSettings = PostingSettings()
    watch: WatchSettings = WatchSettings()
    marketplaces: dict[str, Marketplace] = {}  # by the name campaigns give, like Whop
    today: TodaySettings = TodaySettings()
    briefs: BriefSettings = BriefSettings()
    trends: TrendSettings = TrendSettings()

    @field_validator("marketplaces", mode="before")
    @classmethod
    def _marketplaces(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if len(value) > MARKETPLACES_MAX:
            raise ValueError(f"Settings hold up to {MARKETPLACES_MAX} marketplaces.")
        named = {}
        for name, entry in value.items():
            name = name.strip() if isinstance(name, str) else ""
            if not 1 <= len(name) <= 40:
                raise ValueError("A marketplace's name must be 1 to 40 characters.")
            named[name] = entry
        return named

    @field_validator("asr_language", mode="before")
    @classmethod
    def _language_code(cls, value: Any) -> Any:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("own_channels", mode="before")
    @classmethod
    def _channels(cls, value: Any) -> Any:
        return [v.strip() if isinstance(v, str) else v for v in value] if isinstance(value, list) else value

    @field_validator("llm_models")
    @classmethod
    def _models(cls, value: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
        """Models for the providers that take them, by pass or "*", each named safely, and none that
        leaves a pass without a model."""
        cleaned = {
            provider: {key: name.strip() for key, name in models.items()}
            for provider, models in value.items()
        }
        for provider, models in cleaned.items():
            if provider not in DEFAULT_MODELS:
                raise ValueError(PROVIDER_WRONG)
            if not set(models) <= {*PASSES, "*"}:
                raise ValueError(PASS_WRONG)
            if any(name and not MODEL_NAME.fullmatch(name) for name in models.values()):
                raise ValueError(MODEL_NAME_WRONG)
            if not all(_named(models, p) for p in PASSES):
                raise ValueError(EVERY_PASS)
        return cleaned


def _named(models: dict[str, str], pass_name: str) -> bool:
    """Whether an override leaves ``pass_name`` a model: its own, else "*", else (with neither named) the
    default table's, which has one for every pass."""
    if pass_name in models or "*" in models:
        return bool(models.get(pass_name) or models.get("*"))
    return True


DEFAULT_MODELS: dict[str, dict[str, str]] = {
    "claude_cli": {"discover": "opus", "rerank": "opus", "package": "sonnet", "review": "sonnet"},
    "anthropic": {
        "discover": "claude-opus-5-5",
        "rerank": "claude-opus-5-5",
        "package": "claude-sonnet-5-5",
        "review": "claude-sonnet-5-5",
    },
    "gemini": {"*": "gemini-flash-latest"},
    "openai_compat": {"*": "qwen3:14b"},
}

ECONOMY_MODELS: dict[str, str] = {"claude_cli": "sonnet", "anthropic": "claude-sonnet-5-5"}


def model_for(settings: Settings, provider: str, pass_name: str) -> str:
    """The model for one pass: the user's override, then Economy, then the default table."""
    override = settings.llm_models.get(provider, {})
    if pass_name in override or "*" in override:
        return override.get(pass_name) or override.get("*", "")
    if settings.llm_economy and provider in ECONOMY_MODELS:
        return ECONOMY_MODELS[provider]
    table = DEFAULT_MODELS.get(provider, {})
    return table.get(pass_name) or table.get("*") or ""


class SettingError(ValueError):
    """A change the Settings page can't make: the field it is about and one sentence saying why."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field
        self.message = message


# The settings that are a set of their own: the model, its sentences and the sentence for a name it lacks.
NESTED: dict[str, tuple[type[BaseModel], dict[str, str], str]] = {
    "create_options": (ProjectOptions, OPTION_PROBLEMS, NO_SUCH_OPTION),
    "posting": (PostingSettings, POSTING_PROBLEMS, NO_SUCH_POSTING),
    "watch": (WatchSettings, WATCH_PROBLEMS, NO_SUCH_WATCH),
    "today": (TodaySettings, TODAY_PROBLEMS, NO_SUCH_TODAY),
    "briefs": (BriefSettings, BRIEF_PROBLEMS, NO_SUCH_BRIEFS),
    "trends": (TrendSettings, {}, NO_SUCH_TRENDS),
}


def changed(settings: Settings, changes: dict[str, Any]) -> Settings:
    """``settings`` with ``changes`` put over it, each the whole new value of a field, checked as a whole.
    The first problem is a SettingError naming its field ("create_options.length" inside the Create page's
    options, "posting.windows" inside posting's)."""
    for name in changes:
        if name not in Settings.model_fields:
            raise SettingError(name, NO_SUCH)
    for outer, (model, _, no_such) in NESTED.items():
        inner = changes.get(outer)
        for name in inner if isinstance(inner, dict) else ():
            if name not in model.model_fields:
                raise SettingError(f"{outer}.{name}", no_such)
    try:
        return Settings.model_validate(settings.model_dump() | changes)
    except ValidationError as err:
        raise SettingError(*_problem(err.errors()[0])) from None


def _problem(error: Any) -> tuple[str, str]:
    """The field a validation error is about and its sentence."""
    loc = [str(part) for part in error["loc"]]
    model: type[BaseModel] = Settings
    table, prefix = PROBLEMS, ""
    if loc[0] in NESTED and len(loc) > 1:
        model, table, _ = NESTED[loc[0]]
        prefix, loc = f"{loc[0]}.", loc[1:]
    name = loc[0]
    field = prefix + name
    if error["type"] == "value_error":
        return field, str(error["ctx"]["error"])
    if name in table:
        return field, table[name]
    annotation = model.model_fields[name].annotation
    if choices_of(annotation) is not None:
        return field, CHOICE
    if annotation is bool or bool in get_args(annotation):
        return field, SWITCH
    return field, UNREADABLE


def choices_of(annotation: Any) -> list[Any] | None:
    """The values a Literal field (or an optional one) can take, None left out; None for any other field."""
    if get_origin(annotation) is Literal:
        return list(get_args(annotation))
    if get_origin(annotation) in (Union, types.UnionType):
        for arg in get_args(annotation):
            if get_origin(arg) is Literal:
                return list(get_args(arg))
    return None
