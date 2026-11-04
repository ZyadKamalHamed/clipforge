"""Clip jobs (SPEC 7.5 to 7.10). Their ref is the clip id and their payload names the project. clip.source
finds a file holding the clip: the whole video when it is there, or else the clip padded by 5 s, downloaded
once as a section and reused by every clip it covers; until one of the project's clips has rendered, a
section waits for a better clip on its way (SPEC 19). clip.analyze reads that file once for faces, speakers,
shots and the layout; when analysis.json already has this version and key it only chooses the layout again,
for an edit or a library that changed. clip.render makes the clip's next version in ``clips/<clip_id>/v<N>/``
(SPEC 6.3), built in a hidden ``.v<N>.tmp`` folder and renamed into place once whole, so the previous version
stays until the new one is done. clip.variant makes an account's own version of a clip for its posts (SPEC
11) the same way, in the account's style kit and on its own stretch of gameplay; its ref is
``<clip id>/<account id>`` and its payload names both, and it never becomes the clip's current render."""

from __future__ import annotations

import os
import random
import re
import shutil
import time
import wave
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from clipforge import assets, repo
from clipforge.brain.wording import credit_channel
from clipforge.campaigns.rules import campaign_of, enforce
from clipforge.db import transaction
from clipforge.edits import apply_packaging_edits, effective_edits, render_key
from clipforge.errors import ClipForgeError, PipelineError, ToolError, WaitUntil
from clipforge.jobs.runner import Job, JobContext
from clipforge.jobs.stages import complete_file, read_meta, read_signals, read_transcript, write_text
from clipforge.jobs.stages.ingest import free_gb
from clipforge.library import gameplay
from clipforge.library.music import music_items
from clipforge.media.ffmpeg import probe
from clipforge.models import Analysis, ClipRow, LibraryItem, Packaging, ProjectOptions, RenderRow
from clipforge.paths import ProjectPaths
from clipforge.render import audio
from clipforge.render.cover import make_cover
from clipforge.render.edl import (
    EDL,
    GAMEPLAY_GONE,
    GAMEPLAY_OFF,
    MUSIC_GONE,
    MUSIC_NO_MATCH,
    NO_GAMEPLAY_ITEM,
    VO_CUT,
    VO_GONE,
    VO_LATE,
    build_edl,
    sfx_layer,
)
from clipforge.render.emoji import fetched
from clipforge.render.encode import mux, reencode_capped, render_video
from clipforge.render.qa import QAResult, run_qa, upscale_of
from clipforge.render.styles import Style, StyleKit, style_for, style_key
from clipforge.trends.recommend import best_music, feel
from clipforge.vision.analyze import ANALYSIS_VERSION, PAD_S, SR, analysis_key, analyze_clip, relayout
from clipforge.vision.decode import ClipSource
from clipforge.vision.faces import FaceDetector

if TYPE_CHECKING:
    from clipforge.app import Engine
    from clipforge.campaigns.models import Campaign
    from clipforge.config import Settings
    from clipforge.post.models import Account

SECTION_PAD_S = 5.0
WAIT_S = 5.0
WAITING = "Waiting for the video download to finish"
BEST_FIRST = "Waiting for a better clip to render first"
RENDER_JOBS = ("clip.source", "clip.analyze", "clip.render")  # a clip's render, which goes best first
SOON_S = 30.0  # about what a clip's own section takes to download
NO_SOURCE = "The video for this clip is missing. Try again."
NO_ANALYSIS = "This clip hasn't been analysed yet. Try again."
GONE = "This clip was deleted."
ACCOUNT_GONE = "The account this version was for was removed."
CANCELLED = "The render was stopped."
LOW_DISK = "Free up some disk space: ClipForge needs at least {gb:g} GB free to make clips."
ITEM_LEFT = "That gameplay video left the library as this clip started, so it renders again."
TRACK_LEFT = "That music track left the library as this clip started, so it renders again."
TAKE_LEFT = "The voiceover was removed as this clip started, so it renders again."
SECTION = re.compile(r"video\.(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)\.\w+$")
VERSION = re.compile(r"\.?v(\d+)(?:\.tmp)?$")
ENCODERS = {"fast": "VideoToolbox", "best": "libx264"}
AUDIO_PAD_S = 0.5  # source sound read past the kept parts, for the joins' crossfades and any speed change
EPS = 1e-6


