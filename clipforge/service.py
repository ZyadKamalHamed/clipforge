"""The background service (SPEC 10.7): a LaunchAgent that starts the engine when Zyad logs in and keeps it
running, so the daily run and the scheduled posts don't need ClipForge's window open.

The plist carries only the engine's own environment (``CLIPFORGE_HOME``, ``PATH`` and ``PYTHONUNBUFFERED``),
never the rest of the shell's, so no token in the environment ends up in a file on disk. Every launchctl
call goes through ``RUN``, which tests replace.
"""

from __future__ import annotations

import os
import plistlib
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from clipforge import launcher
from clipforge.errors import ClipForgeError
from clipforge.paths import Paths

LABEL = "com.clipforge.engine"
AGENTS = Path.home() / "Library" / "LaunchAgents"
ENV_KEYS = ("CLIPFORGE_HOME", "PATH", "PYTHONUNBUFFERED")
THROTTLE_S = 30  # launchd waits this long before starting a crashed engine again

RUNNING = "Runs in the background and starts when you log in."
STOPPED = "Installed, but not running now. It starts again when you log in."
OFF = "Not installed, so ClipForge runs from when you open it until you log out or restart the Mac."
INSTALLED = "Installed. ClipForge now starts when you log in."
REMOVED = "Removed. ClipForge no longer starts when you log in."
REMOVED_SELF = "Removed. ClipForge stops now; open it again from the app."
NOT_INSTALLED = "The background service isn't installed."
ALREADY = "ClipForge is already running as the background service."
FAILED = "macOS didn't start the background service (launchctl said {code}). The log has the details."
NO_LAUNCHCTL = "macOS's launchctl isn't on this Mac, so the background service can't be added."
WAITING = "Another ClipForge engine has this home, so the background service takes over once it stops."
HANDOVER_S = 5  # how often the service's engine looks for the home to come free

Run = Callable[[list[str]], subprocess.CompletedProcess]


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=30, check=False)


RUN: Run = _run


@dataclass(frozen=True)
class ServiceStatus:
    installed: bool
    running: bool
    pid: int | None
    sentence: str


def plist_path(agents: Path) -> Path:
    return agents / f"{LABEL}.plist"


def plist(paths: Paths, port: int = launcher.DEFAULT_PORT, python: str = sys.executable) -> dict:
    env = launcher.server_env(paths)
    log = str(paths.logs / "server.log")
    return {
        "Label": LABEL,
        "ProgramArguments": [python, "-m", "clipforge", "serve", "--port", str(port)],
        "EnvironmentVariables": {key: env[key] for key in ENV_KEYS},
        "WorkingDirectory": str(paths.home),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": THROTTLE_S,
        "StandardOutPath": log,
        "StandardErrorPath": log,
        "ProcessType": "Interactive",
    }


def is_self() -> bool:
    """True when this process is the one launchd started for the service."""
    return os.environ.get("XPC_SERVICE_NAME") == LABEL


def _launchctl(run: Run, args: list[str]) -> subprocess.CompletedProcess:
    try:
        return run(["launchctl", *args])
    except OSError as err:
        raise ClipForgeError("no_launchctl", NO_LAUNCHCTL, details=type(err).__name__) from err


def _resolve(agents: Path | None, run: Run | None, uid: int | None) -> tuple[Path, Run, int]:
    return (agents or AGENTS, run or RUN, os.getuid() if uid is None else uid)


def install(
    paths: Paths,
    port: int = launcher.DEFAULT_PORT,
    *,
    agents: Path | None = None,
    run: Run | None = None,
    uid: int | None = None,
    python: str = sys.executable,
) -> str:
    """Write the plist and load it, replacing one already loaded. Run from the service's own engine, it
    changes nothing: booting that engine out would stop the request that asked."""
    agents, run, uid = _resolve(agents, run, uid)
    if is_self():
        return ALREADY
    _launchctl(run, ["bootout", f"gui/{uid}/{LABEL}"])  # any answer: nothing loaded is fine
    agents.mkdir(parents=True, exist_ok=True)
    target = plist_path(agents)
    temp = target.with_name(target.name + ".tmp")
    with temp.open("wb") as fh:
        plistlib.dump(plist(paths, port, python), fh)
    temp.chmod(0o644)
    os.replace(temp, target)
    launcher.server_log(paths)  # make the log folder, and start a fresh log when the old one is big
    done = _launchctl(run, ["bootstrap", f"gui/{uid}", str(target)])
    if done.returncode != 0:
        raise ClipForgeError(
            "service_failed", FAILED.format(code=done.returncode), details=(done.stderr or "").strip()[:500]
        )
    return INSTALLED


def uninstall(
    paths: Paths,
    *,
    agents: Path | None = None,
    run: Run | None = None,
    uid: int | None = None,
    later: Callable[[Callable[[], None]], None] | None = None,
) -> str:
    """Unload the service and delete its plist. From the service's own engine the plist goes first and the
    bootout runs a moment later, so the answer reaches the window before the engine stops."""
    agents, run, uid = _resolve(agents, run, uid)
    target = plist_path(agents)
    if not target.exists():
        return NOT_INSTALLED
    target.unlink()

    def bootout() -> None:
        _launchctl(run, ["bootout", f"gui/{uid}/{LABEL}"])

    if is_self():
        (later or _in_a_second)(bootout)
        return REMOVED_SELF
    bootout()
    return REMOVED


def _in_a_second(action: Callable[[], None]) -> None:
    timer = threading.Timer(1.0, action)
    timer.daemon = True
    timer.start()


_STATE = re.compile(r"^\s*state = (\S+)", re.MULTILINE)
_PID = re.compile(r"^\s*pid = (\d+)", re.MULTILINE)


def status(
    paths: Paths, *, agents: Path | None = None, run: Run | None = None, uid: int | None = None
) -> ServiceStatus:
    agents, run, uid = _resolve(agents, run, uid)
    if not plist_path(agents).exists():
        return ServiceStatus(False, False, None, OFF)
    printed = _launchctl(run, ["print", f"gui/{uid}/{LABEL}"])
    text = printed.stdout or ""
    state = _STATE.search(text)
    if printed.returncode != 0 or not state or state.group(1) != "running":
        return ServiceStatus(True, False, None, STOPPED)
    pid = _PID.search(text)
    return ServiceStatus(True, True, int(pid.group(1)) if pid else None, RUNNING)


def wait_for_home(
    paths: Paths,
    *,
    in_use: Callable[[Paths], bool] = launcher.home_in_use,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Wait while another engine (the window's) holds the home, saying so once. Nothing is open yet, so a
    bootout's SIGTERM ends the wait at once."""
    if not in_use(paths):
        return
    print(WAITING, file=sys.stderr, flush=True)
    while True:
        sleep(HANDOVER_S)
        if not in_use(paths):
            return
