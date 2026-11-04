import logging
import sqlite3
import threading
import time

import pytest

from clipforge.config import Settings
from clipforge.db import connect, migrate
from clipforge.errors import NetworkError, ToolError, WaitUntil
from clipforge.events import EventBus
from clipforge.jobs.runner import LANES, JobRunner
from clipforge.paths import Paths


@pytest.fixture
def env(tmp_path):
    paths = Paths(tmp_path / "home")
    paths.ensure()
    conn = connect(paths.db)
    migrate(conn)
    yield paths, conn
    conn.close()


@pytest.fixture
def make(env):
    paths, _ = env
    made = []

    def factory(handlers, **kw):
        kw.setdefault("poll_s", 0.01)
        runner = JobRunner(paths.db, handlers, paths=paths, settings=Settings(), **kw)
        made.append(runner)
        return runner

    yield factory
    for runner in made:
        runner.stop(timeout=2.0)


def run(runner):
    runner.start()
    assert runner.run_until_idle(timeout_s=5)


def drain(sub):
    events = []
    while (event := sub.get(timeout=0)) is not None:
        events.append(event)
    return events


def concurrency_probe(delay=0.1):
    lock = threading.Lock()
    state = {"now": 0, "max": 0}

    def handler(ctx):
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        time.sleep(delay)
        with lock:
            state["now"] -= 1

    return handler, state


def test_lanes():
    assert LANES == {"io": 3, "cpu": 1, "llm": 3, "post": 1, "post_api": 2}


def test_run_until_idle_leaves_standing_kinds_waiting(make):
    runner = make({"post": ("post", lambda ctx: None), "work": ("io", lambda ctx: None)}, standing={"post"})
    later = runner.enqueue("post", "p1", not_before=time.time() + 3600)
    done = runner.enqueue("work", "w1")
    assert runner.run_until_idle(timeout_s=5)
    assert runner.get(done).status == "done"
    assert runner.get(later).status == "queued"
    assert not runner.run_until_idle(timeout_s=0.2, refs=["p1"])  # asked for by its ref, it is waited for


def test_enqueue_not_before_waits(make, env):
    _, conn = env
    started = []
    runner = make({"work": ("post", lambda ctx: started.append(time.time()))})
    at = time.time() + 0.6
    job_id = runner.enqueue("work", "t1", not_before=at)
    assert conn.execute("SELECT not_before FROM jobs WHERE id = ?", (job_id,)).fetchone()[0] == at
    runner.start()
    time.sleep(0.2)
    assert runner.get(job_id).status == "queued" and not started
    assert runner.run_until_idle(timeout_s=5)
    assert runner.get(job_id).status == "done"
    assert started[0] >= at
    now_id = runner.enqueue("work", "t2")
    assert runner.get(now_id).not_before == 0


def test_unknown_kind_and_lane_rejected(make, env):
    paths, _ = env
    with pytest.raises(ValueError):
        JobRunner(paths.db, {"x": ("gpu", lambda ctx: None)}, paths=paths, settings=Settings())
    runner = make({"work": ("cpu", lambda ctx: None)})
    with pytest.raises(ValueError):
        runner.enqueue("nope", "p1")


def test_jobs_run_by_priority_then_fifo(make):
    order = []
    runner = make({"work": ("cpu", lambda ctx: order.append(ctx.job.ref_id))})
    for ref, priority in [("a", 0), ("b", 5), ("c", 0), ("d", 5)]:
        runner.enqueue("work", ref, priority=priority)
    run(runner)
    assert order == ["b", "d", "a", "c"]


def test_cpu_lane_runs_one_at_a_time(make):
    handler, state = concurrency_probe()
    runner = make({"render": ("cpu", handler)})
    runner.enqueue("render", "a")
    runner.enqueue("render", "b")
    run(runner)
    assert state["max"] == 1


def test_io_lane_runs_three_at_a_time(make):
    handler, state = concurrency_probe()
    runner = make({"fetch": ("io", handler)})
    for ref in "abcde":
        runner.enqueue("fetch", ref)
    run(runner)
    assert state["max"] == 3


def test_recover_requeues_running(make, env):
    _, conn = env
    runner = make({"work": ("cpu", lambda ctx: None)})
    jid = runner.enqueue("work", "a")
    conn.execute("UPDATE jobs SET status = 'running', started_at = 1, heartbeat_at = 1 WHERE id = ?", (jid,))
    assert runner.recover() == 1
    assert runner.get(jid).status == "queued"
    assert runner.recover() == 0