def local_source(pp: ProjectPaths, start_s: float, end_s: float) -> ClipSource | None:
    """The whole video, or a downloaded section that covers ``start_s`` to ``end_s``."""
    video = complete_file(pp.source, "video")
    if video is not None:
        return ClipSource(video)
    if pp.sections.is_dir():
        for path in sorted(pp.sections.iterdir()):
            m = SECTION.match(path.name)
            if m and float(m[1]) <= start_s + EPS and float(m[2]) >= end_s - EPS:
                return ClipSource(path, float(m[1]))
    return None


def _clip(engine: Engine, ctx: JobContext) -> tuple[ClipRow, ProjectPaths]:
    """The job's clip with its edits over its project's layout and caption style (a copy to read: the row
    keeps only the clip's own edits), and the project's folders."""
    clip = repo.get_clip(engine.db(), ctx.job.ref_id)
    if clip is None:
        raise ClipForgeError("no_clip", GONE)
    project = engine.project(clip.project_id)
    edits = effective_edits(project.options if project is not None else ProjectOptions(), clip.edits)
    return clip.model_copy(update={"edits": edits or None}), engine.paths.project(ctx.job.payload["project"])


def room(engine: Engine, ctx: JobContext) -> None:
    """A clip's video and render need room: under ``min_free_gb`` free, the clip fails saying so."""
    if free_gb(engine.paths.home) < ctx.settings.min_free_gb:
        raise PipelineError("low_disk", LOW_DISK.format(gb=ctx.settings.min_free_gb))


def clip_source(engine: Engine, ctx: JobContext) -> dict[str, Any]:
    room(engine, ctx)
    clip, pp = _clip(engine, ctx)
    a, b = max(0.0, clip.start_s - SECTION_PAD_S), clip.end_s + SECTION_PAD_S
    src = local_source(pp, a, b)
    if src is None:
        pid = clip.project_id
        every = engine.clips(pid)
        first = not any(c.current_render_id for c in every)
        downloading = ("queued", "running")
        video = [
            j for j in engine.runner.jobs_for(pid) if j.kind == "ingest.video" and j.status in downloading
        ]
        # SPEC 19: the project's first clip waits for the whole video only if it is nearly done
        if video and (not first or _lands_soon(video[0])):
            raise WaitUntil(time.time() + WAIT_S, WAITING)
        if first and _behind(engine, clip, every):
            raise WaitUntil(time.time() + WAIT_S, BEST_FIRST)
        _download_section(engine, ctx, clip, pp)
        src = local_source(pp, a, b)
        if src is None:
            raise ClipForgeError("no_source", NO_SOURCE)
    return {"path": str(src.path), "offset_s": src.offset_s}


def _download_section(engine: Engine, ctx: JobContext, clip: ClipRow, pp: ProjectPaths) -> None:
    """Downloads the clip's stretch of a YouTube project's video, with ``SECTION_PAD_S`` either side. A
    project made from a file has nothing to fetch."""
    project = engine.project(clip.project_id)
    if project is None or project.source_kind == "file" or not project.source_url:
        return
    a, b = max(0.0, clip.start_s - SECTION_PAD_S), clip.end_s + SECTION_PAD_S
    quality = project.options.quality or ctx.settings.download_quality
    pp.sections.mkdir(parents=True, exist_ok=True)
    with ctx.beating():  # a section says nothing until it is done, within a time limit of its own
        engine.youtube.download_sections(
            project.source_url, pp.sections, [(a, b)], quality, cancelled=ctx.cancelled
        )


def _lands_soon(job: Job) -> bool:
    """Whether the whole video's download, at its pace so far, is done within ``SOON_S``. A 2.1 GB video
    can take longer than speech to text and selection (410 s against 273 s), and a queued one has not
    started."""
    if job.status != "running" or not job.started_at or job.progress <= 0:
        return False
    spent = time.time() - job.started_at
    return spent * (1 - job.progress) / job.progress <= SOON_S


