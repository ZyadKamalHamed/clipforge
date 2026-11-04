"""A clip's own edits (SPEC 14.4): what the clip editor may change about one clip, each change checked as
it is saved so a render made from it works, or says in one sentence naming the edit why it can't."""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from collections.abc import Collection
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, ValidationError

from clipforge.brain.package import CARD_CHARS
from clipforge.brain.text import title_word
from clipforge.brain.wording import INSTAGRAM_TAGS, POST_CHARS, TAG_CHARS, TITLE_CHARS, post_text, units
from clipforge.errors import ClipForgeError
from clipforge.models import ClipRow, FactCard, Packaging, ProjectOptions, Transcript
from clipforge.render.cards import MAX_CARDS
from clipforge.render.edl import EPS, clip_words, sentence_ids
from clipforge.render.styles import COLOUR, PresetName
from clipforge.untrusted import CLEAN

FIX_CHARS = 30  # a word fix is a word or two, not a sentence
MIN_S, MAX_S = 3.0, 180.0  # how long a clip may be
SNAP_S = 0.05  # a range edge this close to a word takes the whole word in
TAKE_FILE = r"^[0-9a-f]{10}\.wav$"  # a voiceover take, named by what is in it

# The edits by what they change: how the video looks, what is cut, the words on it, the post under it
LOOK_KEYS = (
    "preset",
    "accent",
    "punch_ins",
    "pause_trim",
    "filler_removal",
    "auto_pace",
    "emoji",
    "sfx",
    "credit_line",
    "hook_whole_clip",
    "encoder",
    "fps_cap_30",
    "gameplay_mode",
    "progress_bar",
    "cold_open",
    "layout",
    "gameplay_item",
    "gameplay_seed",
    "music",
    "music_db",
    "voiceover",
    "voiceover_at",
)
CUT_KEYS = ("struck", "orig_range")
TEXT_KEYS = ("hook_title", "accent_word", "fact_cards", "word_fixes")
COPY_KEYS = ("tiktok_caption", "tiktok_hashtags", "instagram_caption", "instagram_hashtags")
REVIEW_KEYS = ("struck", "hook_title", "fact_cards", *COPY_KEYS)  # what a re-check reads of the edits

UNKNOWN = "ClipForge doesn't have that edit."
KEPT_RANGE = "ClipForge keeps the clip's original range itself, so it can't be edited."
NO_ITEM = "That gameplay video isn't in the library."
NO_TRACK = "That track isn't in your music library."
NO_TAKE = "That voiceover recording isn't saved for this clip. Record it again."
TAKE_AT = "The voiceover starts between 0 s and the end of the clip."
NOT_IN_TRANSCRIPT = "That word isn't in the transcript."
NOT_IN_CLIP = "That word isn't in the clip."
NOT_IN_TITLE = "The accent word must be one of the hook title's words."
TOO_MANY_CARDS = f"A clip can have at most {MAX_CARDS} fact cards."
TAG_ONLY = "A hashtag can only have letters, numbers and underscores."
TAG_LETTER = "A hashtag needs at least one letter."
TAG_LONG = f"A hashtag can be at most {TAG_CHARS} characters."
IG_TAGS = f"Instagram allows at most {INSTAGRAM_TAGS} hashtags."  # its cap since 18 Dec 2025 (research 04)
POST_LONG = "The {app} post can be at most 2,200 characters, counting its hashtags and credit line."
RANGE_WRONG = "The clip's range must be a start and an end, in seconds."
INSIDE = "The clip must start and end inside the video."
BACKWARDS = "The clip's end must come after its start."
NO_WORD = "The clip needs at least one whole word."
TOO_SHORT = f"The clip must be at least {MIN_S:g} seconds long."
TOO_LONG = f"The clip can be at most {MAX_S:g} seconds long."

