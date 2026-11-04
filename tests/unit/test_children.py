"""The child processes ClipForge starts (SPEC 18): each long one is tracked, so quitting kills it and its
own children, and a job the quit killed runs again at the next start with its attempt given back."""

import os
import re
import signal
import subprocess
import threading
import time
from pathlib import Path

import pytest

from clipforge import children
from clipforge.config import Settings
from clipforge.db import connect, migrate
from clipforge.jobs.runner import JobRunner
from clipforge.paths import Paths

ROOT = Path(children.__file__).parent


@pytest.fixture(autouse=True)
def fresh():
    yield
    children.kill_all()
    children.reset()


def gone(pid: int, wait_s: float = 5.0) -> bool:
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def test_kill_all_kills_process_groups():
    # A shell in a session of its own with a sleep of its own: the kill takes both.
    proc = children.popen(
        ["/bin/sh", "-c", "sleep 30 & echo $!; wait"],
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    grandchild = int(proc.stdout.readline())
    assert children.kill_all() == 1
    assert children.closing()
    assert proc.wait(5) == -signal.SIGKILL
    assert gone(grandchild)
    proc.stdout.close()


def test_a_child_without_a_session_of_its_own_is_killed_alone():
    proc = children.popen(["sleep", "30"])
    assert children.kill_all() == 1
    assert proc.wait(5) == -signal.SIGKILL


def test_finished_children_are_left_alone():
    children.run(["true"])
    proc = children.popen(["true"])
    proc.wait()
    assert children.kill_all() == 0


def test_a_child_started_while_closing_is_killed_at_once():
    children.kill_all()
    started = time.monotonic()
    assert children.run(["sleep", "30"], timeout=10).returncode == -signal.SIGKILL
    assert time.monotonic() - started < 5
    assert children.was_killed()


def test_run_works_like_subprocess_run():
    done = children.run(
        ["/bin/sh", "-c", "cat; echo oops >&2; exit 3"], input="hi", capture_output=True, text=True
    )
    assert (done.returncode, done.stdout, done.stderr) == (3, "hi", "oops\n")
    with pytest.raises(subprocess.CalledProcessError):
        children.run(["false"], check=True)
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        children.run(["sleep", "30"], timeout=0.2)
    assert time.monotonic() - started < 5
    assert children.kill_all() == 0  # nothing was left behind
    assert not children.was_killed()


def test_close_requeues_jobs_killed_by_the_close(tmp_path):
    paths = Paths(tmp_path / "home")
    paths.ensure()
    conn = connect(paths.db)
    migrate(conn)
    started = threading.Event()

    def slow(ctx):
        started.set()
        done = children.run(["sleep", "30"])
        raise RuntimeError(f"sleep ended with {done.returncode}")

    runner = JobRunner(paths.db, {"slow": ("io", slow)}, paths=paths, settings=Settings(), poll_s=0.01)
    job_id = runner.enqueue("slow", "r1")
    runner.start()
    assert started.wait(5)
    began = time.monotonic()
    runner.close(timeout=5)
    assert time.monotonic() - began < 5
    job = runner.get(job_id)
    assert (job.status, job.attempts, job.error_code) == ("queued", 0, None)
    assert not children.closing()  # a later start can run children again
    runner.close()
    conn.close()


def test_every_long_child_is_tracked():
    """Only children.py starts processes, except the Doctor's version checks and the background service's
    launchctl calls, which end in seconds. A bootout stops the engine that asked for it, so its launchctl
    must not be one of the children the engine kills as it closes."""
    popen, run = set(), set()
    for path in sorted(ROOT.rglob("*.py")):
        if path.name == "children.py":
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"\bPopen\(", text):
            popen.add(str(path.relative_to(ROOT)))
        if re.search(r"\bsubprocess\.(run|call|check_call|check_output)\(", text):
            run.add(str(path.relative_to(ROOT)))
    assert popen == set()
    assert run == {"doctor.py", "service.py"}