def test_retryable_error_requeues_with_backoff(make):
    calls = []

    def flaky(ctx):
        calls.append(time.monotonic())
        if len(calls) == 1:
            raise NetworkError("network_down", "The internet connection dropped.")
        return {"ok": True}

    runner = make({"fetch": ("io", flaky)}, backoff_base_s=0.01)
    jid = runner.enqueue("fetch", "a", max_attempts=3)
    run(runner)
    job = runner.get(jid)
    assert job.status == "done" and job.attempts == 2 and job.result == {"ok": True}
    assert job.error is None and job.error_code is None
    assert calls[1] - calls[0] >= 0.01


def test_retryable_error_fails_when_attempts_run_out(make):
    def down(ctx):
        raise NetworkError("network_down", "The internet connection dropped.")

    runner = make({"fetch": ("io", down)}, backoff_base_s=0.01)
    jid = runner.enqueue("fetch", "a", max_attempts=2)
    run(runner)
    job = runner.get(jid)
    assert job.status == "failed" and job.attempts == 2
    assert job.error == "The internet connection dropped." and job.error_code == "network_down"


def test_plain_error_message_recorded(make):
    def broken(ctx):
        raise ToolError("ffmpeg_failed", "ffmpeg could not read this video.", details="moov atom not found")

    runner = make({"probe": ("cpu", broken)})
    jid = runner.enqueue("probe", "a")
    run(runner)
    job = runner.get(jid)
    assert job.status == "failed" and job.finished_at is not None
    assert job.error == "ffmpeg could not read this video." and job.error_code == "ffmpeg_failed"


def test_unexpected_exception_gets_generic_message(make, caplog):
    def buggy(ctx):
        return {}["missing"]

    runner = make({"brain.select": ("llm", buggy)})
    jid = runner.enqueue("brain.select", "a")
    with caplog.at_level(logging.ERROR, logger="clipforge"):
        run(runner)
    job = runner.get(jid)
    assert job.status == "failed" and job.error_code == "internal"
    assert job.error == "Something went wrong. The log has the details."  # SPEC 18: no job kind in it
    assert job.error_details == "KeyError: 'missing'"
    assert "KeyError" in caplog.text


def test_wait_until_requeues_without_using_attempts(make):
    bus = EventBus()
    sub = bus.subscribe()
    calls = []
    note = "Claude's usage limit is reached. ClipForge will carry on at 3:32 am."

    def limited(ctx):
        calls.append(time.time())
        if len(calls) == 1:
            raise WaitUntil(time.time() + 0.05, note)
        return None

    runner = make({"brain.select": ("llm", limited)}, events=bus)
    jid = runner.enqueue("brain.select", "p1", max_attempts=1)
    run(runner)
    job = runner.get(jid)
    assert job.status == "done" and job.attempts == 1
    assert calls[1] - calls[0] >= 0.05
    waiting = [e for e in drain(sub) if e.data["status"] == "queued" and e.data["message"] == note]
    assert len(waiting) == 1


def test_wake_runs_waiting_jobs_now(make, env):
    _, conn = env
    bus = EventBus()
    sub = bus.subscribe()
    runner = make({"work": ("cpu", lambda ctx: None)}, events=bus)
    waiting, ready, other = (runner.enqueue("work", ref, unique=False) for ref in ("p1", "p1", "p2"))
    later = time.time() + 3600
    conn.execute("UPDATE jobs SET not_before = ? WHERE id IN (?, ?)", (later, waiting, other))
    drain(sub)
    assert runner.wake("p1") == 1
    assert [runner.get(j).not_before for j in (waiting, ready, other)] == [0, 0, later]
    assert [e.data["id"] for e in drain(sub)] == [waiting]
    assert runner.wake("p1") == 0  # nothing of p1's waits now
    runner.cancel(other)
    run(runner)
    assert runner.get(waiting).status == "done"


def test_db_gives_each_thread_its_own_connection(make):
    runner = make({"work": ("cpu", lambda ctx: None)})
    mine = runner.db()
    assert runner.db() is mine
    theirs = []
    thread = threading.Thread(target=lambda: theirs.append(runner.db()))
    thread.start()
    thread.join()
    assert theirs[0] is not mine


def test_close_stops_the_workers_and_closes_this_threads_connection(make):
    runner = make({"work": ("cpu", lambda ctx: None)})
    mine = runner.db()
    runner.enqueue("work", "p1")
    run(runner)
    runner.close()
    assert not [t for t in threading.enumerate() if t.name.startswith("clipforge-")]
    with pytest.raises(sqlite3.ProgrammingError):
        mine.execute("SELECT 1")
    assert runner.db() is not mine  # a later query opens a new one


