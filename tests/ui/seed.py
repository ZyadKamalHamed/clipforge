"""Projects made straight in the test home's database, as the pipeline leaves them, for the window to show.
Nothing runs for them and nothing is fetched."""

from __future__ import annotations

import io
import itertools
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PIL import Image

from clipforge.models import ProjectOptions
from tests.fakes import make_meta
from tests.unit.test_api_clips import PACKAGING, add_clips, add_render, clip_row, talk

TITLE = "Inside the Mind of a Master Procrastinator"
_numbers = itertools.count(1)


def picture(url: str = "", max_bytes: int = 0) -> bytes:
    """A thumbnail, as YouTube's would be fetched: the shape of a ``Fetch``, so it can stand in for one."""
    out = io.BytesIO()
    Image.new("RGB", (480, 270), (139, 92, 246)).save(out, "JPEG")
    return out.getvalue()


def hook(k: int) -> str:
    """Clip ``k``'s hook title, each clip's its own."""
    return f"Hook {k + 1}: why we wait"


def render_clip(engine, pid: str, cid: str, version: int = 1, *, video: Path | None = None, **render):
    """A render of the clip made current, with real pictures (a placeholder would be a picture Chrome can't
    show) and, with ``video``, a copy of that file as its video so it plays. ``render`` goes to
    ``add_render`` (its ``edits_key``, say)."""
    row = add_render(engine, pid, cid, version, **render)
    for path in (row.cover_path, row.thumb_path):
        if path:
            Path(path).write_bytes(picture())
    if video is not None:
        shutil.copyfile(video, row.path)
    return row


def seed_project(
    engine,
    *,
    clips: int = 6,
    rendered: int = 4,
    title: str = TITLE,
    status: str = "ready",
    channel: str = "TED",
    duration_s: float = 844.0,
    thumb: bool = True,
    video: Path | None = None,
    each: Callable[[int], dict[str, Any]] | None = None,
    **fields,
) -> str:
    """A project with its details, a 120 s transcript, ``clips`` packaged clips best first (the first
    ``rendered`` of them rendered, see ``render_clip``) and a thumbnail. Each clip has its own hook title
    and runs 19.9 s, so none is flagged for its length; ``each(k)`` gives more of clip k's row (its tier,
    score, start or flags). ``fields`` set more of the project (progress, message, error). Each one is a
    video of its own, newer than the last."""
    n = next(_numbers)
    (p,), _ = engine.add_sources(f"https://www.youtube.com/watch?v=seed{n:07d}", ProjectOptions())
    pp = engine.paths.project(p.id)
    pp.ensure()
    tr = talk()
    pp.transcript_json.write_text(tr.model_dump_json())
    pp.meta_json.write_text(make_meta(tr, title=title, channel=channel).model_dump_json())
    if thumb:
        pp.thumb.write_bytes(picture())
    rows = []
    for k in range(clips):
        packaging = PACKAGING.model_copy(update={"hook_title": hook(k)}).model_dump(mode="json")
        fields_k = {"packaging": packaging} | (each(k) if each else {})
        start = fields_k.pop("start_s", 20.0 * k)
        rows.append(clip_row(p.id, f"{p.id}_c{k}", k + 1, start, start + 19.9, **fields_k))
    add_clips(engine, p.id, *rows)
    for row in rows[:rendered]:
        render_clip(engine, p.id, row.id, video=video)
    engine.update_project(p.id, title=title, channel=channel, duration_s=duration_s, status=status, **fields)
    return p.id
