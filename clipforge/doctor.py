"""`clipforge doctor`: checks everything ClipForge needs on this Mac and says how to fix what's missing."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from clipforge import assets, service
from clipforge.brain.llm.claude_cli import find_claude
from clipforge.config import Settings
from clipforge.paths import Paths

CHROME = "Google Chrome.app"  # the browser route drives the installed Chrome (SPEC 10.3)

PARAKEET_REPO = "mlx-community/parakeet-tdt-0.6b-v3"


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""
    required: bool = True


def hf_model_dir(hf_home: Path, repo_id: str) -> Path:
    return hf_home / "hub" / ("models--" + repo_id.replace("/", "--"))


def model_cached(hf_home: Path, repo_id: str) -> bool:
    snapshots = hf_model_dir(hf_home, repo_id) / "snapshots"
    return snapshots.is_dir() and any(snapshots.iterdir())


def js_runtime(which: Callable[[str], str | os.PathLike | None] = shutil.which) -> Path | None:
    """yt-dlp needs a JavaScript runtime for YouTube: the deno that ships in the venv, else one on PATH."""
    bundled = Path(sys.executable).parent / "deno"
    if bundled.exists():
        return bundled
    for name in ("deno", "node"):
        found = which(name)
        if found:
            return Path(found)
    return None


def _free_gb(path: Path) -> float:
    while not path.exists() and path != path.parent:
        path = path.parent
    return shutil.disk_usage(path).free / 1e9


def _claude_version(path: Path) -> str | None:
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    try:
        out = subprocess.run([str(path), "--version"], capture_output=True, text=True, timeout=20, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


BACKGROUND_ON = "Installed: starts when you log in."
BACKGROUND_FIX = "Run clipforge service install to keep ClipForge running after you log in."


def run_checks(
    paths: Paths,
    settings: Settings,
    *,
    which: Callable[[str], str | os.PathLike | None] = shutil.which,
    user_home: Path | None = None,
    apps: Path = Path("/Applications"),
) -> list[Check]:
    checks: list[Check] = []

    py_ok = sys.version_info[:2] == (3, 12)
    checks.append(
        Check(
            "python",
            py_ok,
            platform.python_version(),
            "" if py_ok else "Run uv sync in the clipforge folder.",
        )
    )

    for tool in ("ffmpeg", "ffprobe"):
        found = which(tool)
        checks.append(
            Check(
                tool,
                found is not None,
                str(found) if found else "not found",
                "Install it: brew install ffmpeg",
            )
        )

    try:
        from yt_dlp.version import __version__ as ytdlp_version

        checks.append(Check("yt-dlp", True, ytdlp_version))
    except ImportError:
        checks.append(Check("yt-dlp", False, "not installed", "Run uv sync in the clipforge folder."))

    runtime = js_runtime(which)
    checks.append(
        Check(
            "js runtime",
            runtime is not None,
            str(runtime) if runtime else "no deno or node found",
            "" if runtime else "Run uv sync (it installs deno), or install Node.js. YouTube needs one.",
        )
    )

    claude = find_claude(which, user_home)
    version = _claude_version(Path(claude)) if claude else None
    checks.append(
        Check(
            "claude",
            version is not None,
            f"{version} at {claude}"
            if version
            else ("not found" if not claude else f"{claude} did not answer"),
            ""
            if version
            else "Install Claude Code, then run claude in Terminal and log in. "
            "Until then ClipForge picks clips in Basic mode.",
            required=settings.llm_provider == "claude_cli",
        )
    )

    cached = model_cached(paths.hf_home, PARAKEET_REPO)
    checks.append(
        Check(
            "speech model",
            cached,
            "Parakeet is downloaded" if cached else "Parakeet is not downloaded yet",
            "" if cached else "It downloads by itself on the first run (about 2.5 GB).",
            required=False,
        )
    )

    free = _free_gb(paths.home)
    disk_ok = free >= settings.min_free_gb
    checks.append(
        Check(
            "disk",
            disk_ok,
            f"{free:.0f} GB free (projects need {settings.min_free_gb:.0f} GB)",
            "" if disk_ok else "Free up space, or point CLIPFORGE_HOME at a bigger drive.",
        )
    )

    try:
        import keyring

        backend = keyring.get_keyring()
        name = f"{type(backend).__module__}.{type(backend).__name__}"
        keychain_ok = "macOS" in name
    except Exception as exc:  # keyring raises its own error types when no backend loads
        name, keychain_ok = f"unavailable ({exc})", False
    checks.append(
        Check(
            "keychain",
            keychain_ok,
            name,
            "" if keychain_ok else "Account tokens and API keys are kept in the macOS Keychain.",
            required=False,
        )
    )
    chrome = next(
        (
            app
            for app in (apps / CHROME, (user_home or Path.home()) / "Applications" / CHROME)
            if app.is_dir()
        ),
        None,
    )
    checks.append(
        Check(
            "chrome",
            chrome is not None,
            str(chrome) if chrome else "not found",
            "" if chrome else "Install Google Chrome: TikTok posts go through it.",
            required=False,
        )
    )
    agents = user_home / "Library" / "LaunchAgents" if user_home else service.AGENTS
    installed = service.plist_path(agents).exists()
    checks.append(
        Check(
            "background",
            installed,
            BACKGROUND_ON if installed else service.OFF,
            "" if installed else BACKGROUND_FIX,
            required=False,
        )
    )
    checks.extend(_render_checks(paths, which))
    return checks


def _encoders(ffmpeg: Path) -> set[str]:
    try:
        out = subprocess.run(
            [str(ffmpeg), "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=20
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    return {parts[1] for line in out.stdout.splitlines() if len(parts := line.split()) > 1}


def _render_checks(paths: Paths, which: Callable[[str], str | os.PathLike | None]) -> list[Check]:
    checks: list[Check] = []
    try:
        import cv2

        cv_ok = hasattr(cv2, "FaceDetectorYN")
        checks.append(
            Check("opencv", cv_ok, cv2.__version__, "" if cv_ok else "Run uv sync in the clipforge folder.")
        )
    except ImportError:
        checks.append(Check("opencv", False, "not installed", "Run uv sync in the clipforge folder."))

    ready = assets.model_ready(paths)
    checks.append(
        Check(
            "face model",
            ready,
            "YuNet is downloaded" if ready else "YuNet is not downloaded yet",
            "" if ready else "It downloads by itself the first time a clip renders (0.2 MB).",
            required=False,
        )
    )

    ffmpeg = which("ffmpeg")
    found = _encoders(Path(ffmpeg)) & {"h264_videotoolbox", "libx264"} if ffmpeg else set()
    fast = "h264_videotoolbox" in found
    checks.append(
        Check(
            "video encoder",
            bool(found),
            ", ".join(sorted(found)) or "no H.264 encoder in ffmpeg",
            ""
            if fast
            else "Reinstall ffmpeg with brew reinstall ffmpeg; renders are faster with VideoToolbox.",
        )
    )

    missing = [name for name in assets.FONTS.values() if not (assets.FONTS_DIR / name).is_file()]
    checks.append(
        Check(
            "fonts",
            not missing,
            "all bundled fonts present" if not missing else "missing " + ", ".join(missing),
            "" if not missing else "Reinstall ClipForge; its fonts ship with it.",
        )
    )
    return checks
