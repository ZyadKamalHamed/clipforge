"""Background jobs in lanes, kept in SQLite so they survive a restart (SPEC 7).

Each lane has a fixed number of worker threads. Whoever moves a job to a final status (done,
failed or cancelled) calls ``on_finished`` for it, exactly once. A watchdog fails the jobs that go quiet
for too long (SPEC 18).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from clipforge import children
from clipforge.config import Settings
from clipforge.db import connect, transaction
from clipforge.errors import INTERNAL, ClipForgeError, WaitUntil
from clipforge.events import EventBus
from clipforge.paths import Paths
from clipforge.untrusted import clean_text

log = logging.getLogger(__name__)

REQUEUED = "status = 'queued', progress = 0, attempts = attempts - 1"  # as if never claimed
LANES: dict[str, int] = {"io": 3, "cpu": 1, "llm": 3, "post": 1, "post_api": 2}
STUCK_S = 600.0  # SPEC 18: a job with no heartbeat for 10 minutes is stuck
STUCK = "This step went quiet for over {span}, so ClipForge stopped waiting for it. Try again."
DETAILS_MAX = 4000  # how much of an exception's text is kept for "Show details"


@dataclass
class Job:
    id: int
    lane: str
    kind: str
    ref_id: str
    status: str  # queued, running, done, failed, cancelled
    priority: int
    progress: float
    message: str | None
    payload: dict[str, Any]
    result: dict[str, Any] | None
    error_code: str | None
    error: str | None
    attempts: int
    max_attempts: int
    not_before: float
    created_at: float
    started_at: float | None
    finished_at: float | None
    heartbeat_at: float | None
    error_details: str | None = None  # the technical side of ``error``

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Job:
        data = dict(row)
        data["payload"] = json.loads(data.pop("payload_json"))
        result = data.pop("result_json")
        data["result"] = None if result is None else json.loads(result)
        return cls(**data)


def job_event(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "kind": job.kind,
        "ref_id": job.ref_id,
        "project": job.payload.get("project") or job.ref_id,  # a clip's job names its project
        "status": job.status,
        "progress": job.progress,
        "message": clean_text(job.message),
    }


def _capped(details: str | None) -> str | None:
    return None if details is None else details[:DETAILS_MAX]


def _load(conn: sqlite3.Connection, job_id: int) -> Job | None:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return Job.from_row(row) if row else None


def _marks(values: list[str] | tuple[str, ...]) -> str:
    return ", ".join("?" for _ in values)


@dataclass
class JobContext:
    job: Job
    conn: sqlite3.Connection  # the worker thread's own connection
    paths: Paths
    settings: Settings
    events: EventBus
    beat_s: float = STUCK_S / 20

    def progress(self, fraction: float, message: str | None = None) -> None:
        """Record progress (0 to 1). Without a message the last one stays."""
        fraction = min(1.0, max(0.0, fraction))
        now = time.time()
        cur = self.conn.execute(
            "UPDATE jobs SET progress = ?, message = COALESCE(?, message), heartbeat_at = ?"
            " WHERE id = ? AND status = 'running'",
            (fraction, message, now, self.job.id),
        )
        self.job.progress, self.job.heartbeat_at = fraction, now
        if message is not None:
            self.job.message = message
        if cur.rowcount:
            self.events.publish("job", job_event(self.job))

    def heartbeat(self) -> None:
        now = time.time()
        self.conn.execute(
            "UPDATE jobs SET heartbeat_at = ? WHERE id = ? AND status = 'running'", (now, self.job.id)
        )
        self.job.heartbeat_at = now

    def cancelled(self) -> bool:
        """True once this job should stop: it was cancelled, or given up on as stuck."""
        row = self.conn.execute("SELECT status FROM jobs WHERE id = ?", (self.job.id,)).fetchone()
        return row is None or row["status"] != "running"

    @contextmanager
    def beating(self) -> Iterator[None]:
        """Heartbeats from a helper thread while the block runs: for one long wait with a time limit of its
        own that can't report progress (a child process, a file copy), so the job isn't taken for stuck."""
        done = threading.Event()
        thread = threading.Thread(
            target=self._beat, args=(done,), name=f"clipforge-beat-{self.job.id}", daemon=True
        )
        thread.start()
        try:
            yield
        finally:
            done.set()
            thread.join()

    def _beat(self, done: threading.Event) -> None:
        conn = connect(self.paths.db)  # the helper thread's own
        try:
            while not done.wait(self.beat_s):
                try:
                    conn.execute(
                        "UPDATE jobs SET heartbeat_at = ? WHERE id = ? AND status = 'running'",
                        (time.time(), self.job.id),
                    )
                except sqlite3.Error:
                    log.warning("could not record a heartbeat for job %s", self.job.id, exc_info=True)
        finally:
            conn.close()


Handler = Callable[[JobContext], dict | None]


class JobRunner:
    def __init__(
        self,
        db_path: Path,
        handlers: dict[str, tuple[str, Handler]],
        *,
        paths: Paths,
        settings: Settings,
        events: EventBus | None = None,
        lanes: dict[str, int] = LANES,
        on_finished: Callable[[Job], None] | None = None,
        poll_s: float = 0.2,
        backoff_base_s: float = 5.0,
        stuck_s: float = STUCK_S,
        standing: Iterable[str] = (),
    ):
        unknown = sorted({lane for lane, _ in handlers.values()} - set(lanes))
        if unknown:
            raise ValueError(f"no workers for lane: {', '.join(unknown)}")
        self.db_path = db_path
        self.handlers = dict(handlers)
        self.paths = paths
        self.settings = settings
        self.events = events if events is not None else EventBus()
        self.lanes = dict(lanes)
        self.on_finished = on_finished
        self.poll_s = poll_s
        self.backoff_base_s = backoff_base_s
        self.stuck_s = stuck_s
        self._kinds = sorted(self.handlers)
        self._waited = sorted(set(self.handlers) - set(standing))  # what run_until_idle waits for unasked
        self._lane_kinds: dict[str, list[str]] = {}
        self._local = threading.local()
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._closing = threading.Event()  # set by close: a job that fails now was killed, not broken
        self._busy_jobs: dict[int, str] = {}  # the ref of each job our workers are inside
        self._busy_lock = threading.Lock()  # guards _busy_jobs and _threads
        self._held: dict[str, int] = {}  # how many holds keep each lane's jobs from starting
        self._claiming = {
            lane: threading.Lock() for lane in self.lanes
        }  # a lane's claims and holds take turns

    # Queries from any thread

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._local.conn = connect(self.db_path)
        return conn

    def db(self) -> sqlite3.Connection:
        """This thread's own connection to the database, opened on first use."""
        return self._conn()

    def get(self, job_id: int) -> Job | None:
        return _load(self._conn(), job_id)

    def jobs_for(self, ref_id: str) -> list[Job]:
        rows = self._conn().execute("SELECT * FROM jobs WHERE ref_id = ? ORDER BY id", (ref_id,))
        return [Job.from_row(r) for r in rows]

    def enqueue(
        self,
        kind: str,
        ref_id: str,
        payload: dict[str, Any] | None = None,
        *,
        priority: int = 0,
        unique: bool = True,
        rerun: bool = False,
        max_attempts: int = 1,
        not_before: float | None = None,
    ) -> int | None:
        """Queue a job, to start at ``not_before`` (epoch seconds) or as soon as a worker is free. With
        ``unique`` a queued, running or done job of the same kind and ref wins."""
        if kind not in self.handlers:
            raise ValueError(f"no handler for job kind {kind!r}")
        blocking = ("queued", "running") if rerun else ("queued", "running", "done")
        conn = self._conn()
        with transaction(conn):
            if unique:
                found = conn.execute(
                    "SELECT 1 FROM jobs WHERE kind = ? AND ref_id = ?"
                    f" AND status IN ({_marks(blocking)}) LIMIT 1",
                    (kind, ref_id, *blocking),
                ).fetchone()
                if found:
                    return None
            cur = conn.execute(
                "INSERT INTO jobs (lane, kind, ref_id, priority, payload_json, max_attempts, not_before,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self.handlers[kind][0],
                    kind,
                    ref_id,
                    priority,
                    json.dumps(payload or {}),
                    max_attempts,
                    not_before or 0.0,
                    time.time(),
                ),
            )
            job_id = cur.lastrowid
        self._changed(conn, job_id)
        return job_id

    def busy(self, refs: Iterable[str]) -> bool:
        """Whether one of our workers is inside a job of one of ``refs``."""
        wanted = set(refs)
        with self._busy_lock:
            return not wanted.isdisjoint(self._busy_jobs.values())

    def delete_jobs(self, refs: Iterable[str]) -> int:
        """Forget every job of ``refs``, as deleting their project does. A handler still inside one sees
        ``ctx.cancelled()``, and its end changes nothing. Gives back how many went."""
        refs = sorted(set(refs))
        if not refs:
            return 0
        return self._conn().execute(f"DELETE FROM jobs WHERE ref_id IN ({_marks(refs)})", refs).rowcount

    def cancel(self, job_id: int) -> None:
        """Cancel a queued or running job. A running handler sees ``ctx.cancelled()`` and stops."""
        conn = self._conn()
        cur = conn.execute(
            "UPDATE jobs SET status = 'cancelled', finished_at = ?"
            " WHERE id = ? AND status IN ('queued', 'running')",
            (time.time(), job_id),
        )
        if cur.rowcount:
            self._finished(self._changed(conn, job_id))

    @contextmanager
    def hold(self, lane: str) -> Iterator[None]:
        """No job of ``lane`` starts while inside; the ones running go on. A claim under way ends first, so
        inside, ``running(lane)`` is every job of the lane that runs until the hold ends."""
        with self._claiming[lane]:
            self._held[lane] = self._held.get(lane, 0) + 1
        try:
            yield
        finally:
            with self._claiming[lane]:
                self._held[lane] -= 1

    def held(self, lane: str) -> bool:
        return self._held.get(lane, 0) > 0

    def running(self, lane: str) -> list[Job]:
        """The jobs of ``lane`` running now."""
        kinds = [kind for kind, (kind_lane, _) in self.handlers.items() if kind_lane == lane]
        if not kinds:
            return []
        rows = self._conn().execute(
            f"SELECT * FROM jobs WHERE status = 'running' AND kind IN ({_marks(kinds)}) ORDER BY id", kinds
        )
        return [Job.from_row(r) for r in rows]

    def wake(self, ref_id: str) -> int:
        """Run this ref's waiting jobs now instead of at their ``not_before``. Gives back how many waited."""
        conn = self._conn()
        now = time.time()
        with transaction(conn):
            ids = [
                r["id"]
                for r in conn.execute(
                    "SELECT id FROM jobs WHERE ref_id = ? AND status = 'queued' AND not_before > ?",
                    (ref_id, now),
                )
            ]
            conn.execute(f"UPDATE jobs SET not_before = 0, message = NULL WHERE id IN ({_marks(ids)})", ids)
        for job_id in ids:
            self._changed(conn, job_id)
        return len(ids)

    def recover(self) -> int:
        """After a restart: jobs left running by the last process go back in the queue."""
        conn = self._conn()
        with transaction(conn):
            ids = [r["id"] for r in conn.execute("SELECT id FROM jobs WHERE status = 'running'")]
            conn.execute(
                "UPDATE jobs SET status = 'queued', started_at = NULL, heartbeat_at = NULL"
                " WHERE status = 'running'"
            )
        for job_id in ids:
            self._changed(conn, job_id)
        return len(ids)

    def fail_stuck(self, max_silence_s: float = STUCK_S) -> int:
        """Fail running jobs that have not reported progress or a heartbeat for ``max_silence_s``. When one
        of our workers is still inside such a job, it is left to it and another takes its place."""
        conn = self._conn()
        now = time.time()
        minutes = max(1, round(max_silence_s / 60))
        message = STUCK.format(span=f"{minutes} minute{'' if minutes == 1 else 's'}")
        with transaction(conn):
            rows = conn.execute(
                "SELECT id, kind, lane FROM jobs"
                " WHERE status = 'running' AND COALESCE(heartbeat_at, started_at, 0) < ?",
                (now - max_silence_s,),
            ).fetchall()
            for row in rows:
                conn.execute(
                    "UPDATE jobs SET status = 'failed', error_code = 'stuck', error = ?,"
                    " error_details = NULL, finished_at = ? WHERE id = ?",
                    (message, now, row["id"]),
                )
        for row in rows:
            log.warning(
                "job %s (%s) was silent for over %.0f s and is marked failed",
                row["id"],
                row["kind"],
                max_silence_s,
            )
            self._finished(self._changed(conn, row["id"]))
            with self._busy_lock:  # only now, so the runner isn't idle before on_finished has run
                ours = self._busy_jobs.pop(row["id"], None) is not None
            if ours:
                self._add_worker(row["lane"])
        return len(rows)

    # Workers

    def start(self) -> None:
        """Start each lane's workers and the watchdog."""
        if self._threads:
            return
        self._stop = threading.Event()
        self._closing.clear()
        for lane, _ in self.handlers.values():
            self._lane_kinds[lane] = sorted(
                k for k, (kind_lane, _) in self.handlers.items() if kind_lane == lane
            )
        threads = [
            threading.Thread(
                target=self._work, args=(kinds, self._stop), name=f"clipforge-{lane}-{n}", daemon=True
            )
            for lane, kinds in self._lane_kinds.items()
            for n in range(self.lanes[lane])
        ]
        threads.append(
            threading.Thread(target=self._watch, args=(self._stop,), name="clipforge-watchdog", daemon=True)
        )
        with self._busy_lock:
            self._threads = threads
        for thread in threads:
            thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        """Let running jobs finish (up to ``timeout``). Any still running are recovered next start."""
        self._stop.set()
        with self._busy_lock:
            threads, self._threads = self._threads, []
        deadline = time.monotonic() + timeout
        for thread in threads:
            thread.join(max(0.0, deadline - time.monotonic()))

    def close(self, timeout: float = 10.0) -> None:
        """Stop taking jobs, kill the children the running ones started, wait for the workers (which put
        those jobs back in the queue), then close this thread's connection. A later query opens a new one."""
        self._closing.set()
        self._stop.set()
        children.kill_all()
        try:
            self.stop(timeout)
        finally:
            children.reset()
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def run_until_idle(
        self, timeout_s: float | None = None, refs: Iterable[str] | Callable[[], Iterable[str]] | None = None
    ) -> bool:
        """Start the workers if needed and wait until none of our jobs is queued or running, or with
        ``refs`` none of theirs; a callable ``refs`` is asked again each time, as jobs add refs. Without
        ``refs`` the standing kinds' jobs, which wait in the queue for their time, are not waited for."""
        pick = refs if callable(refs) else (lambda: refs)
        self.start()
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        while not self._idle(None if (now := pick()) is None else sorted(set(now))):
            if deadline is not None and time.monotonic() >= deadline:
                return False
            time.sleep(self.poll_s)
        return True

    def _idle(self, refs: list[str] | None = None) -> bool:
        # Busy first, then the table: a job between finishing and on_finished queueing the next
        # stage is still busy, and a job claimed after the busy check shows as running.
        with self._busy_lock:
            busy = set(self._busy_jobs.values())
        if busy if refs is None else busy.intersection(refs):
            return False
        kinds = self._kinds if refs is not None else self._waited
        active = (
            f"SELECT COUNT(*) FROM jobs WHERE status IN ('queued', 'running') AND kind IN ({_marks(kinds)})"
        )
        params = list(kinds)
        if refs is not None:
            active += f" AND ref_id IN ({_marks(refs)})"
            params += refs
        return self._conn().execute(active, params).fetchone()[0] == 0

    def _work(self, kinds: list[str], stop: threading.Event) -> None:
        conn = connect(self.db_path)
        self._local.conn = conn  # on_finished and handlers that enqueue use this thread's connection
        try:
            while not stop.is_set():
                try:
                    job = self._claim(conn, kinds)
                except Exception:
                    log.exception("could not claim a job")
                    stop.wait(1.0)
                    continue
                if job is None:
                    stop.wait(self.poll_s)
                    continue
                with self._busy_lock:
                    self._busy_jobs[job.id] = job.ref_id
                try:
                    self._run(conn, job)
                except Exception:
                    log.exception("job %s (%s) could not be recorded as finished", job.id, job.kind)
                finally:
                    with self._busy_lock:
                        given_up = self._busy_jobs.pop(job.id, None) is None
                if given_up:
                    return  # failed as stuck while this worker waited: another took its place
        finally:
            conn.close()

    def _add_worker(self, lane: str) -> None:
        with self._busy_lock:
            if self._stop.is_set() or not self._threads:
                return
            thread = threading.Thread(
                target=self._work,
                args=(self._lane_kinds[lane], self._stop),
                name=f"clipforge-{lane}-{len(self._threads)}",
                daemon=True,
            )
            self._threads.append(thread)
        thread.start()

    def _watch(self, stop: threading.Event) -> None:
        # SPEC 18: about every twentieth of the limit, the jobs silent for longer are failed.
        try:
            while not stop.wait(self.stuck_s / 20):
                try:
                    self.fail_stuck(self.stuck_s)
                except Exception:
                    log.exception("could not check for stuck jobs")
        finally:
            conn = getattr(self._local, "conn", None)
            if conn is not None:
                conn.close()

    def _claim(self, conn: sqlite3.Connection, kinds: list[str]) -> Job | None:
        pick = (
            f"SELECT id FROM jobs WHERE status = 'queued' AND kind IN ({_marks(kinds)}) AND not_before <= ?"
            " ORDER BY priority DESC, created_at, id LIMIT 1"
        )
        now = time.time()
        if conn.execute(pick, (*kinds, now)).fetchone() is None:
            return None  # a plain read first, so idle workers never take the write lock
        lane = self.handlers[kinds[0]][0]
        with self._claiming[lane]:  # so a hold, once taken, sees every job of its lane that will run
            if self._held.get(lane):
                return None
            with transaction(conn):
                row = conn.execute(pick, (*kinds, now)).fetchone()
                if row is None:
                    return None
                conn.execute(
                    "UPDATE jobs SET status = 'running', attempts = attempts + 1, message = NULL,"
                    " started_at = ?, heartbeat_at = ?, finished_at = NULL WHERE id = ?",
                    (now, now, row["id"]),
                )
        return self._changed(conn, row["id"])

    def _run(self, conn: sqlite3.Connection, job: Job) -> None:
        ctx = JobContext(job, conn, self.paths, self.settings, self.events, beat_s=self.stuck_s / 20)
        try:
            result = self.handlers[job.kind][1](ctx)
            result_json = None if result is None else json.dumps(result)
        except WaitUntil as err:
            # Not a failure: run again at err.until and give back the attempt this claim used.
            sets = "status = 'queued', not_before = ?, attempts = attempts - 1, message = ?"
            self._settle(conn, job, sets, (err.until, err.message), final=False)
        except Exception as exc:
            if self._closing.is_set() or children.was_killed():
                # Killed by the close, not broken: it runs again at the next start with its attempt back.
                log.info("job %s (%s) stopped as ClipForge closed: %s", job.id, job.kind, exc)
                self._settle(conn, job, REQUEUED, (), final=False)
            elif isinstance(exc, ClipForgeError):
                self._failed(conn, job, exc)
            else:
                # SPEC 18: the sentence names no job kind; the exception sits behind "Show details".
                log.exception("job %s (%s) failed", job.id, job.kind)
                sets = (
                    "status = 'failed', error_code = 'internal', error = ?, error_details = ?,"
                    " finished_at = ?"
                )
                self._settle(
                    conn, job, sets, (INTERNAL, _capped(f"{type(exc).__name__}: {exc}"), time.time())
                )
        else:
            if self._closing.is_set() and children.was_killed():
                # It finished without what the killed child was making: run it again at the next start.
                self._settle(conn, job, REQUEUED, (), final=False)
                return
            sets = (
                "status = 'done', progress = 1, result_json = ?, error_code = NULL, error = NULL,"
                " error_details = NULL, finished_at = ?"
            )
            self._settle(conn, job, sets, (result_json, time.time()))

    def _failed(self, conn: sqlite3.Connection, job: Job, err: ClipForgeError) -> None:
        """A ClipForgeError: queued again after a backoff when it can be retried, else failed."""
        if err.retryable and job.attempts < job.max_attempts:
            delay = self.backoff_base_s * 3 ** (job.attempts - 1)
            sets = (
                "status = 'queued', progress = 0, not_before = ?, error_code = ?, error = ?,"
                " error_details = ?"
            )
            params = (time.time() + delay, err.code, str(err), _capped(err.details))
            self._settle(conn, job, sets, params, final=False)
            return
        log.warning("job %s (%s) failed: %s: %s", job.id, job.kind, err.code, err.details or err.message)
        sets = "status = 'failed', error_code = ?, error = ?, error_details = ?, finished_at = ?"
        self._settle(conn, job, sets, (err.code, str(err), _capped(err.details), time.time()))

    def _settle(
        self, conn: sqlite3.Connection, job: Job, sets: str, params: tuple, *, final: bool = True
    ) -> None:
        # Only a job that is still running changes: one cancelled or failed as stuck keeps that status.
        cur = conn.execute(f"UPDATE jobs SET {sets} WHERE id = ? AND status = 'running'", (*params, job.id))
        if not cur.rowcount:
            return
        changed = self._changed(conn, job.id)
        if final:
            self._finished(changed)

    def _changed(self, conn: sqlite3.Connection, job_id: int) -> Job:
        job = _load(conn, job_id)
        self.events.publish("job", job_event(job))
        return job

    def _finished(self, job: Job) -> None:
        if self.on_finished is None:
            return
        try:
            self.on_finished(job)
        except Exception:
            log.exception("on_finished failed for job %s (%s)", job.id, job.kind)