SWITCHES = {
    "punch_ins": "Punch-ins",
    "pause_trim": "Pause trimming",
    "filler_removal": "Filler word removal",
    "auto_pace": "Auto pace",
    "emoji": "Emoji",
    "sfx": "Sound effects",
    "credit_line": "The credit line",
    "hook_whole_clip": "Keeping the hook title up",
    "fps_cap_30": "The 30 fps cap",
    "progress_bar": "The progress bar",
    "cold_open": "The cold open",
}
# What an edit of the wrong kind is told, by the edit
WRONG = {
    "preset": "The caption style must be bold_pop, hormozi, beast, pill, clean, one_word or neon.",
    "accent": "The accent colour must look like #FFD60A.",
    "encoder": "The encoder must be fast or best.",
    "gameplay_mode": "Gameplay must be auto, off or always.",
    "layout": "The layout must be auto, face, motion, stacked, gameplay, stream or fit.",
    "gameplay_item": NO_ITEM,
    "gameplay_seed": "The gameplay seed must be a whole number of 0 or more.",
    "music": NO_TRACK,
    "music_db": "Music sits between 24 and 6 dB under the speech.",
    "voiceover": NO_TAKE,
    "voiceover_at": TAKE_AT,
    "struck": "Struck words must be a list of word numbers.",
    "hook_title": "The hook title must be text.",
    "accent_word": "The accent word must be text.",
    "fact_cards": "Fact cards must be a list of cards, each with a sentence and its text.",
    "word_fixes": "Word fixes must pair word numbers with their new text.",
    "tiktok_caption": "The TikTok caption must be text.",
    "tiktok_hashtags": "The TikTok hashtags must be a list of words.",
    "instagram_caption": "The Instagram caption must be text.",
    "instagram_hashtags": "The Instagram hashtags must be a list of words.",
} | {key: f"{label} must be on or off." for key, label in SWITCHES.items()}
APPS = (("tiktok", "TikTok"), ("instagram", "Instagram"))


class EditError(ClipForgeError):
    """An edit ClipForge can't use: ``field`` names it (``fact_cards.1`` for the second card) and the
    message says why in one sentence."""

    def __init__(self, field: str, message: str):
        super().__init__("invalid", message)
        self.field = field


class ClipEdits(BaseModel):
    """Every edit a clip may have, each one optional; the look uses Style's own names."""

    model_config = ConfigDict(extra="forbid")

    preset: PresetName | None = None
    accent: Annotated[StrictStr, Field(pattern=COLOUR)] | None = None
    punch_ins: StrictBool | None = None
    pause_trim: StrictBool | None = None
    filler_removal: StrictBool | None = None
    auto_pace: StrictBool | None = None
    emoji: StrictBool | None = None
    sfx: StrictBool | None = None
    credit_line: StrictBool | None = None
    hook_whole_clip: StrictBool | None = None
    encoder: Literal["fast", "best"] | None = None
    fps_cap_30: StrictBool | None = None
    gameplay_mode: Literal["auto", "off", "always"] | None = None
    progress_bar: StrictBool | None = None
    cold_open: StrictBool | None = None
    layout: Literal["auto", "face", "motion", "stacked", "gameplay", "stream", "fit"] | None = None
    gameplay_item: StrictStr | None = None
    gameplay_seed: Annotated[StrictInt, Field(ge=0)] | None = None
    music: StrictStr | None = None  # "auto" or a music item's id
    music_db: Annotated[float, Field(ge=-24, le=-6, strict=True, allow_inf_nan=False)] | None = None
    voiceover: Annotated[StrictStr, Field(pattern=TAKE_FILE)] | None = None  # a take in the clip's folder
    voiceover_at: Annotated[float, Field(ge=0, strict=True, allow_inf_nan=False)] | None = None  # seconds in
    struck: list[StrictInt] | None = None  # word numbers cut from the clip
    orig_range: tuple[float, float] | None = None  # where the clip was before its range first moved
    hook_title: StrictStr | None = None
    accent_word: StrictStr | None = None  # "" for none
    fact_cards: list[FactCard] | None = None
    word_fixes: dict[StrictStr, StrictStr] | None = None  # word number to the text its caption shows
    tiktok_caption: StrictStr | None = None
    tiktok_hashtags: list[StrictStr] | None = None
    instagram_caption: StrictStr | None = None
    instagram_hashtags: list[StrictStr] | None = None


