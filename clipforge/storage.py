"""What ClipForge's files take on disk, and the clean-up (SPEC 16): the big source files of YouTube projects
left alone for ``delete_sources_after_days`` go, since YouTube still has them, while transcripts, signals,
the AI's answers, clips and renders stay. A project made from a file is never cleaned, as its video can't
be fetched again. Upload folders no project points into go after a day."""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path

from clipforge.config import Settings
from clipforge.paths import Paths, ProjectPaths

log = logging.getLogger(__name__)

DAY_S = 86400.0
UPLOAD_KEEP_S = DAY_S
# Settled projects (none still being made) whose own jobs and whose clips' jobs are all finished, the
# versions for accounts ("<clip>/<account>") among them.
CLEANABLE = """
SELECT p.id FROM projects p
WHERE p.source_kind != 'file' AND p.status IN ('ready', 'failed', 'refused') AND p.updated_at < ?
AND NOT EXISTS (
    SELECT 1 FROM jobs j JOIN clips c ON c.project_id = p.id
    WHERE j.status IN ('queued', 'running') AND (j.ref_id = c.id OR j.ref_id LIKE c.id || '/%')
)
AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.status IN ('queued', 'running') AND j.ref_id = p.id)
ORDER BY p.updated_at
"""


@dataclass(frozen=True)
class Usage:
    free_bytes: int
    total_bytes: int
    sources: int
    work: int
    renders: int
    library: int
    assets: int
    logs: int
    uploads: int


@dataclass(frozen=True)
class CleanResult:
    freed_bytes: int
    projects: int


def size_of(path: Path) -> int:
    """The bytes of ``path``, or of the files under it, links not followed; 0 when it isn't there."""
    try:
        st = path.lstat()
    except OSError:
        return 0
    if not stat.S_ISDIR(st.st_mode):
        return st.st_size
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            with contextlib.suppress(OSError):
                total += os.lstat(os.path.join(root, name)).st_size
    return total


def _projects(paths: Paths) -> list[ProjectPaths]:
    if not paths.projects.is_dir():
        return []
    return [ProjectPaths(p) for p in sorted(paths.projects.iterdir()) if p.is_dir() and not p.is_symlink()]


def usage(paths: Paths) -> Usage:
    disk = shutil.disk_usage(paths.home)
    projects = _projects(paths)
    return Usage(
        free_bytes=disk.free,
        total_bytes=disk.total,
        sources=sum(size_of(pp.source) for pp in projects),
        work=sum(size_of(pp.work) for pp in projects),
        renders=sum(size_of(pp.clips) for pp in projects),
        library=size_of(paths.library),
        assets=size_of(paths.assets),
        logs=size_of(paths.logs),
        uploads=size_of(paths.uploads),
    )


def doomed(pp: ProjectPaths) -> list[Path]:
    """What the clean-up deletes from a project: the downloaded video and audio, the sections cut from
    them and the 16 kHz audio the speech engine read."""
    found = [*pp.source.glob("video.*"), *pp.source.glob("audio.*"), pp.sections, pp.audio16k]
    return [p for p in found if p.exists() or p.is_symlink()]


def cleanable(
    conn: sqlite3.Connection, paths: Paths, settings: Settings, now: float
) -> list[tuple[str, int]]:
    """Each project the clean-up would clean now, with the bytes it would free, oldest first."""
    days = settings.delete_sources_after_days
    if not days:
        return []
    found = []
    for (pid,) in conn.execute(CLEANABLE, (now - days * DAY_S,)).fetchall():
        size = sum(size_of(p) for p in doomed(paths.project(pid)))
        if size:
            found.append((pid, size))
    return found


def remove(path: Path) -> int:
    """``path`` deleted, and the bytes that freed (0 when it couldn't be)."""
    size = size_of(path)
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    except OSError as err:
        log.warning("the clean-up couldn't delete %s: %s", path, err)
        return 0
    return size


def clean_project(pp: ProjectPaths) -> int:
    return sum(remove(p) for p in doomed(pp))


def _newest(folder: Path) -> float:
    times = []
    for p in [folder, *folder.iterdir()]:
        with contextlib.suppress(OSError):
            times.append(p.lstat().st_mtime)
    return max(times, default=0.0)


def stale_uploads(conn: sqlite3.Connection, paths: Paths, now: float) -> list[Path]:
    """Upload folders untouched for a day that no project's file is in."""
    if not paths.uploads.is_dir():
        return []
    urls = [u for (u,) in conn.execute("SELECT source_url FROM projects WHERE source_kind = 'file'") if u]
    stale = []
    for folder in sorted(paths.uploads.iterdir()):
        if not folder.is_dir() or folder.is_symlink():
            continue
        inside = folder.as_uri() + "/"
        if now - _newest(folder) > UPLOAD_KEEP_S and not any(u.startswith(inside) for u in urls):
            stale.append(folder)
    return stale