def _behind(engine: Engine, clip: ClipRow, every: list[ClipRow]) -> bool:
    """Whether the clip's section waits for a better clip (SPEC 19), until one of the project's clips
    (``every``) has rendered: sections downloading beside a render made it over twice as slow, so a better
    clip with a render job queued or running goes first. Its re-check, which can wait hours on a usage
    limit, holds up nothing."""
    better = [c.id for c in every if c.rank is not None and (clip.rank is None or c.rank < clip.rank)]
    return any(
        job.kind in RENDER_JOBS and job.status in ("queued", "running")
        for cid in better
        for job in engine.runner.jobs_for(cid)
    )


def face_detector(engine: Engine):
    """YuNet, loaded once per engine; tests set ``engine.face_detector`` to a stand-in."""
    if engine.face_detector is None:
        engine.face_detector = FaceDetector(assets.ensure_model(engine.paths))
    return engine.face_detector


def has_gameplay(conn) -> bool:
    """Whether the library has gameplay to put under a speaker."""
    return bool(gameplay.usable(conn))


def read_pcm(path: Path, start_s: float, end_s: float) -> np.ndarray | None:
    """The 16 kHz mono sound from ``start_s`` to ``end_s``, read from the audio stage's file, so a long
    video is never loaded whole."""
    if not path.exists():
        return None
    with wave.open(str(path), "rb") as w:
        if w.getframerate() != SR or w.getnchannels() != 1 or w.getsampwidth() != 2:
            return None
        first = min(w.getnframes(), round(start_s * SR))
        w.setpos(first)
        data = w.readframes(max(0, round((end_s - start_s) * SR)))
    return np.frombuffer(data, np.int16).astype(np.float32) / 32768


def clip_analyze(engine: Engine, ctx: JobContext) -> dict[str, Any]:
    clip, pp = _clip(engine, ctx)
    src = local_source(pp, clip.start_s, clip.end_s)
    if src is None:
        raise ClipForgeError("no_source", NO_SOURCE)
    transcript = read_transcript(pp)
    signals = read_signals(pp) if pp.signals_json.exists() else None
    edits = clip.edits or {}
    campaign = campaign_of(engine, engine.project(clip.project_id))
    forced = ctx.job.payload.get("layout") or edits.get("layout")
    choice: dict[str, Any] = {
        "gameplay_mode": enforce(style_for(ctx.settings, edits), campaign).gameplay_mode,
        "has_gameplay": has_gameplay(engine.db()),
        "forced": None if forced == "gameplay" and _no_gameplay(campaign) else forced,
    }
    out = pp.analysis_json(clip.id)
    key = analysis_key(src, clip.start_s, clip.end_s)
    old = Analysis.model_validate_json(out.read_text()) if out.exists() else None
    if old is not None and old.version == ANALYSIS_VERSION and old.key == key:
        analysis = relayout(old, transcript, signals, clip.start_s, clip.end_s, **choice)
        if (analysis.layout, analysis.layout_reason) == (old.layout, old.layout_reason):
            return {"skipped": True}
    else:
        pcm_start = max(0.0, clip.start_s - PAD_S)
        analysis = analyze_clip(
            src,
            clip.start_s,
            clip.end_s,
            detector=face_detector(engine),
            transcript=transcript,
            signals=signals,
            **choice,
            pcm=read_pcm(pp.audio16k, pcm_start, clip.end_s + PAD_S),
            pcm_start_s=pcm_start,
        )
    write_text(out, analysis.model_dump_json())
    engine.update_clip(clip.id, layout=analysis.layout, layout_reason=analysis.layout_reason)
    return {"layout": analysis.layout, "quality": analysis.quality}


def _no_gameplay(campaign: Campaign | None) -> bool:
    """Whether the linked campaign bans the gameplay split, which then wins over a layout you chose."""
    return campaign is not None and "gameplay" in campaign.banned


