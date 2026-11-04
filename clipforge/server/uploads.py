"""Files dropped on the window (SPEC 14.1): the request's body goes straight to disk a few MB at a time,
never all in memory, into a folder of its own under the home's uploads. A drop that can't be taken is
turned away before a byte is written, and one cut off halfway leaves nothing behind."""

from __future__ import annotations

import shutil
import uuid
from typing import TYPE_CHECKING

import anyio.to_thread
from starlette.requests import ClientDisconnect

from clipforge.server.errors import ApiError
from clipforge.server.views import clean_file_name

if TYPE_CHECKING:
    from pathlib import Path

    from starlette.requests import Request

CHUNK = 4 * 1024 * 1024  # how much is gathered before each write
VIDEO_TYPES = frozenset({"mp4", "mov", "mkv", "webm", "m4v"})
NEEDS_LENGTH = "ClipForge needs the file's size before it starts. Try dropping it again."
NOT_A_VIDEO = "That file type isn't a video ClipForge can use. Use MP4, MOV, MKV, WEBM or M4V."
NO_SPACE = "That file needs {size}, which would leave less than {min} GB free. Free up some space first."
CUT = "The file didn't arrive whole. Try dropping it again."


def size_text(n: int) -> str:
    """``n`` bytes as Finder shows a size, in decimal units: "10 KB", "1.5 GB"."""
    value, unit = float(n), "bytes"
    for bigger in ("KB", "MB", "GB", "TB"):
        if value < 999.5:
            break
        value, unit = value / 1000, bigger
    if unit == "bytes":
        return f"{n} byte" if n == 1 else f"{n} bytes"
    shown = f"{value:.1f}".removesuffix(".0") if value < 10 else f"{value:.0f}"
    return f"{shown} {unit}"


async def receive_upload(
    request: Request, dest_dir: Path, name: str, *, allowed: frozenset[str], floor_bytes: int
) -> Path:
    """The request's body saved as ``name`` (cleaned) in a new folder in ``dest_dir``, once all of it
    has arrived. Refused (as an ``ApiError``) without its size (411), with an extension not in ``allowed``
    (415), when it would leave less than ``floor_bytes`` free (507) or when it doesn't arrive whole
    (400), and then nothing of it is left on disk."""
    length = request.headers.get("content-length", "")
    if not (length.isascii() and length.isdigit()):
        raise ApiError(411, "length_required", NEEDS_LENGTH)
    size = int(length)
    clean = clean_file_name(name)
    _, dot, ext = clean.rpartition(".")
    if not dot or ext.lower() not in allowed:
        raise ApiError(415, "not_a_video", NOT_A_VIDEO)
    if _free_bytes(dest_dir) - size < floor_bytes:
        message = NO_SPACE.format(size=size_text(size), min=f"{floor_bytes / 1e9:g}")
        raise ApiError(507, "no_space", message)
    folder = dest_dir / uuid.uuid4().hex
    folder.mkdir(parents=True)
    try:
        part = folder / ".part"
        await _save(request, part, size)
        return part.rename(folder / clean)
    except BaseException:  # cut off, refused or cancelled: nothing of it stays
        shutil.rmtree(folder, ignore_errors=True)
        raise


async def _save(request: Request, part: Path, size: int) -> None:
    """Writes the body to ``part``, off the event loop. A body longer or shorter than ``size``, or one
    the window stopped sending, is ``CUT``."""
    received = 0
    buf = bytearray()
    with part.open("wb") as out:
        try:
            async for chunk in request.stream():
                received += len(chunk)
                if received > size:
                    raise ApiError(400, "upload_cut", CUT)
                buf += chunk
                if len(buf) >= CHUNK:
                    data, buf = buf, bytearray()
                    await anyio.to_thread.run_sync(out.write, data)
        except ClientDisconnect:
            raise ApiError(400, "upload_cut", CUT) from None
        if received != size:
            raise ApiError(400, "upload_cut", CUT)
        await anyio.to_thread.run_sync(out.write, buf)


def _free_bytes(folder: Path) -> int:
    """The free space on the disk ``folder`` is on, or will be once it is made."""
    for place in (folder, *folder.parents):
        if place.exists():
            return shutil.disk_usage(place).free
    return 0
