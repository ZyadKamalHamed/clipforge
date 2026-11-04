"""The answers the brain asks the LLM for (SPEC 9.2, 9.3). Types and enums only: counts, lengths and
ranges are applied by the pass that reads the answer, so an answer that breaks one is trimmed rather than
refused (the schema module says why). The defaults keep a reply that leaves out a list readable; the
schema sent still asks for every key."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Archetype = Literal[
    "stakes_numbers",
    "contrarian",
    "confession",
    "conflict",
    "story",
    "viewer_question",
    "insider_reveal",
    "prediction_warning",
    "reaction",
    "rule_framework",
    "funny",
    "other",
]
RedFlag = Literal[
    "meaning_distortion",
    "no_faithful_hook",
    "missing_context",
    "defamation",
    "harmful_misinformation",
    "unsafe_content",
    "third_party_media",
    "private_info",
    "political_campaign",
]
SoftFlag = Literal["sponsor_read", "crosstalk", "early_profanity", "long_monologue", "jargon"]
Criterion = Literal["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C9"]  # C8 is measured from the frames
Mood = Literal["hype", "funny", "inspiring", "serious", "emotional", "calm", "dark", "curious"]


class Scores(BaseModel):
    C1: int
    C2: int
    C3: int
    C4: int
    C5: int
    C6: int
    C7: int
    C9: int


class EvidenceOut(BaseModel):
    criterion: Criterion
    quote: str


class DiscoverItem(BaseModel):
    start_sid: str
    end_sid: str
    scores: Scores
    evidence: list[EvidenceOut] = []
    archetype: Archetype = "other"
    red_flags: list[RedFlag] = []
    soft_flags: list[SoftFlag] = []
    hook_titles: list[str] = []
    topic_tags: list[str] = []
    cold_open_sid: str | None = None
    reason: str = ""


class DiscoverOut(BaseModel):
    candidates: list[DiscoverItem]


class RerankItem(BaseModel):
    id: str
    score: int
    why: str = ""
    on_topic: bool = True


class RerankOut(BaseModel):
    ranking: list[RerankItem]


class EmojiOut(BaseModel):
    sid: str
    word: str
    emoji: str


class CardOut(BaseModel):
    sid: str
    text: str


class PackageItem(BaseModel):
    id: str
    start_sid: str
    end_sid: str
    hook_title: str
    hook_alts: list[str] = []
    accent_word: str | None = None
    emphasis: list[str] = []
    emoji_cues: list[EmojiOut] = []
    fact_cards: list[CardOut] = []
    cold_open_sid: str | None = None
    mood: Mood = "calm"
    energy: int = 3
    tiktok_caption: str = ""
    tiktok_hashtags: list[str] = []
    instagram_caption: str = ""
    instagram_hashtags: list[str] = []
    keywords: list[str] = []


class PackageOut(BaseModel):
    clips: list[PackageItem]


class ReviewItem(BaseModel):
    id: str
    notes: str = ""  # asked for first, so the verdicts follow from it
    complete_story: bool
    meaning_changed: bool
    true_title_index: int
    cards_supported: list[bool] = []
    tiktok_caption_true: bool = True
    instagram_caption_true: bool = True


class ReviewOut(BaseModel):
    clips: list[ReviewItem]
