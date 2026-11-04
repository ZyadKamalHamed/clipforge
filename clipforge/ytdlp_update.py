"""Updating yt-dlp from the Doctor page or ``clipforge update-ytdlp`` (SPEC 4.1): uv moves the lock file to
the newest yt-dlp, then installs exactly what the lock file says. A step that fails puts the lock file back,
so it still says what is installed. One update runs at a time, and the new version is used once ClipForge
restarts."""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

LIMIT_S = 300  # for each uv step
VERSION_CODE = "import yt_dlp.version; print(yt_dlp.version.__version__)"
NEWER = "yt-dlp is now {after}. Restart ClipForge to use it."
NEWEST = "yt-dlp {before} is already the newest."
OUTSIDE = (
    "ClipForge isn't running from its own folder, so update yt-dlp there: run"
    " uv lock --upgrade-package yt-dlp, then uv sync --frozen."
)
NO_UV = "Updating yt-dlp needs uv. Install it from docs.astral.sh/uv, then try again."
BUSY = "yt-dlp is already updating. Wait for that to finish."
FAILED = "ClipForge couldn't update yt-dlp. Check your internet connection, then try again."
SLOW = "Updating yt-dlp took over 5 minutes, so ClipForge stopped it. Try again later."
# --inexact keeps what the lock file doesn't name, such as the optional Whisper engine.
STEPS = (("lock", "--upgrade-package", "yt-dlp"), ("sync", "--frozen", "--inexact"))
UV_PLACES = ("~/.local/bin/uv", "/opt/homebrew/bin/uv", "~/.cargo/bin/uv")  # off the app's PATH

_one = threading.Lock()


@dataclass(frozen=True)
class UpdateResult:
    ok: bool
    before: str
    after: str
    message: str
    details: str = ""  # what uv said, for "Show details"


def is_checkout(root: Path) -> bool:
    """Whether ``root`` is ClipForge's own folder: its pyproject.toml and the lock file beside it."""
    try:
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8")).get("project", {})
    except (OSError, ValueError):
        return False
    return project.get("name") == "clipforge" and (root / "uv.lock").is_file()


def checkout() -> Path | None:
    """The folder ClipForge runs from, when it is ClipForge's own (and not an installed copy)."""
    root = Path(__file__).resolve().parent.parent
    return root if is_checkout(root) else None


def find_uv() -> str | None:
    found = shutil.which("uv")
    if found:
        return found
    return next((str(p) for p in map(Path, UV_PLACES) if p.expanduser().is_file()), None)


def update_ytdlp(
    *,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    uv: str | None = None,
    root: Path | None = None,
) -> UpdateResult:
    """Runs the two uv steps in ``root`` (ClipForge's own folder by default) and says what changed."""
    root = root if root is not None else checkout()
    if root is None or not is_checkout(root):
        return UpdateResult(False, "", "", OUTSIDE)
    uv = uv or find_uv()
    if uv is None:
        return UpdateResult(False, "", "", NO_UV)
    if not _one.acquire(blocking=False):
        return UpdateResult(False, "", "", BUSY)
    try:
        return _update(run, uv, root)
    finally:
        _one.release()


def _update(run: Callable[..., subprocess.CompletedProcess], uv: str, root: Path) -> UpdateResult:
    python = root / ".venv" / "bin" / "python"
    python = python if python.exists() else Path(sys.executable)
    before = _version(run, python)
    lock = root / "uv.lock"
    saved = lock.read_bytes()
    for step in STEPS:
        try:
            done = run(
                [uv, *step],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=LIMIT_S,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return _undone(lock, saved, before, SLOW, "")
        except OSError as err:
            return _undone(lock, saved, before, FAILED, str(err))
        if done.returncode != 0:
            return _undone(lock, saved, before, FAILED, (done.stderr or done.stdout or "").strip()[-2000:])
    after = _version(run, python)
    message = NEWEST.format(before=before) if after == before else NEWER.format(after=after)
    return UpdateResult(True, before, after, message)


def _undone(lock: Path, saved: bytes, before: str, message: str, details: str) -> UpdateResult:
    """The lock file put back as it was, and the failure."""
    if lock.read_bytes() != saved:
        tmp = lock.with_name(lock.name + ".tmp")
        tmp.write_bytes(saved)
        tmp.replace(lock)
    return UpdateResult(False, before, before, message, details)


def _version(run: Callable[..., subprocess.CompletedProcess], python: Path) -> str:
    """The yt-dlp version ``python`` imports, read in a child so it is the one installed now."""
    try:
        done = run([str(python), "-I", "-c", VERSION_CODE], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    if done.returncode != 0:
        return "unknown"
    return (done.stdout or "").strip() or "unknown"