def validate_edits(
    clip: ClipRow,
    transcript: Transcript,
    current: dict,
    patch: dict,
    *,
    library_ids: set[str],
    credit: str | None,
    music_ids: Collection[str] = (),
    takes: Collection[str] = (),
) -> dict:
    """``current`` with ``patch`` over it, each edit it sets checked against the clip as it is now and
    tidied; a None value removes an edit. ``credit`` is the credit line the posts carry, ``music_ids`` the
    tracks a clip may pick and ``takes`` the voiceover recordings saved for it. Raises EditError for the
    first edit that can't be used, leaving ``current`` as it was."""
    out = dict(current)
    for key, value in patch.items():
        if key == "orig_range":
            raise EditError(key, KEPT_RANGE)
        if key not in ClipEdits.model_fields:
            raise EditError(str(key), UNKNOWN)
        if value is None:
            out.pop(key, None)
            continue
        try:
            ClipEdits.model_validate({key: value})
        except ValidationError:
            raise EditError(key, WRONG[key]) from None
        out[key] = value
    given = {key: value for key, value in patch.items() if value is not None}
    words = clip_words(transcript, clip)
    pack = Packaging.model_validate(clip.packaging) if clip.packaging else Packaging(hook_title="")

    if "accent" in given:
        out["accent"] = given["accent"].upper()
    if "gameplay_item" in given and given["gameplay_item"] not in library_ids:
        raise EditError("gameplay_item", NO_ITEM)
    if "music" in given and given["music"] != "auto" and given["music"] not in music_ids:
        raise EditError("music", NO_TRACK)
    if "voiceover" in given and given["voiceover"] not in takes:
        raise EditError("voiceover", NO_TAKE)
    if given.get("voiceover_at", 0) > clip.end_s - clip.start_s:
        raise EditError("voiceover_at", TAKE_AT)
    if "struck" in given:
        struck = sorted(set(given["struck"]))
        if any(not 0 <= i < len(transcript.words) for i in struck):
            raise EditError("struck", NOT_IN_TRANSCRIPT)
        _keep(out, "struck", struck)
    if "hook_title" in given:
        out["hook_title"] = _line(given["hook_title"], "hook_title", TITLE_CHARS, "The hook title")
    title = out.get("hook_title", pack.hook_title)
    if "accent_word" in given:
        word = given["accent_word"]
        found = title_word(word, title) if word.strip() else ""
        if found is None:
            raise EditError("accent_word", NOT_IN_TITLE)
        out["accent_word"] = found
    elif "hook_title" in patch and out.get("accent_word") and title_word(out["accent_word"], title) is None:
        del out["accent_word"]  # a new title drops an accent word it doesn't have
    if "fact_cards" in given:
        out["fact_cards"] = _cards(given["fact_cards"], set(sentence_ids(transcript, words).values()))
    if "word_fixes" in given:
        _keep(out, "word_fixes", _fixes(given["word_fixes"], words))
    for app, name in APPS:
        caption, tags = f"{app}_caption", f"{app}_hashtags"
        if caption in given:
            out[caption] = _caption(given[caption])
        if tags in given:
            out[tags] = _hashtags(given[tags], tags)
            if app == "instagram" and len(out[tags]) > INSTAGRAM_TAGS:
                raise EditError(tags, IG_TAGS)
        if caption in given or tags in given:
            text = post_text(
                out.get(caption, getattr(pack, caption)), out.get(tags, getattr(pack, tags)), credit
            )
            if units(text) > POST_CHARS:
                raise EditError(caption if caption in given else tags, POST_LONG.format(app=name))
    return out


