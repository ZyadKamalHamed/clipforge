"""Each project's picture on its card (SPEC 14.3): YouTube's own thumbnail, fetched only from YouTube's image
hosts and checked before it is kept, or a frame of a dropped file. Either is saved 640 px wide as
``source/thumb.jpg``. Making one is best-effort: without it the card shows no picture."""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image

from clipforge.assets import fetch_bytes, url_allowed, write_bytes
from clipforge.ingest.urls import file_from_url
from clipforge.jobs.stages import complete_file
from clipforge.media.ffmpeg import QUIET, ffmpeg_bin, run
from clipforge.models import SourceMeta

if TYPE_CHECKING:
    from clipforge.assets import Fetch
    from clipforge.models import Project
    from clipforge.paths import Paths

log = logging.getLogger(__name__)

THUMB_HOSTS = frozenset({"i.ytimg.com", "i9.ytimg.com", "yt3.ggpht.com"})
MAX_BYTES = 2 * 1024 * 1024
MAX_PIXELS = 20_000_000  # checked before decoding, so a small file can't unpack into a huge picture
WIDTH = 640
QUALITY = 85
FRAME_TIMEOUT_S = 60.0


def frame_time(duration_s: float | None) -> float:
    """Where a file's thumbnail frame comes from: a tenth of the way in, at most 30 s."""
    return min(0.1 * (duration_s or 0.0), 30.0)


def ensure_thumb(paths: Paths, project: Project, *, fetch: Fetch | None = None) -> Path | None:
    """The project's thumbnail, made now when it has none yet: once its metadata is saved, from YouTube's
    picture (``fetch`` gets it; None fetches from the web) or a frame of the file. None when it can't be
    made, for whatever reason; the reason goes to the log."""
    pp = paths.project(project.id)
    if pp.thumb.is_file():
        return pp.thumb
    if not pp.meta_json.is_file():
        return None
    try:
        meta = SourceMeta.model_validate_json(pp.meta_json.read_text(encoding="utf-8"))
        if project.source_kind == "file":
            data = _frame(_video(pp.source, project), frame_time(meta.duration_s))
        else:
            url = meta.thumbnail_url
            if url is None or not url_allowed(url, THUMB_HOSTS):
                log.info("no thumbnail for %s: %r isn't on YouTube's image hosts", project.id, url)
                return None
            get = fetch or (lambda u, max_bytes: fetch_bytes(u, max_bytes=max_bytes, hosts=THUMB_HOSTS))
            data = get(url, MAX_BYTES)
        out = io.BytesIO()
        _picture(data).save(out, "JPEG", quality=QUALITY)
        write_bytes(pp.thumb, out.getvalue())
    except Exception as exc:  # a picture that can't be had or read, whatever went wrong
        log.info("no thumbnail for %s: %s: %s", project.id, type(exc).__name__, exc)
        return None
    return pp.thumb


def _video(source: Path, project: Project) -> Path:
    """The file's copy in the project, or the file itself while there is no copy yet."""
    found = complete_file(source, "video")
    if found is not None:
        return found
    original = file_from_url(project.source_url or "")
    if not original.is_file():
        raise FileNotFoundError(f"neither a copy in {source} nor {original}")
    return original


def _frame(video: Path, at: float) -> bytes:
    """The frame at ``at`` seconds as a PNG, already 640 px wide (and turned upright, as ffmpeg does)."""
    args = [ffmpeg_bin(), *QUIET, "-ss", f"{at:.3f}", "-i", str(video.absolute()), "-map", "0:v:0"]
    args += ["-vf", f"scale={WIDTH}:-2", "-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "pipe:1"]
    data = run(args, timeout_s=FRAME_TIMEOUT_S).stdout
    if not data:
        raise ValueError(f"ffmpeg gave no frame at {at:.3f} s")
    return data


def _picture(data: bytes) -> Image.Image:
    """``data`` decoded, in RGB, 640 px wide. Anything over 20,000,000 pixels is refused unread."""
    with Image.open(io.BytesIO(data)) as img:
        w, h = img.size
        if not w or not h or w * h > MAX_PIXELS:
            raise ValueError(f"a {w} x {h} picture is too big to be a thumbnail")
        img.load()
        rgb = img.convert("RGB")
    return rgb.resize((WIDTH, max(1, round(h * WIDTH / w))), Image.Resampling.LANCZOS)
