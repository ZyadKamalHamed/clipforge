"""Opening ClipForge like an app (SPEC 20). A ClipForge that already answers is used as it is; otherwise
the engine starts in the background, its output added to logs/server.log, and once it answers its window
opens in Chrome. ClipForge.app runs this, so it must also work with the bare PATH an app gets from Finder."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from clipforge.paths import Paths

DEFAULT_PORT = 8723  # the server's own, kept here so opening a window doesn't load the web app
POLL_S = 0.2
WAIT_S = 15.0
NOT_STARTED = "ClipForge didn't start. The log is at {path}."
LOG_KEEP_BYTES = 5_000_000  # a bigger server.log becomes server.log.1 before the next start
# Where Homebrew and the Claude Code and uv installers put their commands, which Finder's PATH lacks.
TOOL_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "~/.local/bin")


def base_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/"


def health(url: str, timeout: float = 0.5) -> dict | None:
    """ClipForge's health answer at ``url``, or None when nothing answers there or another app does."""
    try:
        resp = httpx.get(url.rstrip("/") + "/api/health", timeout=timeout, trust_env=False)
        body = resp.json()
    except (httpx.HTTPError, ValueError):
        return None
    if resp.status_code != 200 or not isinstance(body, dict) or body.get("app") != "clipforge":
        return None
    return body


def open_window(url: str, *, run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> None:
    """ClipForge in a Chrome app window, or in the default browser when Chrome can't be opened."""
    for argv in (["open", "-na", "Google Chrome", "--args", f"--app={url}"], ["open", url]):
        try:
            if run(argv, capture_output=True, text=True, timeout=30, check=False).returncode == 0:
                return
        except (OSError, subprocess.TimeoutExpired):
            pass


def wait_for(
    url: str,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    stopped: Callable[[], bool] = lambda: False,
) -> bool:
    """Whether ClipForge answers at ``url`` within WAIT_S, looking every POLL_S. ``stopped`` ends the wait
    early, for a server that has already exited."""
    deadline = clock() + WAIT_S
    while health(url) is None:
        if stopped() or clock() >= deadline:
            return False
        sleep(POLL_S)
    return True


def open_when_ready(
    url: str,
    *,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """For ``clipforge serve --open``: the window, once the server beside it answers."""
    if wait_for(url, sleep=sleep, clock=clock):
        open_window(url, run=run)


def home_in_use(paths: Paths) -> bool:
    """Whether a ClipForge (a server, or a run in Terminal) holds this home's engine."""
    from clipforge.app import take_home
    from clipforge.errors import ClipForgeError

    paths.ensure()
    try:
        os.close(take_home(paths))
    except ClipForgeError:
        return True
    return False


def server_env(paths: Paths) -> dict[str, str]:
    """The engine's environment: this home, output written as it happens, and the tool folders on PATH."""
    env = dict(os.environ, CLIPFORGE_HOME=str(paths.home), PYTHONUNBUFFERED="1")
    entries = [entry for entry in env.get("PATH", "").split(os.pathsep) if entry]
    extra = [str(Path(d).expanduser()) for d in TOOL_DIRS if str(Path(d).expanduser()) not in entries]
    env["PATH"] = os.pathsep.join(entries + extra)
    return env


def server_log(paths: Paths) -> Path:
    log = paths.logs / "server.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        if log.stat().st_size > LOG_KEEP_BYTES:
            os.replace(log, log.with_name("server.log.1"))
    except FileNotFoundError:
        pass
    return log


def open_app(
    paths: Paths,
    port: int = DEFAULT_PORT,
    *,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    popen: Callable[..., subprocess.Popen] = subprocess.Popen,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """What ClipForge.app does: the window on a ClipForge that answers, starting one first if need be."""
    url = base_url(port)
    if health(url) is None:
        problem = _start(paths, port, url, popen=popen, sleep=sleep, clock=clock)
        if problem:
            print(problem, file=sys.stderr)
            return 1
    open_window(url, run=run)
    return 0


def _start(paths, port, url, *, popen, sleep, clock) -> str | None:
    """Starts the engine, or waits for one already starting, and says why when it doesn't answer."""
    if home_in_use(paths):  # a second click while the first is starting, or a run in Terminal
        if wait_for(url, sleep=sleep, clock=clock):
            return None
        from clipforge.app import BUSY

        return BUSY
    if _port_answers(port):
        from clipforge.server.app import PORT_TAKEN

        return PORT_TAKEN.format(port=port)
    log = server_log(paths)
    with log.open("a", encoding="utf-8") as out:
        proc = popen(
            [sys.executable, "-m", "clipforge", "serve", "--port", str(port)],
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # it keeps running when the app that started it is gone
            env=server_env(paths),
            cwd=paths.home,
        )
    if wait_for(url, sleep=sleep, clock=clock, stopped=lambda: proc.poll() is not None):
        return None
    return NOT_STARTED.format(path=log)


def _port_answers(port: int) -> bool:
    """Whether something accepts connections on the port (ClipForge's own would have answered health)."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False