def test_run_until_idle_can_wait_for_some_refs_only(make, env):
    _, conn = env
    runner = make({"work": ("cpu", lambda ctx: None)})
    mine, other = runner.enqueue("work", "p1"), runner.enqueue("work", "p2")
    conn.execute("UPDATE jobs SET not_before = ? WHERE id = ?", (time.time() + 3600, other))
    runner.start()
    assert runner.run_until_idle(timeout_s=5, refs=["p1"])
    assert runner.get(mine).status == "done" and runner.get(other).status == "queued"
    assert runner.run_until_idle(timeout_s=0.05) is False  # p2 still waits


def test_unique_enqueue_skips_queued_running_and_done(make, env):
    _, conn = env
    runner = make({"work": ("cpu", lambda ctx: None), "other": ("cpu", lambda ctx: None)})
    jid = runner.enqueue("work", "p1")
    assert jid is not None
    assert runner.enqueue("work", "p1") is None
    for status in ("running", "done"):
        conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, jid))
        assert runner.enqueue("work", "p1") is None
    assert runner.enqueue("other", "p1") is not None
    assert runner.enqueue("work", "p2") is not None
    conn.execute("UPDATE jobs SET status = 'failed' WHERE id = ?", (jid,))
    again = runner.enqueue("work", "p1")
    assert again is not None
    assert runner.enqueue("work", "p1", unique=False) not in (None, again)


def test_rerun_allows_done_job(make, env):
    _, conn = env
    runner = make({"work": ("cpu", lambda ctx: None)})
    jid = runner.enqueue("work", "p1")
    conn.execute("UPDATE jobs SET status = 'done' WHERE id = ?", (jid,))
    again = runner.enqueue("work", "p1", rerun=True)
    assert again is not None and again != jid
    assert runner.enqueue("work", "p1", rerun=True) is None  # the rerun itself is queued
    assert [j.id for j in runner.jobs_for("p1")] == [jid, again]
    assert runner.get(10_000) is None


def test_fail_stuck_marks_silent_job_failed(make, env):
    _, conn = env
    finished = []
    runner = make({"work": ("cpu", lambda ctx: None)}, on_finished=finished.append)
    old = runner.enqueue("work", "old")
    fresh = runner.enqueue("work", "fresh")
    now = time.time()
    conn.execute("UPDATE jobs SET status = 'running', heartbeat_at = ? WHERE id = ?", (now - 700, old))
    conn.execute("UPDATE jobs SET status = 'running', heartbeat_at = ? WHERE id = ?", (now - 5, fresh))
    assert runner.fail_stuck(max_silence_s=600) == 1
    stuck = runner.get(old)
    assert stuck.status == "failed" and stuck.error_code == "stuck"
    assert (
        stuck.error
        == "This step went quiet for over 10 minutes, so ClipForge stopped waiting for it. Try again."
    )
    assert runner.get(fresh).status == "running"
    assert [(j.id, j.status) for j in finished] == [(old, "failed")]


def test_job_failed_as_stuck_stays_failed_when_its_handler_returns(make):
    finished = []
    seen = {}

    def slow(ctx):
        time.sleep(0.03)
        seen["stuck"] = seen["runner"].fail_stuck(max_silence_s=0.01)
        seen["cancelled"] = ctx.cancelled()
        return {"late": True}

    runner = make({"work": ("cpu", slow)}, on_finished=finished.append)
    seen["runner"] = runner
    jid = runner.enqueue("work", "p1")
    run(runner)
    runner.stop()  # idle as soon as the job is failed as stuck, so this waits for its handler to return
    job = runner.get(jid)
    assert seen == {"runner": runner, "stuck": 1, "cancelled": True}
    assert job.status == "failed" and job.error_code == "stuck" and job.result is None
    assert [(j.id, j.status) for j in finished] == [(jid, "failed")]


