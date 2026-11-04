"""Gameplay under the speaker (SPEC 7.7 and 12): video files added with ``clipforge library add``, copied
into ``library/gameplay/``. Gameplay audio is always muted. Each use records the segment and the clip (and,
from Phase 4, the account and post), and a new use prefers the item used least and a segment no other use
overlaps, so no segment repeats while an unused one is left."""

from __future__ import annotations

import os
import random
import shutil
import sqlite3
from collections.abc import Collection
from pathlib import Path

from clipforge import repo
from clipforge.errors import ClipForgeError, ToolError
from clipforge.media.ffmpeg import probe
from clipforge.models import GameplayUse, LibraryItem
from clipforge.paths import Paths

EPS = 1e-6
NOT_VIDEO = "That file has no video in it, so it can't be gameplay."
UNREADABLE = "That file isn't a video ClipForge can read."


def usable(conn: sqlite3.Connection) -> list[LibraryItem]:
    """The gameplay items whose file is still there, oldest first."""
    return [item for item in repo.list_library(conn, "gameplay") if Path(item.path).is_file()]


def counted(conn: sqlite3.Connection, item_id: str) -> list[GameplayUse]:
    """The uses of ``item_id`` that count: a clip's or a post's, not one whose clip was removed."""
    return [u for u in repo.gameplay_uses(conn, item_id) if u.clip_id is not None or u.post_id is not None]


def clips_using(conn: sqlite3.Connection, item_id: str) -> int:
    """How many clips show ``item_id``: a clip counts once, however many of its versions and posts do."""
    return len({u.clip_id or u.post_id for u in counted(conn, item_id)})


def pick_item(
    conn: sqlite3.Connection, clip_id: str, *, rng: random.Random, wanted: str | None = None
) -> LibraryItem | None:
    """The gameplay for ``clip_id``: ``wanted`` (the item picked for the clip) while its file is there, else
    the item it already shows while that file is there, or else the item used least (``rng`` picks among
    equals); None when the library has none."""
    items = usable(conn)
    for item in items:
        if item.id == wanted:
            return item
    own = repo.gameplay_use_of(conn, clip_id)
    for item in items:
        if own is not None and item.id == own.item_id:
            return item
    if not items:
        return None
    uses = {item.id: len(counted(conn, item.id)) for item in items}
    least = min(uses.values())
    return rng.choice([item for item in items if uses[item.id] == least])


def pick_segment(
    conn: sqlite3.Connection, item: LibraryItem, length_s: float, *, rng: random.Random
) -> float:
    """Where in ``item`` a clip ``length_s`` long starts: somewhere no counted use overlaps (``rng`` picks
    evenly along the free stretches), or else where the uses overlap it least; 0 when the item is no longer
    than the clip or its length is unknown."""
    room = (item.duration_s or 0.0) - length_s
    if room <= EPS:
        return 0.0
    uses = [(u.start_s, u.end_s) for u in counted(conn, item.id)]
    free = _free(room, [(a - length_s, e) for a, e in uses])
    total = sum(b - a for a, b in free)
    if total > EPS:
        x = rng.uniform(0.0, total)
        for a, b in free:
            if x <= b - a or (a, b) == free[-1]:
                return min(max(round(a + x, 3), a), b)
            x -= b - a
    if free:  # only single moments overlap nothing
        return rng.choice([a for a, _ in free])

    def overlap(s: float) -> float:
        return sum(max(0.0, min(s + length_s, e) - max(s, a)) for a, e in uses)

    edges = {min(max(t, 0.0), room) for a, e in uses for t in (a - length_s, a, e - length_s, e)}
    starts = sorted(edges | {0.0, room})
    least = min(overlap(s) for s in starts)
    return rng.choice([s for s in starts if overlap(s) <= least + EPS])


def segment_for(
    conn: sqlite3.Connection,
    clip_id: str,
    item: LibraryItem,
    length_s: float,
    *,
    rng: random.Random,
    account_id: str | None = None,
) -> float:
    """Where the clip's gameplay starts (with ``account_id``, in that account's version of it): where it
    started last time while that still fits, so a re-render keeps its segment, or else a new pick."""
    own = repo.gameplay_use_of(conn, clip_id, account_id)
    if (
        own is not None
        and own.item_id == item.id
        and own.start_s + length_s <= (item.duration_s or 0.0) + EPS
    ):
        return own.start_s
    return pick_segment(conn, item, length_s, rng=rng)


def add_gameplay(
    conn: sqlite3.Connection,
    paths: Paths,
    file: Path | str,
    *,
    credit: str,
    license: str,
    license_url: str | None = None,
    notes: str | None = None,
    name: str | None = None,
    move: bool = False,
    tags: Collection[str] = (),
) -> LibraryItem:
    """Copy ``file`` into ``library/gameplay/`` and add it to the library, named ``name`` or after the file
    and with ``tags`` (a starter video's ``starter:<id>``). The original stays where it is, unless ``move``
    (an upload, or a download) moves it in."""
    src = Path(file).expanduser()
    if not src.is_file():
        raise ClipForgeError("no_file", f"There's no file at {file}.")
    try:
        info = probe(src)
    except ToolError as err:
        raise ClipForgeError("not_video", UNREADABLE, details=err.details) from err
    if not info.has_video:
        raise ClipForgeError("not_video", NOT_VIDEO)
    item_id = repo.new_id("l")
    dest = keep(src, paths.gameplay / f"{item_id}{src.suffix.lower()}", move=move)
    item = LibraryItem(
        id=item_id,
        kind="gameplay",
        name=name or src.stem,
        path=str(dest),
        credit=credit,
        license=license,
        license_url=license_url,
        notes=notes,
        duration_s=info.duration_s,
        width=info.width,
        height=info.height,
        tags=list(tags),
    )
    try:
        return repo.add_library_item(conn, item)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise


def keep(src: Path, dest: Path, *, move: bool) -> Path:
    """``src`` copied to ``dest`` (made whole under another name first), or moved there with ``move``."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    try:
        if move:
            try:
                os.replace(src, dest)
            except OSError:  # another disk
                shutil.copyfile(src, tmp)
                os.replace(tmp, dest)
                src.unlink(missing_ok=True)
        else:
            shutil.copyfile(src, tmp)
            os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    return dest


def _free(room: float, blocked: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """The stretches of [0, room] outside every open interval in ``blocked``, in order (a stretch can be a
    single moment)."""
    free, at = [], 0.0
    for a, b in sorted(blocked):
        if at > room:
            break
        if b <= at:
            continue
        if a >= at:
            free.append((at, min(a, room)))
        at = b
    if at <= room:
        free.append((at, room))
    return free
