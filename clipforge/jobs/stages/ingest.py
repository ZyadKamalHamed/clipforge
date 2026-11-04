"""The ingest stages (SPEC 7.1 and 7.2): what the source is, its audio, YouTube's captions, its comments and
live chat, and the whole video for rendering later. A link's files come from yt-dlp and a file is copied in
whole. Each stage skips the work whose files are already there."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from clipforge import children
from clipforge.errors import ClipForgeError, PipelineError, SourceRefused
from clipforge.ingest import ytdlp
from clipforge.ingest.thumbs import ensure_thumb
from clipforge.ingest.urls import file_from_url, video_title
from clipforge.jobs.stages import complete_file, missing, read_meta, write_json, write_text
from clipforge.media import ffmpeg
from clipforge.models import SourceMeta
from clipforge.render.qa import TIKTOK_MAX_S

if TYPE_CHECKING:
    from clipforge.app import Engine
    from clipforge.config import Settings
    from clipforge.jobs.runner import JobContext
    from clipforge.models import Project, ProjectOptions
    from clipforge.paths import ProjectPaths

log = logging.getLogger(__name__)

MAX_COMMENTS = 300  # SPEC 7.2: the top 300 comments
LIVE_CHAT_TIMEOUT_S = 600.0  # SPEC 7.2: the live chat replay gets at most 10 minutes
COPY_TIMEOUT_S = 3600.0
NO_AUDIO = "This video has no sound, so there is nothing to clip."
NO_LENGTH = "ClipForge can't tell how long this video is, so it can't clip it. The file may be damaged."


def free_gb(path: Path) -> float:
    """Free space on the disk that holds ``path``, in GB. A folder not made yet counts as its parent."""
    path = Path(path)
    while not path.exists() and path != path.parent:
        path = path.parent
    return shutil.disk_usage(path).free / 1e9


def video_wanted(meta: SourceMeta, options: ProjectOptions, settings: Settings) -> bool:
    """Whether to download the whole video now, for rendering later (SPEC 7.2): only for clips that will
    render, from a link whose video at the chosen quality is known to fit in ``full_download_max_gb``.
    Otherwise rendering downloads just the clips' sections. Drive serves whole files, so a Drive video
    downloads whole unless it is known to be too big."""
    if not options.render or meta.source_kind == "file":
        return False
    size = meta.video_bytes.get(options.quality or settings.download_quality)
    if size is None:
        return meta.source_kind == "drive"
    return size <= settings.full_download_max_gb * 1e9


def as_is_refusal(meta: SourceMeta, options: ProjectOptions) -> SourceRefused | None:
    """Post as it is posts the whole video (SPEC 14.2), so one over TikTok's 10 minutes is refused before
    anything downloads."""
    if not options.as_is or not meta.duration_s or meta.duration_s <= TIKTOK_MAX_S:
        return None
    m, s = divmod(math.ceil(meta.duration_s), 60)
    length = f"{m} min {s} s" if s else f"{m} min"
    return SourceRefused(
        "too_long",
        f"This video is {length} long, over the 10 minutes TikTok takes. Turn off Post as it is to cut "
        "clips from it instead.",
    )


# ingest.metadata


def metadata(engine: Engine, ctx: JobContext) -> dict[str, Any]:
    """The source's details, saved as info.json and meta.json, and the project card filled in. A source
    that can't be clipped is refused before anything is downloaded, with the card still saying which
    video it was."""
    pid = ctx.job.ref_id
    settings = ctx.settings
    pp = engine.paths.project(pid)
    if free_gb(pp.root) < settings.min_free_gb:
        raise PipelineError(
            "low_disk",
            f"Free up some disk space: ClipForge needs at least {settings.min_free_gb:g} GB free to start "
            "a project.",
        )
    pp.ensure()
    project = engine.project(pid)
    if project.source_kind == "file":
        return _file_metadata(engine, ctx, project, pp)
    if pp.info_json.is_file() and pp.meta_json.is_file():
        info = json.loads(pp.info_json.read_text(encoding="utf-8"))
        meta = read_meta(pp)
    else:
        info = ytdlp.clean_info(engine.youtube.fetch_info(project.source_url))
        write_json(pp.info_json, info)
        meta = ytdlp.meta_from_info(info)
        if project.source_kind == "drive":  # Drive names a video by its file name
            meta = meta.model_copy(update={"source_kind": "drive", "title": video_title(meta.title)})
        elif project.source_kind == "other":
            meta = meta.model_copy(update={"source_kind": "other"})
    engine.update_project(
        pid,
        title=meta.title,
        channel=meta.channel,
        channel_url=meta.channel_url,
        duration_s=meta.duration_s,
        language=meta.language or project.language,
        license=meta.license,
        rights_basis=rights_basis(info, meta, settings.own_channels, project.rights_basis),
    )
    refusal = ytdlp.refusal_for(meta, max_hours=settings.max_source_hours) or as_is_refusal(
        meta, project.options
    )
    if refusal is not None:
        raise refusal
    write_text(pp.meta_json, meta.model_dump_json())
    _thumb(engine, project)
    return {"video": video_wanted(meta, project.options, settings)}


def _file_metadata(engine: Engine, ctx: JobContext, project: Project, pp: ProjectPaths) -> dict[str, Any]:
    src = file_path(project)
    clone = complete_file(pp.source, "video")  # the project's own copy, once it has one
    if clone is None and not src.is_file():
        raise PipelineError(
            "missing_file",
            f"ClipForge can't find the file {src.name} any more. Put it back, or drop it in again.",
        )
    info = ffmpeg.probe(clone or src)
    title = project.title or src.stem
    engine.update_project(project.id, title=title, duration_s=info.duration_s)
    if not info.has_audio:
        raise SourceRefused("no_audio", NO_AUDIO)
    if not info.duration_s:
        raise SourceRefused("no_length", NO_LENGTH)
    meta = SourceMeta(
        source_kind="file",
        video_id=None,
        title=title,
        channel=None,
        channel_url=None,
        duration_s=info.duration_s,
    )
    refusal = ytdlp.refusal_for(meta, max_hours=ctx.settings.max_source_hours) or as_is_refusal(
        meta, project.options
    )
    if refusal is not None:
        raise refusal
    if clone is None:
        with ctx.beating():  # a big file from a slow drive takes a while, and cp says nothing
            copy_in(src, pp.source / f"video{src.suffix.lower()}")
    write_text(pp.meta_json, meta.model_dump_json())
    if src.parent.parent.resolve() == engine.paths.uploads.resolve():
        shutil.rmtree(src.parent, ignore_errors=True)  # a file dropped on the window: the copy is enough now
    _thumb(engine, project)
    return {"video": False}


def _thumb(engine: Engine, project: Project) -> None:
    """The card's picture, made now so the window has it from the start (SPEC 14.3). One that can't be
    made is remembered, so the window shows none rather than asking for it again."""
    if ensure_thumb(engine.paths, project, fetch=engine.thumb_fetch) is None:
        engine.thumb_misses.add(project.id)


def file_path(project: Project) -> Path:
    """The original file of a project made from one; its ``source_url`` is a file:// link to it."""
    return file_from_url(project.source_url or "")