def test_watchdog_fails_a_silent_job_and_frees_its_lane(make):
    release = threading.Event()
    finished = []
    probe, state = concurrency_probe(delay=0.05)

    def silent(ctx):
        release.wait(10)  # a wait that never reports in, as a hung child process would
        return {"late": True}

    runner = make(
        {"silent": ("cpu", silent), "probe": ("cpu", probe)}, on_finished=finished.append, stuck_s=0.2
    )
    hung = runner.enqueue("silent", "p1")
    try:
        run(runner)  # idle once the watchdog gives up on it, though its thread still waits
        job = runner.get(hung)
        assert (job.status, job.error_code) == ("failed", "stuck")
        quick = [runner.enqueue("probe", ref) for ref in ("p2", "p3")]
        run(runner)  # the cpu lane's one worker is still inside silent, so another took its place
        assert [runner.get(j).status for j in quick] == ["done", "done"]
    finally:
        release.set()
    for ref in ("p4", "p5", "p6"):
        runner.enqueue("probe", ref)
    run(runner)
    assert state["max"] == 1  # the worker silent held left once it returned: still one in the lane
    assert (finished[0].id, finished[0].status) == (hung, "failed")


def test_a_long_wait_inside_beating_is_never_stuck(make):
    def long_wait(ctx):
        with ctx.beating():
            time.sleep(0.6)  # silent, as a child process is, but within its own time limit
        return {"ok": True}

    runner = make({"work": ("cpu", long_wait)}, stuck_s=0.2)
    jid = runner.enqueue("work", "p1")
    run(runner)
    assert runner.get(jid).status == "done"


def test_on_finished_and_events(make):
    bus = EventBus()
    sub = bus.subscribe()
    finished = []

    def ok(ctx):
        ctx.progress(0.5, "Halfway there")
        ctx.progress(0.75)
        return {"n": 1}

    def bad(ctx):
        raise ToolError("x_failed", "It broke.")

    runner = make({"ok": ("io", ok), "bad": ("io", bad)}, events=bus, on_finished=finished.append)
    a = runner.enqueue("ok", "p1")
    b = runner.enqueue("bad", "p1")
    run(runner)
    assert sorted((j.id, j.status) for j in finished) == [(a, "done"), (b, "failed")]
    events = drain(sub)
    assert {e.topic for e in events} == {"job"}
    assert set(events[0].data) == {"id", "kind", "ref_id", "project", "status", "progress", "message"}
    mine = [e.data for e in events if e.data["id"] == a]
    assert mine[0]["status"] == "queued" and mine[-1]["status"] == "done"
    assert {"status": "running", "progress": 0.5, "message": "Halfway there"}.items() <= mine[2].items()
    assert (
        mine[3]["progress"] == 0.75 and mine[3]["message"] == "Halfway there"
    )  # no message keeps the last one
    assert runner.get(a).result == {"n": 1} and runner.get(a).progress == 1.0


def test_cancel_queued_job(make):
    ran, finished = [], []
    runner = make({"work": ("cpu", lambda ctx: ran.append(1))}, on_finished=finished.append)
    jid = runner.enqueue("work", "p1")
    runner.cancel(jid)
    run(runner)
    assert ran == [] and runner.get(jid).status == "cancelled"
    runner.cancel(jid)  # cancelling a finished job changes nothing
    assert [(j.id, j.status) for j in finished] == [(jid, "cancelled")]


def test_cancel_running_job_stays_cancelled(make):
    started = threading.Event()
    finished = []

    def slow(ctx):
        started.set()
        deadline = time.monotonic() + 5
        while not ctx.cancelled() and time.monotonic() < deadline:
            time.sleep(0.01)
        return {"finished": True}

    runner = make({"work": ("cpu", slow)}, on_finished=finished.append)
    jid = runner.enqueue("work", "p1")
    runner.start()
    assert started.wait(5)
    runner.cancel(jid)
    assert runner.run_until_idle(timeout_s=5)
    job = runner.get(jid)
    assert job.status == "cancelled" and job.result is None
    assert [(j.id, j.status) for j in finished] == [(jid, "cancelled")]


def test_run_until_idle_waits_for_jobs_enqueued_by_on_finished(make):
    order = []
    holder = {}

    def first(ctx):
        order.append("first")

    def second(ctx):
        order.append("second")

    def chain(job):
        if job.kind == "first":
            time.sleep(0.05)  # the gap between a job finishing and the next stage being queued
            holder["runner"].enqueue("second", job.ref_id)

    runner = make({"first": ("io", first), "second": ("cpu", second)}, on_finished=chain)
    holder["runner"] = runner
    runner.enqueue("first", "p1")
    run(runner)
    assert order == ["first", "second"]


def test_run_until_idle_times_out(make):
    runner = make({"work": ("cpu", lambda ctx: time.sleep(0.3))})
    runner.enqueue("work", "p1")
    runner.start()
    assert runner.run_until_idle(timeout_s=0.05) is False
    assert runner.run_until_idle(timeout_s=5) is True