def apply_range(
    clip: ClipRow, transcript: Transcript, new: tuple[float, float] | None, *, duration_s: float
) -> tuple[dict, dict]:
    """The clip moved to ``new``, snapped out to whole words, or back where it started for None: the row's
    new start, end and sentence ids, and the edits to merge, with ``orig_range`` kept from the first move
    (None once the clip is back). Raises EditError("range", ...) for a range ClipForge can't cut."""
    orig = (clip.edits or {}).get("orig_range")
    if new is None:
        if not orig:
            return {}, {}
        start, end = float(orig[0]), float(orig[1])
    else:
        start, end = _snapped(transcript, new, duration_s)
    first = orig or [clip.start_s, clip.end_s]
    back = abs(start - first[0]) <= EPS and abs(end - first[1]) <= EPS
    moved = clip.model_copy(update={"start_s": start, "end_s": end})
    sids = sentence_ids(transcript, clip_words(transcript, moved))
    fields = {"start_s": start, "end_s": end, "sentence_ids": list(dict.fromkeys(sids.values()))}
    return fields, {"orig_range": None if back else [first[0], first[1]]}


def apply_packaging_edits(p: Packaging, edits: dict) -> Packaging:
    """The packaging with the clip's own hook title, accent word, fact cards and post copy over it. The
    accent word is a word of the title the clip ends up with, or there is none."""
    changes = {
        key: edits[key] for key in ("hook_title", "fact_cards", *COPY_KEYS) if edits.get(key) is not None
    }
    word = edits.get("accent_word")
    if not changes and word is None:
        return p
    merged = Packaging.model_validate(p.model_dump() | changes)
    accent = title_word(p.accent_word if word is None else word, merged.hook_title)
    return merged.model_copy(update={"accent_word": accent})


def effective_edits(options: ProjectOptions, edits: dict | None) -> dict:
    """The clip's own edits over its project's layout and caption style, which every clip of the project
    takes unless it has its own; a clip's "auto" layout is ClipForge's pick whatever the project says."""
    base: dict[str, Any] = {}
    if options.layout != "auto":
        base["layout"] = options.layout
    if options.caption_preset:
        base["preset"] = options.caption_preset
    merged = base | (edits or {})
    if merged.get("layout") == "auto":
        del merged["layout"]
    return merged


def render_key(clip: ClipRow, edits: dict) -> str:
    """What the clip's video is made from as 10 hex characters: its range and its edits, but not the post
    copy (which isn't in the video) or the range it started from."""
    made = {k: v for k, v in edits.items() if k not in COPY_KEYS and k != "orig_range" and v is not None}
    blob = json.dumps(
        {"range": [clip.start_s, clip.end_s], "edits": made}, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:10]


def review_key(clip: ClipRow, edits: dict) -> str:
    """What a re-check of the clip reads (SPEC 14.4) as 10 hex characters: its range, the words struck from
    it, its hook title, its fact cards and its post copy."""
    read = {k: edits[k] for k in REVIEW_KEYS if edits.get(k) is not None}
    blob = json.dumps(
        {"range": [clip.start_s, clip.end_s], "edits": read}, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:10]


def changes_review(edits: dict) -> bool:
    """Whether the edits change what the review read: a moved range, struck words, or a title, fact cards
    or post copy of the user's own."""
    return any(edits.get(k) is not None for k in ("orig_range", *REVIEW_KEYS))


# Small helpers


def _keep(out: dict, key: str, value: Any) -> None:
    """``value`` as the edit, or no edit when it is empty."""
    if value:
        out[key] = value
    else:
        out.pop(key, None)


def _single(text: str) -> str:
    """The text with its control characters, line breaks and runs of spaces made single spaces."""
    return " ".join(text.translate(CLEAN).split())


