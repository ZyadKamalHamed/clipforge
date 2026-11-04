"""One video, several projects (SPEC 10.9): two clipping channels that pick up the same upload, or one that
picks up a video started by hand. Each download and transcription stage first takes the finished files
another project of the video has, as hard links, and while an older project of the video is still making
them, it waits for that one rather than fetching or hearing the video a second time."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

from clipforge import repo
from clipforge.errors import WaitUntil

if TYPE_CHECKING:
    from clipforge.app import Engine
    from clipforge.jobs.runner import Job
    from clipforge.models import Project

log = logging.getLogger(__name__)

PARTIAL = (".part", ".ytdl", ".tmp", ".temp")  # downloads and writes still on their way
WAIT_S = 30.0  # how soon a stage waiting for another project's files looks again
WAITING = "Another project of this video is fetching it, so this one waits to use its files."
ACTIVE = ("queued", "running")
# The stages that can take another project's files, each with the stages of that project it waits for
WAITS_ON: dict[str, tuple[str, ...]] = {
    "ingest.audio": ("ingest.metadata", "ingest.audio"),
    "ingest.captions": ("ingest.metadata", "ingest.captions"),
    "ingest.extras": ("ingest.metadata", "ingest.extras"),
    "ingest.video": ("ingest.metadata", "ingest.video"),
    "asr.transcribe": (
        "ingest.metadata",
        "ingest.audio",
        "ingest.captions",
        "asr.transcribe",
        "signals.compute",
    ),
}


def share_files(src: Path, dst: Path) -> None:
    """Hard links in ``dst`` to the finished files in ``src`` that ``dst`` doesn't have, so a second project
    of a video downloads nothing again. Stages replace files rather than write into them, so the two
    projects never change each other's. A file that can't be linked is left for its stage to fetch."""
    if not src.is_dir():
        return
    for path in sorted(src.iterdir()):
        name = path.name
        if name.startswith(".") or name.endswith(PARTIAL) or not path.is_file() or path.is_symlink():
            continue
        _link(path, dst / name)


def _link(src: Path, dst: Path) -> bool:
    if not src.is_file() or dst.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)
    except OSError as err:
        log.info("%s is not shared (%s), so it is made again", dst.name, err)
        return False
    return True


def _order(project: Project) -> tuple[float, str]:
    return project.created_at, project.id


def borrow(engine: Engine, job: Job) -> None:
    """Before ``job``'s stage runs: WaitUntil while an older project of the same video still has one of the
    stages it waits for queued or running, then the files a settled project of the video has finished.
    Only older projects are waited for, so two never wait for each other."""
    conn = engine.db()
    project = repo.get_project(conn, job.ref_id)
    if project is None or not project.video_id:
        return
    kinds = WAITS_ON[job.kind]
    settled = []
    for other in sorted(repo.find_projects_by_video(conn, project.video_id), key=_order):
        if other.id == project.id:
            continue
        if any(j.kind in kinds and j.status in ACTIVE for j in engine.runner.jobs_for(other.id)):
            if _order(other) < _order(project):
                raise WaitUntil(time.time() + WAIT_S, WAITING)
            continue  # a newer one still on its way: its files may be half made
        settled.append(engine.paths.project(other.id))
    pp = engine.paths.project(project.id)
    for theirs in settled:
        if job.kind == "asr.transcribe":
            # The transcript and the signals made from it go together, from the one project
            if not pp.transcript_json.exists() and _link(theirs.transcript_json, pp.transcript_json):
                _link(theirs.signals_json, pp.signals_json)
        else:
            share_files(theirs.source, pp.source)
            if job.kind == "ingest.audio":
                _link(theirs.audio16k, pp.audio16k)
