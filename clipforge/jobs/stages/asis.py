"""Post as it is (SPEC 14.2): the project's whole video becomes one ready clip, with no cutting, captions or
hook title. A portrait H.264 video the apps take is copied untouched; any other is fitted to 9:16 over its
own blurred frame and encoded once, a plain change of layout and format. Nothing is done to get past an
app's duplicate checks: no mirroring, speed or pitch change, and the file's metadata is kept."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from clipforge import repo
from clipforge.brain.wording import credit_line
from clipforge.db import transaction
from clipforge.edits import render_key
from clipforge.errors import ToolError
from clipforge.jobs.runner import JobContext
from clipforge.jobs.stages import clips, complete_file, missing, read_meta, write_text
from clipforge.media.ffmpeg import QUIET, MediaInfo, ffmpeg_bin, probe, run
from clipforge.models import ClipRow, Packaging, RenderRow
from clipforge.render.cover import cover_at
from clipforge.render.edl import H_OUT, W_OUT
from clipforge.render.encode import MP4, TO_709, encoder_args, rate, reencode_capped
from clipforge.render.qa import Measured, QAResult, as_is_qa, measure, mp4_problem
from clipforge.render.timeline import output_fps

if TYPE_CHECKING:
    from clipforge.app import Engine

STYLE_KEY = "as_is"  # the render's style: the video's own look
ENCODE_S = 3600  # an encode of a 10 minute video takes minutes, not hours
FIT = (  # the picture whole in the middle, over itself enlarged and blurred to fill 9:16
    f"split[a][b];[a]scale={W_OUT}:{H_OUT}:force_original_aspect_ratio=increase,crop={W_OUT}:{H_OUT},"
    f"boxblur=20:2[bg];[b]scale={W_OUT}:{H_OUT}:force_original_aspect_ratio=decrease:force_divisible_by=2"
    "[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2"
)
SOUND = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
COPIED = "Copied as it is: the picture and sound untouched."
FITTED = "Fitted to 9:16 over its own blurred frame and encoded once with {encoder}."


def make(engine: Engine, ctx: JobContext) -> dict[str, Any]:
    """The project's one clip: its whole video as it is, rendered and ready to post. Made again, it
    takes the place of the one before unless that one has a post."""
    clips.room(engine, ctx)
    pid = ctx.job.ref_id
    project = engine.project(pid)
    pp = engine.paths.project(pid)
    src = complete_file(pp.source, "video")
    if src is None:
        raise missing(pp.source / "video.mp4")
    info = probe(src)
    clip_id = repo.new_id("c")
    folder = pp.clip_dir(clip_id)
    tmp = folder / ".v1.tmp"
    tmp.mkdir(parents=True)
    video = tmp / "clip.mp4"
    try:
        with ctx.beating():
            note = _copy(src, info, video) or _fit(src, info, video, ctx.settings.encoder)
            capped = reencode_capped(video)
            cover_s = cover_at(video, 0, tmp / "cover.jpg", tmp / "thumb.jpg")
            qa = as_is_qa(measure(video))
        made = probe(video)
        write_text(tmp / "qa.json", qa.model_dump_json())
        write_text(tmp / "render.log", _log(note, capped, qa))
        final = pp.render_dir(clip_id, 1)
        os.replace(tmp, final)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    blocked = not qa.passed_for("instagram") and not qa.passed_for("tiktok")
    title = project.title or Path(src).stem
    meta = read_meta(pp)
    packaging = Packaging(
        hook_title=title,
        tiktok_caption=title,
        instagram_caption=title,
        credit=credit_line(meta, project.rights_basis, project.options),
        mode="basic",
    )
    clip = ClipRow(
        id=clip_id,
        project_id=pid,
        rank=1,
        start_s=0.0,
        end_s=made.duration_s,
        mode="as_is",
        status="failed" if blocked else "rendered",
        error=clips.blocker(qa) if blocked else None,
        passes_gates=True,
        packaging=packaging.model_dump(mode="json"),
    )
    render = RenderRow(
        id=repo.new_id("r"),
        clip_id=clip_id,
        version=1,
        style_key=STYLE_KEY,
        edits_key=render_key(clip, {}),
        path=str(final / "clip.mp4"),
        cover_path=str(final / "cover.jpg"),
        thumb_path=str(final / "thumb.jpg"),
        cover_s=cover_s,
        duration_s=made.duration_s,
        size_bytes=(final / "clip.mp4").stat().st_size,
        qa=qa.model_dump(mode="json"),
        status="failed" if blocked else "ready",
    )
    clip = clip.model_copy(update={"current_render_id": render.id})
    with engine.lock:
        with transaction(engine.db()):  # the clip and its video land together
            gone = repo.replace_ideas(engine.db(), pid, [clip])
            repo.add_render(engine.db(), render)
        clips.forget(engine, pid, gone)
    engine.events.publish("clips", {"project_id": pid})
    return {"clip_id": clip_id, "render_id": render.id, "copied": note == COPIED, "problems": qa.problems()}


def _copy(src: Path, info: MediaInfo, out: Path) -> str | None:
    """``src`` copied untouched into ``out`` when it is portrait and the apps take its picture and sound
    as they are, with its index first and no edit list; None when it isn't, with nothing left in ``out``."""
    if info.width > info.height or not _formats_ok(info):
        return None
    args = [ffmpeg_bin(), *QUIET, "-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", *MP4]
    run([*args, str(out)], timeout_s=ENCODE_S)
    if mp4_problem(out) is not None:
        out.unlink()
        return None
    return COPIED


def _formats_ok(info: MediaInfo) -> bool:
    """The picture is H.264 yuv420p at 23 to 60 fps and the sound, if any, AAC the apps take."""
    qa = as_is_qa(Measured(info=info, first_luma=None, peak_bps=0.0, layout=None))
    return all(c.ok for c in qa.checks if c.name in ("codec", "fps_range", "audio_format"))


def _fit(src: Path, info: MediaInfo, out: Path, encoder: str) -> str:
    """``src`` fitted to 1080 x 1920 over its own blurred frame, in H.264 and AAC; VideoToolbox when it
    fails is done again with libx264."""
    fps = output_fps(info.nominal_fps or info.fps or 30.0, False)
    try:
        _encode(src, out, fps, encoder)
    except ToolError:
        if encoder == "best":
            raise
        out.unlink(missing_ok=True)
        encoder = "best"
        _encode(src, out, fps, encoder)
    return FITTED.format(encoder=clips.ENCODERS[encoder])


def _encode(src: Path, out: Path, fps: float, encoder: str) -> None:
    args = encoder_args(encoder, fps)
    args[args.index("-vf") + 1] = f"{FIT},{TO_709}"
    head = [ffmpeg_bin(), *QUIET, "-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", "-r", rate(fps)]
    run([*head, *args, *SOUND, str(out)], timeout_s=ENCODE_S)


def _log(note: str, capped: bool, qa: QAResult) -> str:
    lines = [note]
    if capped:
        lines.append("Encoded again with libx264 under Instagram's 25 Mbps limit.")
    lines += [f"QA: {problem}" for problem in qa.problems()] or ["QA: passed."]
    return "\n".join(lines) + "\n"