def copy_in(src: Path, dst: Path) -> Path:
    """``src`` copied to ``dst`` whole or not at all: an APFS clone when the disk can make one, which is
    instant and takes no space, else a plain copy."""
    tmp = dst.with_name(f".{dst.name}.part")
    tmp.unlink(missing_ok=True)
    try:
        children.run(
            ["/bin/cp", "-c", str(src.absolute()), str(tmp.absolute())],
            check=True,
            capture_output=True,
            timeout=COPY_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as err:
        tmp.unlink(missing_ok=True)
        if children.closing():  # killed as ClipForge closes: no slow copy now
            raise
        log.info("no clone of %s (%s), so it is copied", src.name, err)
        try:
            shutil.copy2(src, tmp)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    os.replace(tmp, dst)
    return dst


# Who may post the clips


def rights_basis(info: dict, meta: SourceMeta, own_channels: list[str], current: str) -> str:
    """Why clips of this video may be posted (SPEC 7.1): "own" when it is on one of the user's channels,
    "cc" when it has a Creative Commons license, else ``current``. It never blocks anything."""
    names = ("channel_url", "uploader_url", "uploader_id", "channel_id")
    if channel_keys(*(info.get(name) for name in names)) & channel_keys(*own_channels):
        return "own"
    return "cc" if meta.is_creative_commons else current


def channel_keys(*values: object) -> set[str]:
    """The names a channel goes by in ``values``, so that "youtube.com/@TED/", "@ted" and
    "https://www.youtube.com/@TED" all match: each link in lower case without its scheme, a leading
    www. or m., its query and a trailing slash; its @handle; and the id or name after /channel/, /c/ or
    /user/. A value with no slash, like "@TED" or a channel id, also counts without its @."""
    keys: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        text = re.sub(r"^[a-z][a-z0-9+.-]*://", "", value.strip().lower())
        text = re.sub(r"^(?:www|m)\.", "", re.split(r"[?#]", text, maxsplit=1)[0]).rstrip("/")
        if not text:
            continue
        keys.add(text)
        parts = text.split("/")
        if len(parts) == 1:
            keys.add(text.removeprefix("@"))
        for k, part in enumerate(parts[1:], 1):
            if part.startswith("@") and len(part) > 1:
                keys.add(part[1:])
            elif part in ("channel", "c", "user") and k + 1 < len(parts) and parts[k + 1]:
                keys.add(parts[k + 1])
    keys.discard("")
    return keys


# ingest.audio


def audio(engine: Engine, ctx: JobContext) -> dict[str, Any]:
    """The speech track as 16 kHz mono WAV: from YouTube's audio-only stream for a link, from the copied
    video for a file."""
    pid = ctx.job.ref_id
    pp = engine.paths.project(pid)
    if pp.audio16k.is_file():
        return {"skipped": True}
    project = engine.project(pid)
    if project.source_kind == "file":
        src = complete_file(pp.source, "video")
        if src is None:
            raise missing(pp.source / f"video{file_path(project).suffix.lower()}")
    else:
        src = complete_file(pp.source, "audio")
        if src is None:
            src = engine.youtube.download_audio(project.source_url, pp.source, progress=ctx.progress)
    ffmpeg.to_wav16k(src, pp.audio16k)
    return {"from": src.name}


# ingest.captions and ingest.extras


def captions(engine: Engine, ctx: JobContext) -> dict[str, Any]:
    """YouTube's captions, which the speech to text checks its gaps against. A bonus like the extras: when
    they fail, the transcript is made without them."""
    pid = ctx.job.ref_id
    project = engine.project(pid)
    if project.source_kind != "youtube":
        return {"skipped": True}  # caption tracks are YouTube's
    pp = engine.paths.project(pid)
    meta = read_meta(pp)
    youtube, url = engine.youtube, project.source_url
    parts: list[tuple[str, Callable[[], object]]] = []
    if meta.caption_track and not any(pp.root.glob(pp.captions_glob)):
        parts.append(("captions", lambda: youtube.download_captions(url, pp.source, meta.caption_track)))
    return _fetch(ctx, parts)


def extras(engine: Engine, ctx: JobContext) -> dict[str, Any]:
    """YouTube's top comments and, for a past live stream, the chat replay. Only the signals read them, so
    the transcription never waits for a long replay."""
    pid = ctx.job.ref_id
    project = engine.project(pid)
    if project.source_kind != "youtube":
        return {"skipped": True}  # comments and chat replays are YouTube's
    pp = engine.paths.project(pid)
    meta = read_meta(pp)
    settings, youtube, url = ctx.settings, engine.youtube, project.source_url
    parts: list[tuple[str, Callable[[], object]]] = []
    if settings.comments and not pp.comments_json.exists():
        parts.append(("comments", lambda: _save_comments(youtube.fetch_comments(url, MAX_COMMENTS), pp)))
    if settings.live_chat and meta.live_status == "was_live" and not pp.live_chat_json.exists():

        def live_chat() -> object:
            with ctx.beating():  # the chat child says nothing until it is done, at most LIVE_CHAT_TIMEOUT_S
                return youtube.download_live_chat(url, pp.source, timeout_s=LIVE_CHAT_TIMEOUT_S)

        parts.append(("live chat", live_chat))
    return _fetch(ctx, parts)


def _fetch(ctx: JobContext, parts: list[tuple[str, Callable[[], object]]]) -> dict[str, Any]:
    """Gets each named part. Each is a bonus: one that fails is logged and left out. A network error that
    may pass is raised for another try once every part has had its turn, while tries remain."""
    pid = ctx.job.ref_id
    failed: list[str] = []
    again: ClipForgeError | None = None
    for name, fetch in parts:
        try:
            fetch()
        except ClipForgeError as err:
            log.warning("%s for %s left out: %s: %s", name, pid, err.code, err.details or err.message)
            failed.append(name)
            if err.retryable and again is None:
                again = err
        except Exception:  # one part's trouble never costs the others
            log.exception("%s for %s left out", name, pid)
            failed.append(name)
    if again is not None and ctx.job.attempts < ctx.job.max_attempts:
        raise again
    return {"failed": failed}


def _save_comments(comments: list, pp: ProjectPaths) -> None:
    write_json(pp.comments_json, [c.model_dump() for c in comments])


# ingest.video


def video(engine: Engine, ctx: JobContext) -> dict[str, Any]:
    """The whole video at the chosen quality, kept for rendering later."""
    pid = ctx.job.ref_id
    pp = engine.paths.project(pid)
    if complete_file(pp.source, "video") is not None:
        return {"skipped": True}
    project = engine.project(pid)
    quality = project.options.quality or ctx.settings.download_quality
    path = engine.youtube.download_video(project.source_url, pp.source, quality, progress=ctx.progress)
    return {"file": Path(path).name}
