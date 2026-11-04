"""How a clip looks (SPEC 8): the caption preset, colours, overlays and edit choices, from the settings
and the clip's own edits, and for an account's posts its style kit over them (SPEC 11)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator

from clipforge.config import Settings

PresetName = Literal["bold_pop", "hormozi", "beast", "pill", "clean", "one_word", "neon"]
# The keys of assets.FONTS
FontName = Literal[
    "montserrat-black",
    "montserrat-extrabold",
    "montserrat-bold",
    "poppins-extrabold",
    "anton",
    "bangers",
    "inter-extrabold",
    "bebas-neue",
    "archivo-black",
    "luckiest-guy",
]
GameplayMode = Literal["auto", "off", "always"]
COLOUR = r"^#[0-9A-Fa-f]{6}$"
CHANNEL = "{channel}"  # where a credit format puts the channel's name
CREDIT_MAX = 60
Colour = Annotated[str, Field(pattern=COLOUR)]
# Settings a clip's look copies as they are; the preset and accent are worked out on their own
COPIED = (
    "gameplay_mode",
    "punch_ins",
    "pause_trim",
    "filler_removal",
    "auto_pace",
    "emoji",
    "sfx",
    "encoder",
    "fps_cap_30",
    "hook_whole_clip",
    "credit_line",
    "progress_bar",
    "cold_open",
)


@dataclass(frozen=True)
class CaptionPreset:
    font: str  # a key of assets.FONTS
    size: int = 72
    fill: str = "#FFFFFF"
    stroke: int = 7  # black, in px at ``size``
    accent: str = "#FFD60A"  # the preset's own active word colour
    capitals: bool = False
    box: bool = False  # a box in the accent behind the active word
    one_word: bool = False
    glow: bool = False  # a soft glow in the accent around the active word


PRESETS: dict[str, CaptionPreset] = {
    "bold_pop": CaptionPreset("montserrat-black"),
    "hormozi": CaptionPreset("montserrat-black", accent="#3CFF5A", capitals=True),
    "beast": CaptionPreset("bangers", size=84, stroke=10, capitals=True),
    "pill": CaptionPreset("poppins-extrabold", box=True),
    "clean": CaptionPreset("inter-extrabold", stroke=0),
    "one_word": CaptionPreset("anton", size=110, stroke=10, capitals=True, one_word=True),
    "neon": CaptionPreset("montserrat-black", accent="#00E5FF", glow=True),
}


class Style(BaseModel):
    preset: PresetName = "bold_pop"
    accent: str = Field("#FFD60A", pattern=COLOUR)
    hook_fill: str = Field("#FFFFFF", pattern=COLOUR)
    hook_text: str = Field("#000000", pattern=COLOUR)
    credit_format: str = "Source: {channel}"
    gameplay_mode: GameplayMode = "auto"
    font: FontName | None = None  # the captions' font, when not the preset's own
    punch_ins: bool = True
    pause_trim: bool = True
    filler_removal: bool = True
    auto_pace: bool = False
    emoji: bool = True
    sfx: bool = True
    encoder: Literal["fast", "best"] = "fast"
    fps_cap_30: bool = False
    hook_whole_clip: bool = False
    credit_line: bool = True
    progress_bar: bool = False  # a bar along the bottom of the picture as the clip plays (SPEC 8.7)
    cold_open: bool = False  # open on the packaging's strongest line, then a whoosh into the start (SPEC 8.3)
    hook: bool = True  # the hook title; off only when a campaign bans it (SPEC 10.10)
    cards: bool = True  # the fact cards, the same
    music: str | None = None  # a track under the speech: "auto", a music item's id, or None for none
    music_db: float = -12.0  # how far under the speech's loudness the track sits

    @field_validator("accent", "hook_fill", "hook_text")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.upper()


class StyleKit(BaseModel):
    """An account's look. Each field it sets wins over the clip's own for that account's posts; one left
    None keeps the clip's."""

    preset: PresetName | None = None
    font: FontName | None = None
    accent: Colour | None = None
    hook_fill: Colour | None = None  # the hook title's box
    hook_text: Colour | None = None  # the hook title's words
    credit_format: Annotated[str, Field(max_length=CREDIT_MAX)] | None = None
    gameplay_mode: GameplayMode | None = None

    @field_validator("accent", "hook_fill", "hook_text")
    @classmethod
    def _upper(cls, value: str | None) -> str | None:
        return value.upper() if value else value

    @field_validator("credit_format")
    @classmethod
    def _channel(cls, value: str | None) -> str | None:
        if value is not None and CHANNEL not in value:
            raise ValueError(f"a credit format holds {CHANNEL}")
        return value

    @property
    def empty(self) -> bool:
        """Whether it sets nothing, so the account's posts wear the clip's own look."""
        return not self.model_dump(exclude_none=True)


def style_for(settings: Settings, edits: dict, kit: StyleKit | None = None) -> Style:
    """The settings, then the clip's own edits, which use Style's field names (a None edit is not set),
    then the account's ``kit``. The accent is the kit's or the clip's, else the settings', else the
    preset's own."""
    mine = {k: v for k, v in (edits or {}).items() if k in Style.model_fields and v is not None}
    if kit is not None:
        mine |= kit.model_dump(exclude_none=True)
    fields: dict[str, Any] = {k: getattr(settings, k) for k in COPIED} | {"preset": settings.caption_preset}
    fields |= mine
    preset = PRESETS.get(fields["preset"])
    fields["accent"] = mine.get("accent") or settings.accent_color or (preset or PRESETS["bold_pop"]).accent
    return Style.model_validate(fields)


def caption_font(style: Style) -> str:
    """The captions' font: the style's own, else its preset's."""
    return style.font or PRESETS[style.preset].font


def style_key(style: Style) -> str:
    """The first 10 hex characters of the SHA-256 of the style's JSON, which leaves out a font that isn't
    set, so a look without one keeps the key its renders had before fonts could be chosen."""
    unset = {"font"} if style.font is None else None
    return hashlib.sha256(style.model_dump_json(exclude=unset).encode()).hexdigest()[:10]
