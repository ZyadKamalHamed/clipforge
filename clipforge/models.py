"""Shared pydantic models. The names and fields are the contract between the pipeline's parts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, field_validator

PROMPT_MAX = 300


class ProjectOptions(BaseModel):
    length: Literal["auto", "short", "standard", "long"] = "auto"
    clip_count: int | None = None
    content_type: Literal["auto", "podcast", "solo", "stream", "other"] = "auto"
    prompt: str | None = None  # "What should clips be about?"
    quality: Literal["best", "balanced", "fast"] | None = None
    credit: bool | None = None  # None: on unless the rights basis is "own"
    render: bool = True  # False for --no-render
    provider: str | None = None  # overrides Settings.llm_provider for this project
    layout: Literal["auto", "face", "stacked", "gameplay", "stream", "fit", "motion"] = (
        "auto"  # under a clip's own
    )
    caption_preset: Literal["bold_pop", "hormozi", "beast", "pill", "clean", "one_word", "neon"] | None = None
    # A clipping channel's own answer to Claude's usage limit and model choice, over Settings' (SPEC 10.9)
    on_limit: Literal["wait", "basic"] | None = None
    economy: bool | None = None
    as_is: bool = False  # Post as it is: the whole video is one clip, uncut (SPEC 14.2)

    @field_validator("prompt")
    @classmethod
    def _cap_prompt(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value[:PROMPT_MAX] or None


LENGTH_RANGES: dict[str, tuple[float, float]] = {
    "auto": (15.0, 75.0),
    "short": (15.0, 35.0),
    "standard": (30.0, 60.0),
    "long": (60.0, 90.0),
}


class Project(BaseModel):
    id: str
    source_kind: str
    source_url: str | None = None
    video_id: str | None = None
    title: str | None = None
    channel: str | None = None
    channel_url: str | None = None
    duration_s: float | None = None
    language: str | None = None
    license: str | None = None
    rights_basis: str = "not_cleared"
    focus_s: float | None = None
    prompt: str | None = None
    options: ProjectOptions = ProjectOptions()
    status: str = "queued"  # queued, running, waiting, ready, failed, refused
    stage: str | None = None
    progress: float = 0.0
    message: str | None = None
    mode: str | None = None
    error_code: str | None = None
    error: str | None = None
    error_details: str | None = None  # the technical side of ``error``, behind "Show details"
    clip_channel_id: str | None = None  # the clipping channel that started it (SPEC 10.9)
    campaign_id: str | None = None  # the paid campaign it is linked to (SPEC 10.10)
    created_at: float
    updated_at: float


class ClipRow(BaseModel):
    id: str
    project_id: str
    rank: int | None = None
    start_s: float
    end_s: float
    sentence_ids: list[str] = []
    scores: dict[str, int] = {}
    llm_score: float | None = None
    rerank_score: float | None = None
    signal_score: float | None = None
    final_score: float | None = None
    tier: str | None = None
    passes_gates: bool | None = None
    archetype: str | None = None
    reason: str | None = None
    hook_titles: list[str] = []  # Discover's ideas, until packaging writes the real one
    topic_tags: list[str] = []  # what it's about, for matching clips to accounts' niches (SPEC 10.4)
    evidence: list[dict[str, Any]] = []
    flags: dict[str, list[str]] = {}  # red, soft and notes
    packaging: dict[str, Any] | None = None
    review: dict[str, Any] | None = None
    edits: dict[str, Any] | None = None
    layout: str | None = None
    layout_reason: str | None = None
    mode: str | None = None
    status: str = "idea"
    current_render_id: str | None = None
    error: str | None = None  # why the clip failed, in a sentence
    discarded_at: float | None = None  # when it was set aside; set-aside clips are never rendered
    created_at: float = 0.0
    updated_at: float = 0.0


class Chapter(BaseModel):
    start: float
    end: float
    title: str


class HeatBin(BaseModel):
    start: float
    end: float
    value: float


class Comment(BaseModel):
    text: str
    likes: int = 0


class SourceMeta(BaseModel):
    """What ClipForge knows about a source before downloading the video (SPEC 7.1)."""

    source_kind: Literal["youtube", "drive", "file", "other"]
    video_id: str | None
    title: str
    channel: str | None
    channel_url: str | None
    duration_s: float
    upload_date: str | None = None
    description: str = ""
    language: str | None = None
    license: str | None = None
    is_creative_commons: bool = False
    chapters: list[Chapter] = []
    heatmap: list[HeatBin] = []
    live_status: str | None = None
    availability: str | None = None
    age_limit: int = 0
    view_count: int | None = None
    thumbnail_url: str | None = None
    caption_track: str | None = None
    video_bytes: dict[str, int | None] = {}  # estimated video stream size per quality preset


class Word(BaseModel):
    i: int  # its place in Transcript.words
    text: str
    start: float
    end: float
    conf: float | None = None
    filler: bool = False  # an isolated "um", "uh", "erm", "er", "ah" or "hmm" (SPEC 7.3)
    pause_after: float = 0.0  # seconds of silence before the next word


class Sentence(BaseModel):
    id: str  # S1, S2, ... in order
    start: float
    end: float
    w0: int  # first and last word, both included
    w1: int
    text: str
    tags: list[str] = []
    chapter: str | None = None
    source: Literal["asr", "captions"] = "asr"  # "captions" when YouTube's caption words filled a gap here
    cont: bool = False  # it carries on the sentence before, so no clip starts on it or ends just before it


class Transcript(BaseModel):
    engine: str
    engine_version: str
    language: str | None
    duration: float
    words: list[Word]
    sentences: list[Sentence]

    def index(self, sid: str) -> int:
        """Where sentence ``sid`` sits in ``sentences``. KeyError when there is no such sentence."""
        if sid[:1] == "S" and sid[1:].isdigit():
            k = int(sid[1:]) - 1
            if 0 <= k < len(self.sentences) and self.sentences[k].id == sid:
                return k
        for k, sentence in enumerate(self.sentences):
            if sentence.id == sid:
                return k
        raise KeyError(sid)

    def sentence(self, sid: str) -> Sentence:
        return self.sentences[self.index(sid)]

    def text(self, i0: int, i1: int) -> str:
        """The text of sentences ``i0`` to ``i1``, both included."""
        return " ".join(s.text for s in self.sentences[i0 : i1 + 1])


class SentenceSignals(BaseModel):
    """The evidence about one sentence, before any LLM call (SPEC 7.4)."""

    energy_pct: float  # among the video's sentences, 0 to 1
    heat_pct: float | None  # the highest replay bin it touches, among all bins; None without a heatmap
    comments_n: int  # the most comments of a hot bucket it touches
    comments_w: float  # the weight of every comment bucket it touches
    chat_burst: bool
    events: list[str]  # "laughter", "applause", "music" within 2 s
    rate_wps: float
    tags: list[str] = []


class Signals(BaseModel):
    has_heatmap: bool
    has_comments: bool  # at least one hot bucket
    has_chat: bool
    has_events: bool
    rms: list[float]  # per second of audio
    heat_p80: float | None
    max_bucket_weight: float
    comment_buckets: list[
        tuple[float, float, float]
    ] = []  # (start, end, weight) of every bucket comments name
    sentences: dict[str, SentenceSignals]


class RangeSignals(BaseModel):
    """The evidence about one stretch of the video, such as a candidate clip."""

    heat_max_pct: float | None
    comments_weight: float
    chat_burst: bool
    energy_pct: float
    laughter: bool
    applause: bool


class Evidence(BaseModel):
    criterion: str  # C1 to C9
    quote: str  # the clip's own words behind a high score


class EmojiCue(BaseModel):
    sid: str  # the sentence it pops on
    word: str  # the spoken word it matches
    emoji: str


class FactCard(BaseModel):
    sid: str  # the sentence it explains
    text: str


class Packaging(BaseModel):
    """What goes on and under a clip (SPEC 8.2 to 8.5 and 9.2 step 4), from the LLM or from Basic mode."""

    hook_title: str
    hook_alts: list[str] = []
    accent_word: str | None = None  # a word of the hook title, as written there
    emphasis: list[str] = []
    emoji_cues: list[EmojiCue] = []
    fact_cards: list[FactCard] = []
    cold_open_sid: str | None = None
    mood: str = "calm"
    energy: int = 3  # 1 quiet to 5 intense
    tiktok_caption: str = ""
    tiktok_hashtags: list[str] = []
    instagram_caption: str = ""
    instagram_hashtags: list[str] = []
    keywords: list[str] = []
    credit: str | None = None  # "Source: <channel>" for the post caption
    mode: Literal["llm", "basic"] = "llm"


class ReviewResult(BaseModel):
    """What the review pass found about a clip as cut (SPEC 9.2 step 5). The numbers count the titles and
    cards the clip had when it was checked."""

    complete_story: bool
    meaning_changed: bool
    true_title_index: int  # the first title it supports: 0 the hook title, 1 on the alternatives, -1 none
    cards_supported: list[bool]  # one for each fact card
    tiktok_caption_true: bool = True  # whether the caption claims only what the clip shows
    instagram_caption_true: bool = True
    notes: str = ""


class Candidate(BaseModel):
    """A moment that might become a clip, from Discover or Basic mode through scoring (SPEC 9.2 to 9.4)."""

    id: str  # k1, k2, ... across the run
    start_sid: str
    end_sid: str
    start_s: float
    end_s: float
    scores: dict[str, int]  # C1 to C9, each 0 to 10
    evidence: list[Evidence] = []
    archetype: str = "other"
    red_flags: list[str] = []
    soft_flags: list[str] = []
    hook_titles: list[str] = []
    topic_tags: list[str] = []
    cold_open_sid: str | None = None
    reason: str = ""
    window: int = 0  # the Discover window it came from
    mode: Literal["llm", "basic"] = "llm"
    llm_score: float = 0.0  # 0 to 100
    rerank_score: float | None = None
    on_topic: bool | None = None  # about the topic the user asked for; None when none was asked, or unknown
    signal_score: float | None = None
    final_score: float | None = None
    passes_gates: bool = True
    tier: Literal["post", "test", "weak", "no"] | None = None
    notes: list[str] = []
    packaging: Packaging | None = None
    review: ReviewResult | None = None

    @property
    def duration(self) -> float:
        return self.end_s - self.start_s


# Vision (Phase 2). Positions are fractions of the displayed source frame, 0 to 1.


class FaceBox(BaseModel):
    t: float
    x: float
    y: float
    w: float
    h: float
    score: float
    marks: list[tuple[float, float]] = []  # right eye, left eye, nose tip, right and left mouth corners


class Track(BaseModel):
    """One face followed through one shot, on the analysis frame grid."""

    id: int
    shot: int
    times: list[float]
    boxes: list[tuple[float, float, float, float]]  # smoothed x, y, w, h
    eyes: list[tuple[float, float]]  # smoothed point between the eyes
    scores: list[float]  # detection score, 0 where a gap was bridged
    src: list[tuple[int, int] | None]  # (sample, face) behind each time, None where bridged
    mouth: list[float] = []  # mouth motion per time (vision.asd)


class SpeakerTurn(BaseModel):
    start_s: float
    end_s: float
    track: int


class Shot(BaseModel):
    start_s: float
    end_s: float
    kind: Literal["single", "two_shot", "group", "stream", "screen", "motion"]
    tracks: list[int]
    quality: float  # C8, 0 to 10


class Analysis(BaseModel):
    """clips/<clip_id>/analysis.json (SPEC 6.3)."""

    version: int
    key: str  # the source file, its size and the range: a different key means analyse again
    start_s: float
    end_s: float
    width: int  # the displayed source frame
    height: int
    fps: float  # the source's
    cuts: list[float]
    shots: list[Shot]
    tracks: list[Track]
    turns: list[SpeakerTurn]
    motion: list[tuple[float, float, float]]  # t, motion centre x, motion amount
    quality: float
    layout: str
    layout_reason: str


class RenderRow(BaseModel):
    """One rendered version of a clip, in ``clips/<clip_id>/v<version>/`` (SPEC 6.3)."""

    id: str
    clip_id: str
    version: int
    style_key: str  # the style the render was asked for (render.styles.style_key)
    path: str  # its clip.mp4
    cover_path: str | None = None
    thumb_path: str | None = None
    cover_s: float | None = None  # when the cover's frame shows, for Instagram's cover offset
    duration_s: float | None = None
    size_bytes: int | None = None
    edl_path: str | None = None
    qa: dict[str, Any] = {}  # render.qa.QAResult
    status: Literal["ready", "failed"] = "ready"  # failed: a hard problem blocks both apps
    edits_key: str | None = None  # the edits it was made from, to tell when it is out of date
    account_id: str | None = None  # the account it was made for (SPEC 11); None for the clip's own
    created_at: float = 0.0


class LibraryItem(BaseModel):
    """A file added to the library (SPEC 12), kept in ``library/<kind>/``."""

    id: str
    kind: str  # gameplay or music
    name: str
    path: str
    source_url: str | None = None
    credit: str | None = None
    license: str | None = None
    license_url: str | None = None
    notes: str | None = None
    duration_s: float | None = None
    width: int | None = None
    height: int | None = None
    tags: list[str] = []  # a track's moods
    credit_required: bool = False  # a track whose licence asks for credit in the caption
    energy: int | None = None  # a track's, 1 (calm) to 5 (all out)
    created_at: float = 0.0


class GameplayUse(BaseModel):
    """A segment of a gameplay item under a clip, and once posted, under a post to an account (SPEC 12)."""

    id: str
    item_id: str
    start_s: float
    end_s: float
    clip_id: str | None = None
    account_id: str | None = None
    post_id: str | None = None
    created_at: float = 0.0
