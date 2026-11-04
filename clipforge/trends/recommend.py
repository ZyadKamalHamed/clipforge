"""Sound suggestions for a clip (SPEC 13): your tracks and the TikTok sounds you shortlisted, by how well
their moods and energy match the clip's packaging, and an Instagram account's trending list by its place in
that list, since Instagram gives no mood. ``best_music`` is the one track "auto" music picks for a clip."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

from clipforge.brain.schemas import Mood
from clipforge.library.music import music_items
from clipforge.models import ClipRow, LibraryItem
from clipforge.trends import store

if TYPE_CHECKING:
    from clipforge.app import Engine

# The moods next to each one: a track in one of them is close to a clip's mood.
NEAR: dict[Mood, tuple[Mood, ...]] = {
    "hype": ("funny", "inspiring"),
    "funny": ("hype", "curious"),
    "inspiring": ("emotional", "hype"),
    "serious": ("dark", "curious"),
    "emotional": ("inspiring", "calm"),
    "calm": ("emotional", "curious"),
    "dark": ("serious",),
    "curious": ("serious", "funny", "calm"),
}
CLOSE_ENERGY = 1  # an energy this near the clip's suits it on its own, with no mood in common
FRESH_S = 86400.0  # a trending list read longer ago than this says so
TRENDING = "Trending on Instagram today"
TRENDING_OLD = "Trending on Instagram when last checked"


class Suggestion(BaseModel):
    """A track or sound suggested for a clip, with why and how well it suits it (``score``, highest first)."""

    kind: Literal["music", "shortlist", "ig_trending"]
    id: str
    title: str
    artist: str | None
    why: str
    stale: bool
    score: float


def _mood_points(mood: str | None, moods: list[str]) -> int:
    if mood is None:
        return 0
    if mood in moods:
        return 2
    return 1 if any(m in moods for m in NEAR.get(mood, ())) else 0


def match(mood: str | None, energy: int | None, moods: list[str], item_energy: int | None) -> float:
    """2 for the clip's mood among ``moods``, 1 for a mood near it, plus up to 1 for how near the two
    energies are, when both are known."""
    points = float(_mood_points(mood, moods))
    if energy is not None and item_energy is not None:
        points += (4 - abs(energy - item_energy)) / 4
    return points


def _why(mood: str | None, energy: int | None, moods: list[str], item_energy: int | None) -> str | None:
    """Why a track or sound suits the clip, or None when it doesn't: its mood is the clip's or near it, or,
    failing that, its energy is within ``CLOSE_ENERGY`` of the clip's."""
    points = _mood_points(mood, moods)
    if points == 2:
        return f"Matches its {mood} mood"
    if points == 1:
        return f"Close to its {mood} mood"
    if energy is not None and item_energy is not None and abs(energy - item_energy) <= CLOSE_ENERGY:
        return "Its energy is close"
    return None


def feel(clip: ClipRow) -> tuple[str | None, int | None]:
    """The clip's mood and energy from its packaging, None for each it doesn't have."""
    packaging: dict[str, Any] = clip.packaging or {}
    mood, energy = packaging.get("mood"), packaging.get("energy")
    return (
        mood if isinstance(mood, str) else None,
        energy if isinstance(energy, int) and not isinstance(energy, bool) else None,
    )


def suggestions(
    engine: Engine,
    clip: ClipRow,
    *,
    platform: str | None = None,
    account_id: str | None = None,
    limit: int = 5,
) -> list[Suggestion]:
    """Up to ``limit`` tracks and up to ``limit`` sounds for the clip, best first: your tracks always, your
    TikTok shortlist for TikTok (or no platform), and the account's trending list for Instagram, after the
    mood matches by its place in the list. Each kind has its own places, since a picker shows one kind. A
    stale sound goes below a fresh one at the same score; ties go by title."""
    conn = engine.db()
    now = engine.now().timestamp()  # the clock the lists were read by
    mood, energy = feel(clip)
    found: list[tuple[bool, Suggestion]] = []  # whether it matched on mood, and the suggestion

    def consider(
        kind: Any,
        sid: str,
        title: str,
        artist: str | None,
        moods: list[str],
        item_energy: int | None,
        stale: bool,
    ) -> None:
        why = _why(mood, energy, moods, item_energy)
        if why is not None:
            score = match(mood, energy, moods, item_energy)
            suggestion = Suggestion(
                kind=kind, id=sid, title=title, artist=artist, why=why, stale=stale, score=score
            )
            found.append((_mood_points(mood, moods) > 0, suggestion))

    for item in music_items(conn):
        consider("music", item.id, item.name, item.credit, item.tags, item.energy, False)
    if platform in (None, "tiktok"):
        for sound in store.sounds(conn, source="shortlist"):
            consider(
                "shortlist",
                sound.id,
                sound.title,
                sound.artist,
                sound.mood_tags,
                sound.energy,
                sound.stale(now),
            )
    if platform == "instagram" and account_id:
        for sound in store.sounds(conn, source="ig_trending", account_id=account_id):
            read = sound.fetched_at if sound.fetched_at is not None else sound.created_at
            suggestion = Suggestion(
                kind="ig_trending",
                id=sound.id,
                title=sound.title,
                artist=sound.artist,
                why=TRENDING if now - read <= FRESH_S else TRENDING_OLD,
                stale=sound.stale(now),
                score=1 / (1 + sound.rank) if sound.rank is not None else 0.0,
            )
            found.append((False, suggestion))
    found.sort(key=lambda f: (not f[0], -f[1].score, f[1].stale, f[1].title.casefold(), f[1].id))
    taken = {"music": 0, "sound": 0}
    kept = []
    for _, suggestion in found:
        kind = "music" if suggestion.kind == "music" else "sound"
        taken[kind] += 1
        if taken[kind] <= limit:
            kept.append(suggestion)
    return kept


def best_music(items: list[LibraryItem], mood: str | None, energy: int | None) -> LibraryItem | None:
    """The track among ``items`` that suits a clip of ``mood`` and ``energy`` best (ties by title, then id),
    or None when none suits it."""
    suits = [item for item in items if _why(mood, energy, item.tags, item.energy) is not None]
    if not suits:
        return None
    return min(suits, key=lambda i: (-match(mood, energy, i.tags, i.energy), i.name.casefold(), i.id))
