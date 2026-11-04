"""The child processes ClipForge starts that can run for long (downloads, ffmpeg, the speech worker, the
Claude CLI), kept track of so quitting ClipForge kills them, and their own children, instead of leaving
them running (SPEC 18). Only ClipForge's own children are ever killed: nothing here looks at another
process. One engine runs per process, so the record is the process's."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import weakref
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

_lock = threading.Lock()
_live: weakref.WeakKeyDictionary[subprocess.Popen, int] = weakref.WeakKeyDictionary()  # child -> thread
_closing = threading.Event()
_killed: set[int] = set()  # the threads whose children the close killed


def _kill(proc: subprocess.Popen) -> bool:
    """SIGKILL to the child's process group when it leads one, else to the child. False when it had
    already ended: its pid may be another process's by now, so it is left alone."""
    if proc.poll() is not None:
        return False
    try:
        if os.getpgid(proc.pid) == proc.pid:
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError):
        return False
    return True


def register(proc: subprocess.Popen) -> subprocess.Popen:
    """Track ``proc`` for the thread that started it. Started as ClipForge closes, it is killed at once."""
    with _lock:
        _live[proc] = threading.get_ident()
        closing = _closing.is_set()
    if closing and _kill(proc):
        with _lock:
            _killed.add(threading.get_ident())
    return proc


def unregister(proc: subprocess.Popen) -> None:
    with _lock:
        _live.pop(proc, None)


@contextmanager
def tracked(proc: subprocess.Popen) -> Iterator[subprocess.Popen]:
    """``proc``, tracked until the block ends."""
    register(proc)
    try:
        yield proc
    finally:
        unregister(proc)


def popen(args: Sequence[str], **kw: Any) -> subprocess.Popen:
    """``subprocess.Popen(args, **kw)``, tracked until it is garbage or unregistered."""
    return register(subprocess.Popen(args, **kw))


def run(
    args: Sequence[str],
    *,
    input: Any = None,
    capture_output: bool = False,
    timeout: float | None = None,
    check: bool = False,
    **kw: Any,
) -> subprocess.CompletedProcess:
    """``subprocess.run`` for a tracked child: on a timeout or an error the child (and its group) is
    killed and waited for."""
    if input is not None:
        kw["stdin"] = subprocess.PIPE
    if capture_output:
        kw["stdout"] = kw["stderr"] = subprocess.PIPE
    with tracked(popen(args, **kw)) as proc, proc:
        try:
            out, err = proc.communicate(input, timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill(proc)
            proc.wait()
            raise
        except BaseException:
            _kill(proc)
            raise
        code = proc.poll()
    if check and code:
        raise subprocess.CalledProcessError(code, args, out, err)
    return subprocess.CompletedProcess(args, code, out, err)


def kill_all() -> int:
    """Kills every tracked child still running and refuses new ones until ``reset``; how many it killed."""
    with _lock:
        _closing.set()
        live = list(_live.items())
    killed = [ident for proc, ident in live if _kill(proc)]
    with _lock:
        _killed.update(killed)
    return len(killed)


def closing() -> bool:
    return _closing.is_set()


def was_killed() -> bool:
    """Whether the close killed a child this thread started (a worker thread runs one job at a time)."""
    with _lock:
        return threading.get_ident() in _killed


def reset() -> None:
    """Children may start again: for a runner started after a close, as in tests."""
    with _lock:
        _closing.clear()
        _killed.clear()