def look_of(
    engine: Engine, clip: ClipRow, settings: Settings, kit: StyleKit | None = None
) -> tuple[ClipRow, dict[str, Any], Style, Campaign | None]:
    """What the clip renders from: a copy of it with its edits over its project's, less a gameplay layout
    its linked campaign bans (the campaign wins over your choice); those edits; the style it renders in,
    with ``kit`` (an account's style kit) laid over the clip's look; and that campaign."""
    project = engine.project(clip.project_id)
    edits = effective_edits(project.options if project is not None else ProjectOptions(), clip.edits)
    campaign = campaign_of(engine, project)
    style = enforce(style_for(settings, edits, kit), campaign)
    if edits.get("layout") == "gameplay" and _no_gameplay(campaign):
        edits = {k: v for k, v in edits.items() if k != "layout"}
    return clip.model_copy(update={"edits": edits or None}), edits, style, campaign


def clip_render(engine: Engine, ctx: JobContext) -> dict[str, Any]:
    """The clip's next version (SPEC 7.10): the edit list built from the analysis, packaging and style, the
    sound (made beside the picture, as it needs little of the CPU) and picture made from it and put
    together, capped under Instagram's bitrate limit, its cover and
    QA, all in ``.v<N>.tmp`` until it is renamed ``v<N>``. Then a renders row, and the version becomes the
    clip's current one: the clip is ``rendered``, or ``failed`` with QA's sentence when a hard problem
    blocks both apps. A failure on the way leaves no version and the previous one current."""
    clip = repo.get_clip(engine.db(), ctx.job.ref_id)
    if clip is None:
        raise ClipForgeError("no_clip", GONE)
    return _render(engine, ctx, clip, None)


def clip_variant(engine: Engine, ctx: JobContext) -> dict[str, Any]:
    """An account's own version of the clip for its posts (SPEC 11), made as the clip's own is but in the
    account's style kit and on a stretch of gameplay of its own, as a renders row naming the account. It
    never becomes the clip's current render or changes the clip; one that QA blocks fails the job with
    QA's sentence, so the post waiting on it says why."""
    payload = ctx.job.payload or {}
    clip = repo.get_clip(engine.db(), payload.get("clip_id", ""))
    if clip is None:
        raise ClipForgeError("no_clip", GONE)
    from clipforge.post import store  # the posting side reads this module's jobs

    account = store.get_account(engine.db(), payload.get("account_id", ""))
    if account is None:
        raise ClipForgeError("no_account", ACCOUNT_GONE)
    return _render(engine, ctx, clip, account)


