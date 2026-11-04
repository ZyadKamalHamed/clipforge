"""The gameplay starter list (SPEC 12, research 05): nine vertical gameplay videos whose uploaders say they
are free to use, each with its credit line, licence and terms. Nothing downloads until Zyad presses
Download. A download is a ``library.starter`` job that saves the video in a folder of its own under the
library (the first 20 minutes of a longer one), then adds it as gameplay tagged ``starter:<id>``; a failure
removes that folder, so the entry is offered again. Minecraft items are for personal channels only
(Mojang's usage guidelines), and SYBO's own upload carries only informal permission."""

from __future__ import annotations

import json
import shutil
import tempfile
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from clipforge import repo
from clipforge.errors import ClipForgeError
from clipforge.jobs.stages.ingest import free_gb
from clipforge.library import gameplay
from clipforge.models import LibraryItem

if TYPE_CHECKING:
    from clipforge.app import Engine
    from clipforge.jobs.runner import JobContext

KIND = "library.starter"
TAG = "starter:"
PACK = Path(__file__).parent / "starter_pack.json"
STARTER_MAX_S = 1200.0  # a longer video downloads only its first 20 minutes
NOTE = (
    "These uploaders say their videos are free to use, but YouTube's terms don't allow downloading even"
    " then. Your own recordings, or files a creator sends you, are the cleanest choice."
)
PERSONAL_ONLY = "Personal channels only, not for brand or agency work (Mojang's usage guidelines)"
INFORMAL = "Informal permission only: keep it off paid or brand work"
FIRST_PART = "Downloads its first 20 minutes."
NO_VIDEO = "The download finished without a video, so try again."
NOT_LISTED = "That isn't on the starter list."
LOW_DISK = "Free up some disk space: ClipForge needs at least {gb:g} GB free to download gameplay."


class StarterEntry(BaseModel):
    id: str
    game: str
    title: str
    uploader: str
    video_id: str
    duration_s: float
    size: str  # width x height
    licence: str
    credit: str
    terms: str
    personal_only: bool  # Minecraft: not for brand or agency work
    informal: bool  # permission only in a video's description


@cache
def entries() -> list[StarterEntry]:
    """The list, in the order it shows."""
    return [StarterEntry.model_validate(raw) for raw in json.loads(PACK.read_text(encoding="utf-8"))]


def entry_of(entry_id: str) -> StarterEntry | None:
    return next((e for e in entries() if e.id == entry_id), None)


def url_of(entry: StarterEntry) -> str:
    return f"https://www.youtube.com/watch?v={entry.video_id}"


def chips(entry: StarterEntry) -> list[str]:
    return [PERSONAL_ONLY] * entry.personal_only + [INFORMAL] * entry.informal


def added(engine: Engine, entry_id: str) -> LibraryItem | None:
    """The library's copy of the entry, while there is one."""
    tag = TAG + entry_id
    return next((i for i in repo.list_library(engine.db(), "gameplay") if tag in i.tags), None)


def state_of(engine: Engine, entry: StarterEntry) -> dict[str, Any]:
    """``added`` while the library has it, ``downloading`` while its job is queued or running, ``failed``
    with the sentence when its last job failed, or else ``offered``."""
    item = added(engine, entry.id)
    if item is not None:
        return {"state": "added", "item_id": item.id, "error": None}
    jobs = [j for j in engine.runner.jobs_for(entry.id) if j.kind == KIND]
    last = jobs[-1] if jobs else None
    if last is not None and last.status in ("queued", "running"):
        return {"state": "downloading", "item_id": None, "error": None, "job_id": last.id}
    if last is not None and last.status == "failed":
        return {"state": "failed", "item_id": None, "error": last.error}
    return {"state": "offered", "item_id": None, "error": None}


def start(engine: Engine, entry: StarterEntry) -> dict[str, Any]:
    """The entry's download queued, unless the library has it or it is already on its way."""
    from clipforge.jobs.pipeline import USER_PRIORITY  # the pipeline registers this module's job

    with engine.lock:
        if added(engine, entry.id) is None:
            engine.runner.enqueue(KIND, entry.id, priority=USER_PRIORITY, rerun=True)
        return state_of(engine, entry)


def sweep(engine: Engine) -> None:
    """Download folders a stopped run left in the library removed. It runs while ClipForge opens, when no
    download is under way, and a download that was cut off starts again from nothing."""
    for folder in engine.paths.library.glob(".starter-*"):
        if folder.is_dir() and not folder.is_symlink():
            shutil.rmtree(folder, ignore_errors=True)


def download_starter(engine: Engine, ctx: JobContext) -> dict[str, Any] | None:
    """The job: the video (or its first 20 minutes) into a folder of its own, then into the library."""
    entry = entry_of(ctx.job.ref_id)
    if entry is None:
        raise ClipForgeError("not_found", NOT_LISTED)
    if ctx.cancelled():
        return None
    if (
        free_gb(engine.paths.home) < ctx.settings.min_free_gb
    ):  # as for a clip, so a video never fills the disk
        raise ClipForgeError("low_disk", LOW_DISK.format(gb=ctx.settings.min_free_gb))
    engine.paths.library.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix=".starter-", dir=engine.paths.library))
    try:
        url = url_of(entry)
        with ctx.beating():  # yt-dlp can take minutes over a big video
            if entry.duration_s > STARTER_MAX_S:
                got = engine.youtube.download_sections(
                    url, folder, [(0.0, STARTER_MAX_S)], "best", cancelled=ctx.cancelled
                )
                file = got[0] if got else None
            else:
                file = engine.youtube.download_video(url, folder, "best")
        if ctx.cancelled():
            return None
        if file is None or not Path(file).is_file():
            raise ClipForgeError("no_video", NO_VIDEO)
        notes = " ".join([entry.terms, *(f"{chip}." for chip in chips(entry)), f"From {url}"])
        with engine.lock:
            if (item := added(engine, entry.id)) is None:
                item = gameplay.add_gameplay(
                    engine.db(),
                    engine.paths,
                    file,
                    credit=entry.credit,
                    license=entry.licence,
                    notes=notes,
                    name=entry.title,
                    move=True,
                    tags=(TAG + entry.id,),
                )
        return {"item": item.id}
    finally:
        shutil.rmtree(folder, ignore_errors=True)