def _sized(text: str, field: str, limit: int, what: str) -> str:
    if not text:
        raise EditError(field, f"{what} can't be empty.")
    if len(text) > limit:
        raise EditError(field, f"{what} can be at most {limit} characters.")
    return text


def _line(text: str, field: str, limit: int, what: str) -> str:
    """Text that must stay on one line, tidied."""
    if len(text.strip().splitlines()) > 1:
        raise EditError(field, f"{what} can't have line breaks.")
    return _sized(_single(text), field, limit, what)


def _cards(cards: list[Any], said: set[str]) -> list[dict]:
    if len(cards) > MAX_CARDS:
        raise EditError("fact_cards", TOO_MANY_CARDS)
    out: list[dict] = []
    for n, raw in enumerate(cards):
        card = FactCard.model_validate(raw)
        field, what = f"fact_cards.{n}", f"Fact card {n + 1}"
        text = _sized(_single(card.text), field, CARD_CHARS, what)
        if card.sid not in said:
            raise EditError(field, f"{what}'s sentence isn't in the clip.")
        out.append({"sid": card.sid, "text": text})
    return out


def _fixes(fixes: dict[str, str], words: list) -> dict[str, str]:
    """The fixes for words inside the clip, tidied; one that changes nothing is left out."""
    by_number = {str(w.i): w for w in words}
    out: dict[str, str] = {}
    for key, text in fixes.items():
        field = f"word_fixes.{key}"
        word = by_number.get(key)
        if word is None:
            raise EditError(field, NOT_IN_CLIP)
        text = _line(text, field, FIX_CHARS, "A word fix")
        if text != word.text:
            out[key] = text
    return out


def _caption(text: str) -> str:
    """The caption with its line breaks kept, its other control characters made spaces and the spaces at
    the ends of its lines taken off."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.translate(CLEAN).rstrip() for line in lines).strip()


def _hashtags(tags: list[str], field: str) -> list[str]:
    """Each tag as ``#`` and the tag's letters, numbers and underscores (its spaces taken out), repeats
    left out whatever their case. Empty tags are skipped; a tag with anything else is refused."""
    out: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        word = "".join(unicodedata.normalize("NFKC", tag).split()).lstrip("#")
        if not word:
            continue
        if any(ch != "_" and unicodedata.category(ch)[0] not in "LNM" for ch in word):
            raise EditError(field, TAG_ONLY)
        if not any(ch.isalpha() for ch in word):
            raise EditError(field, TAG_LETTER)
        if len(word) > TAG_CHARS:
            raise EditError(field, TAG_LONG)
        if word.casefold() not in seen:
            seen.add(word.casefold())
            out.append(f"#{word}")
    return out


def _snapped(transcript: Transcript, new: Any, duration_s: float) -> tuple[float, float]:
    """``new`` snapped out to whole words: from the first word starting at or after its start, to the last
    word ending at or before its end."""
    try:
        start, end = new
    except (TypeError, ValueError):
        raise EditError("range", RANGE_WRONG) from None
    if any(isinstance(v, bool) or not isinstance(v, int | float) for v in (start, end)):
        raise EditError("range", RANGE_WRONG)
    if not (math.isfinite(start) and math.isfinite(end)) or start < -EPS or end > duration_s + EPS:
        raise EditError("range", INSIDE)
    if end <= start:
        raise EditError("range", BACKWARDS)
    first = next((w for w in transcript.words if w.start >= start - SNAP_S - EPS), None)
    last = next((w for w in reversed(transcript.words) if w.end <= end + SNAP_S + EPS), None)
    if first is None or last is None or last.i < first.i:
        raise EditError("range", NO_WORD)
    if last.end - first.start < MIN_S - EPS:
        raise EditError("range", TOO_SHORT)
    if last.end - first.start > MAX_S + EPS:
        raise EditError("range", TOO_LONG)
    return first.start, last.end