def _render(engine: Engine, ctx: JobContext, clip: ClipRow, account: Account | None) -> dict[str, Any]:
    """The clip's next version, its own (``account`` None) or that account's; only its own moves the clip's
    current render and status."""
    room(engine, ctx)
    owner = account.id if account is not None else None
    kit = account.settings.style_kit if account is not None else None
    clip, edits, style, campaign = look_of(engine, clip, ctx.settings, kit)
    pp = engine.paths.project(clip.project_id)
    src = local_source(pp, clip.start_s, clip.end_s)
    if src is None and account is not None:  # the clean-up may have taken the video since the clip's own
        _download_section(engine, ctx, clip, pp)
        src = local_source(pp, clip.start_s, clip.end_s)
    if src is None:
        raise ClipForgeError("no_source", NO_SOURCE)
    saved = pp.analysis_json(clip.id)
    if not saved.exists():
        raise ClipForgeError("no_analysis", NO_ANALYSIS)
    analysis = Analysis.model_validate_json(saved.read_text())
    if kit is not None and kit.gameplay_mode is not None:  # the kit's gameplay mode chooses the layout again
        signals = read_signals(pp) if pp.signals_json.exists() else None
        analysis = relayout(
            analysis,
            read_transcript(pp),
            signals,
            clip.start_s,
            clip.end_s,
            gameplay_mode=style.gameplay_mode,
            has_gameplay=has_gameplay(engine.db()),
            forced=edits.get("layout"),
        )
    project = engine.project(clip.project_id)
    packaging = Packaging.model_validate(clip.packaging) if clip.packaging else Packaging(hook_title="")
    packaging = apply_packaging_edits(packaging, edits)
    info = probe(src.path)
    seed = f"{clip.id}:{owner}" if owner is not None else clip.id  # each account's version picks its own
    rng = random.Random(f"{seed}:{edits.get('gameplay_seed', 0)}")  # a shuffle moves the seed on
    wanted = edits.get("gameplay_item")
    item = None
    if analysis.layout == "gameplay" and not _no_gameplay(campaign):
        item = gameplay.pick_item(engine.db(), clip.id, rng=rng, wanted=wanted)
    edl = build_edl(
        clip,
        read_transcript(pp),
        analysis,
        packaging,
        style,
        src_fps=analysis.fps,
        src_duration=src.offset_s + info.duration_s,
        credit_name=credit_channel(read_meta(pp), project.rights_basis, project.options),
        gameplay=_gameplay(item),
    )
    if wanted and item is not None and item.id != wanted and edl.gameplay is not None:
        edl = edl.model_copy(update={"notes": [*edl.notes, GAMEPLAY_GONE]})
    if item is not None and edl.gameplay is not None:
        with engine.lock:  # so two renders never take the same segment
            if repo.get_library_item(engine.db(), item.id) is None:  # deleted since it was picked
                raise ClipForgeError("gameplay_gone", ITEM_LEFT, retryable=True)
            start = gameplay.segment_for(engine.db(), clip.id, item, edl.duration, rng=rng, account_id=owner)
            repo.record_gameplay_use(
                engine.db(), item.id, start, start + edl.duration, clip_id=clip.id, account_id=owner
            )
        edl = edl.model_copy(update={"gameplay": {**edl.gameplay, "start_s": start}})
    track, left_out = music_for(engine.db(), clip, style)
    notes = [*edl.notes, left_out] if left_out else edl.notes
    edl = edl.model_copy(update={"music": music_record(track, style), "notes": notes})
    take, left_out = voiceover_for(pp, clip, edl.duration)
    notes = [*edl.notes, left_out] if left_out else edl.notes
    edl = edl.model_copy(update={"voiceover": take, "notes": notes})
    with ctx.beating():  # each emoji picture's fetch has a time limit of its own
        edl = _with_emoji(engine, edl)
    layout, reason = analysis.layout, analysis.layout_reason
    fallback = next((note for note in edl.notes if note in (GAMEPLAY_OFF, NO_GAMEPLAY_ITEM)), None)
    if fallback is not None:
        layout, reason = "face", fallback

    folder = pp.clip_dir(clip.id)
    folder.mkdir(parents=True, exist_ok=True)
    for left in folder.glob(".v*.tmp"):  # what a render killed halfway left behind
        shutil.rmtree(left, ignore_errors=True)
    taken = [r.version for r in repo.list_renders(engine.db(), clip.id)]
    taken += [int(m[1]) for p in folder.iterdir() if (m := VERSION.fullmatch(p.name))]
    version = 1 + max(taken, default=0)
    tmp = folder / f".v{version}.tmp"
    tmp.mkdir()
    video = tmp / "clip.mp4"
    log = Log(version, clip.id, layout, reason)
    try:
        with ctx.beating():
            # the sound goes to a thread of its own: the picture's progress and cancel checks use this
            # thread's connection to the database
            with ThreadPoolExecutor(1, thread_name_prefix="sound") as pool:
                bed = Path(track.path) if track is not None else None
                sound = pool.submit(_timed, _sound, src, edl, tmp / "sound.m4a", bed)
                with log.step("picture"):
                    game = ClipSource(Path(item.path)) if item is not None and edl.gameplay else None
                    used = _picture(ctx, edl, src, game, tmp / "video.mp4", log)
                report, took = sound.result()
            log.times.insert(0, ("sound", took))
            with log.step("file"):
                mux(tmp / "video.mp4", tmp / "sound.m4a", video)
                (tmp / "video.mp4").unlink()
                (tmp / "sound.m4a").unlink()
                capped = reencode_capped(video)
            with log.step("cover"):
                cover_s = make_cover(video, used, tmp / "cover.jpg", tmp / "thumb.jpg")
            with log.step("QA"):
                upscale = upscale_of(used)
                qa = run_qa(
                    video, used, report, upscale=upscale, hdr=info.hdr, own=project.rights_basis == "own"
                )
            made = probe(video)
            write_text(tmp / "edl.json", used.model_dump_json())
            write_text(tmp / "qa.json", qa.model_dump_json())
            write_text(tmp / "render.log", log.text(used, item, report, capped, cover_s, qa))
        final = pp.render_dir(clip.id, version)
        os.replace(tmp, final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    blocked = not qa.passed_for("instagram") and not qa.passed_for("tiktok")
    row = RenderRow(
        id=repo.new_id("r"),
        clip_id=clip.id,
        version=version,
        style_key=style_key(style),  # the style asked for, though a failed fast encode was made again
        edits_key=render_key(clip, edits),
        path=str(final / "clip.mp4"),
        cover_path=str(final / "cover.jpg"),
        thumb_path=str(final / "thumb.jpg"),
        cover_s=cover_s,
        duration_s=made.duration_s,
        size_bytes=(final / "clip.mp4").stat().st_size,
        edl_path=str(final / "edl.json"),
        qa=qa.model_dump(mode="json"),
        status="failed" if blocked else "ready",
        account_id=owner,
    )
    with engine.lock:
        with transaction(engine.db()):  # the version and the clip pointing at it land together
            repo.add_render(engine.db(), row)
            if used.gameplay is None:
                repo.drop_gameplay_use(engine.db(), clip.id, owner)
            if account is None:
                repo.update_clip(
                    engine.db(),
                    clip.id,
                    current_render_id=row.id,
                    status="failed" if blocked else "rendered",
                    error=blocker(qa) if blocked else None,
                    layout=layout,
                    layout_reason=reason,
                )
        if account is None:
            engine.publish_clip(clip.id)  # after the commit, so a window that looks sees the new version
    if account is not None and blocked:
        raise ClipForgeError("variant_blocked", blocker(qa))
    return {
        "render_id": row.id,
        "version": version,
        "path": row.path,
        "status": row.status,
        "problems": qa.problems(),
    }


def blocker(qa: QAResult) -> str:
    """The sentence for a render that can go to neither app: a problem for both, else the first one."""
    hard = [c for c in qa.checks if not c.ok and c.hard]
    return next((c.message for c in hard if c.platform is None), hard[0].message)


def resting(conn, clip: ClipRow) -> tuple[str, str | None]:
    """The status, and its sentence, of a clip whose jobs were stopped: as its current render left it (one
    that can go to neither app failed, and says why), or an idea when it has none."""
    render = repo.get_render(conn, clip.current_render_id) if clip.current_render_id else None
    if render is None:
        return "idea", None
    qa = QAResult.model_validate(render.qa) if render.qa else None
    if render.status == "ready" or qa is None or all(c.ok or not c.hard for c in qa.checks):
        return "rendered", None
    return "failed", blocker(qa)


def forget(engine: Engine, project_id: str, clip_ids: Iterable[str]) -> None:
    """For clips a new selection removed: stops their jobs and deletes their folders."""
    pp = engine.paths.project(project_id)
    for cid in clip_ids:
        for job in engine.runner.jobs_for(cid):
            if job.status in ("queued", "running"):
                engine.runner.cancel(job.id)
        shutil.rmtree(pp.clip_dir(cid), ignore_errors=True)


def _gameplay(item: LibraryItem | None) -> dict[str, Any] | None:
    """The edit list's record of the gameplay under the speaker; its segment is picked once the clip's
    length is known."""
    if item is None:
        return None
    fields = {"item_id": item.id, "name": item.name, "credit": item.credit, "license": item.license}
    return {**fields, "start_s": 0.0}


def _with_emoji(engine: Engine, edl: EDL) -> EDL:
    """The edit list with each emoji's picture, leaving out (with their pops) the ones it couldn't get."""
    kept = fetched(engine.paths, edl.emoji, fetch=engine.emoji_fetch)
    update: dict[str, Any] = {"emoji": kept}
    lost = len(edl.emoji) - len(kept)
    if lost:
        update["sfx"] = sfx_layer(edl.spans, kept, edl.payoff_s, edl.style, edl.duration)
        note = "1 emoji was" if lost == 1 else f"{lost} emoji were"
        pictures = "its picture" if lost == 1 else "their pictures"
        update["notes"] = [*edl.notes, f"{note} left out, since ClipForge couldn't get {pictures}"]
    return edl.model_copy(update=update)


def _timed(work: Callable[..., Any], *args: Any) -> tuple[Any, float]:
    """``work(*args)`` and how long it took, in seconds."""
    start = time.monotonic()
    return work(*args), time.monotonic() - start


def _sound(src: ClipSource, edl: EDL, out: Path, track: Path | None) -> audio.AudioReport:
    """The clip's finished sound: the kept parts of the source's, the speech cleaned, the voiceover over it
    when the edit list has one, the effects under that, the music ``track`` under all of it when the edit
    list has one, levelled to -14 LUFS."""
    base = max(0.0, min(s.src_start for s in edl.segments) - AUDIO_PAD_S)
    end = max(s.src_end for s in edl.segments) + AUDIO_PAD_S
    speech = audio.clean_speech(audio.assemble(audio.decode_audio(src, base, end), base, edl))
    if edl.voiceover is not None:
        take = Path(edl.voiceover["path"])
        if not take.is_file():  # removed since the edit list was made
            raise ClipForgeError("voiceover_gone", TAKE_LEFT, retryable=True)
        voice = audio.clean_speech(audio.decode_take(take, edl.voiceover["duration_s"] + 1.0))
        speech = audio.mix_voiceover(speech, voice, edl.voiceover["at"])
    mixed = audio.mix_sfx(speech, edl.sfx, fade_s=edl.audio_fade_s)
    if edl.music is not None and track is not None:
        if not track.is_file():  # deleted since it was picked
            raise ClipForgeError("music_gone", TRACK_LEFT, retryable=True)
        bed = audio.decode_track(track, len(mixed))
        mixed = audio.mix_music(mixed, bed, under_db=edl.music["db"])
    return audio.finish(mixed, audio.SR, out)


def music_for(conn, clip: ClipRow, style: Style) -> tuple[LibraryItem | None, str | None]:
    """The track to put under the clip, and the note to say when it asked for one and gets none: "auto"
    takes the track whose moods and energy suit the clip best, a named one is used whatever its mood, and
    a track whose file has gone is never used."""
    if style.music is None:
        return None, None
    if style.music == "auto":
        found = best_music([i for i in music_items(conn) if Path(i.path).is_file()], *feel(clip))
        return (found, None) if found is not None else (None, MUSIC_NO_MATCH)
    item = repo.get_library_item(conn, style.music)
    if item is None or item.kind != "music" or not Path(item.path).is_file():
        return None, MUSIC_GONE
    return item, None


VO_MIN_PLAY_S = 0.5  # less of a take than this would not be heard as a voiceover


def voiceover_for(
    pp: ProjectPaths, clip: ClipRow, duration: float
) -> tuple[dict[str, Any] | None, str | None]:
    """The edit list's record of the clip's voiceover (its file, the second of the clip it starts at and its
    length) and the note to say when it runs past the clip's end, its file has gone, or it would start
    after the end or too near it for half a second of it to play."""
    edits = clip.edits or {}
    if not edits.get("voiceover"):
        return None, None
    path = pp.clip_dir(clip.id) / "voiceover" / edits["voiceover"]
    try:
        with wave.open(str(path), "rb") as w:
            length = w.getnframes() / w.getframerate()
    except (OSError, EOFError, wave.Error):
        return None, VO_GONE
    at = float(edits.get("voiceover_at", 0.0))
    if at > duration - VO_MIN_PLAY_S:  # cuts left the clip ending before the take would be heard
        return None, VO_LATE
    record = {"path": str(path), "at": at, "duration_s": round(length, 3)}
    return record, VO_CUT if at + length > duration + 1e-3 else None


def music_record(item: LibraryItem | None, style: Style) -> dict[str, Any] | None:
    """The edit list's record of the track under the speech; its ``credit`` is the line the posts carry,
    there only when the track's licence asks for credit."""
    if item is None:
        return None
    credit = f"Music: {item.name} by {item.credit}" if item.credit else f"Music: {item.name}"
    return {
        "item_id": item.id,
        "title": item.name,
        "artist": item.credit,
        "db": style.music_db,
        "credit": credit if item.credit_required else None,
    }


def _picture(ctx: JobContext, edl: EDL, src: ClipSource, game: ClipSource | None, out: Path, log: Log) -> EDL:
    """Renders the picture with the edit list's encoder, and again with libx264 when VideoToolbox fails
    (SPEC 8.6). Returns the edit list as it was rendered. A cancelled job stops at the next frames."""

    def progress(share: float) -> None:
        if ctx.cancelled():
            raise ClipForgeError("cancelled", CANCELLED)
        ctx.progress(0.1 + 0.8 * share)

    try:
        render_video(edl, src, out, gameplay=game, progress=progress)
        return edl
    except ToolError as err:
        if err.code != "encode_failed" or edl.style.encoder != "fast":
            raise
        log.lines.append(f"VideoToolbox failed ({err}), so the picture was made again with libx264")
    best = edl.model_copy(update={"style": edl.style.model_copy(update={"encoder": "best"})})
    render_video(best, src, out, gameplay=game, progress=progress)
    return best


class Log:
    """What ``render.log`` says about a version: how it was made, and how long each step took."""

    def __init__(self, version: int, clip_id: str, layout: str, reason: str):
        self.head = [
            f"ClipForge render: version {version} of clip {clip_id}, {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Layout: {layout}. {reason}",
        ]
        self.lines: list[str] = []
        self.times: list[tuple[str, float]] = []

    @contextmanager
    def step(self, name: str) -> Iterator[None]:
        start = time.monotonic()
        yield
        self.times.append((name, time.monotonic() - start))

    def text(
        self,
        edl: EDL,
        item: LibraryItem | None,
        sound: audio.AudioReport,
        capped: bool,
        cover_s: float,
        qa: QAResult,
    ) -> str:
        lines = [*self.head, f"Encoder: {ENCODERS[edl.style.encoder]}", *self.lines]
        if item is not None and edl.gameplay:
            start = edl.gameplay["start_s"]
            lines.append(f"Gameplay: {item.name} ({item.id}) from {start:.3f} s, muted")
            lines.append(f"Gameplay credit: {item.credit}; license: {item.license}")
        if edl.music is not None:
            by = f" by {edl.music['artist']}" if edl.music["artist"] else ""
            under = f"{-edl.music['db']:g} dB under the speech"
            lines.append(f"Music: {edl.music['title']}{by} ({edl.music['item_id']}) at {under}")
        if edl.voiceover is not None:
            vo = edl.voiceover
            lines.append(
                f"Voiceover: {Path(vo['path']).name}, {vo['duration_s']:.1f} s from {vo['at']:.1f} s in"
            )
        level = "silent, so left as it was" if sound.silent else f"{sound.input_lufs:.1f} LUFS in"
        lines.append(f"Sound: {level}, {sound.output_lufs:.1f} LUFS out, true peak {sound.true_peak:.1f} dB")
        if capped:
            lines.append("Bitrate: over Instagram's limit, so it was encoded again with libx264 under it")
        lines.append(f"Cover: the frame at {cover_s:.3f} s")
        lines.append("Steps: " + ", ".join(f"{name} {s:.2f} s" for name, s in self.times))
        lines += [f"Note: {note}" for note in edl.notes]
        lines += [f"QA: {problem}" for problem in qa.problems()] or ["QA: no problems"]
        return "\n".join(lines) + "\n"
