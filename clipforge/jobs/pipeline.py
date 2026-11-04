"""The pipeline (SPEC 7): its stages in order, which stage can run once which have finished, and the project's
status, stage and progress as its jobs move on. Every stage is a job on the runner; each time one ends,
``advance`` queues the stages it unblocks."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, NamedTuple

from clipforge import repo
from clipforge.config import Settings
from clipforge.edits import changes_review, review_key
from clipforge.errors import WaitUntil
from clipforge.ingest import ytdlp
from clipforge.jobs import share
from clipforge.jobs.runner import Handler, Job, JobContext, JobRunner
from clipforge.jobs.stages import asis, asr, brain, clips, ingest, review_clip, signals
from clipforge.models import Project
from clipforge.paths import Paths

if TYPE_CHECKING:
    from clipforge.app import Engine


class Stage(NamedTuple):
    kind: str
    lane: str
    after: tuple[str, ...]  # the stages it waits on
    required: bool  # when an optional stage fails, the project carries on without it


class Flow(NamedTuple):
    """A project's pipeline: its stages in order, each one's share of the progress bar, and the stage
    after which the project is ready."""

    stages: tuple[Stage, ...]
    weights: dict[str, float]
    last: str


STAGES = (
    Stage("ingest.metadata", "io", (), True),
    Stage("ingest.audio", "io", ("ingest.metadata",), True),
    Stage("ingest.captions", "io", ("ingest.metadata",), False),
    Stage("ingest.extras", "io", ("ingest.metadata",), False),  # a chat replay can take 10 minutes
    Stage("ingest.video", "io", ("ingest.metadata",), False),  # queued only when the metadata says so
    Stage("asr.transcribe", "cpu", ("ingest.audio", "ingest.captions"), True),
    Stage("signals.compute", "cpu", ("asr.transcribe", "ingest.extras"), True),
    Stage("brain.select", "llm", ("signals.compute",), True),
    Stage("brain.package", "llm", ("brain.select",), True),
)
BY_KIND = {s.kind: s for s in STAGES}
WEIGHTS = {  # each stage's share of the progress bar; the video downloads in the background
    "ingest.metadata": 0.02,
    "ingest.audio": 0.10,
    "ingest.captions": 0.01,
    "ingest.extras": 0.02,
    "asr.transcribe": 0.35,
    "signals.compute": 0.05,
    "brain.select": 0.30,
    "brain.package": 0.15,
}
REFUSED_CODES = frozenset({*ytdlp.REFUSED, "too_long", "no_audio", "no_length", "no_speech"})
LAST = "brain.package"  # the project is ready after it; each clip then renders in jobs of its own
FLOW = Flow(STAGES, WEIGHTS, LAST)
# Post as it is (SPEC 14.2): the video is the clip, so it must download, and nothing listens to it or cuts it
AS_IS = Flow(
    (
        BY_KIND["ingest.metadata"],
        Stage("ingest.video", "io", ("ingest.metadata",), True),
        Stage("asis.make", "cpu", ("ingest.video",), True),
    ),
    {"ingest.metadata": 0.05, "ingest.video": 0.75, "asis.make": 0.20},
    "asis.make",
)
TRIES = {"io": 3}  # SPEC 7.2: network trouble is tried three times, with backoff
BRAIN = ("brain.select", "brain.package")
STOPPED = "This step was stopped before it finished."
# Each clip's own jobs, in order; their ref is the clip id.
CLIP_STAGES = (
    Stage("clip.source", "io", (), True),
    Stage("clip.analyze", "cpu", ("clip.source",), True),
    Stage("clip.render", "cpu", ("clip.analyze",), True),
)
CLIP_FUNCTIONS = {
    "clip.source": clips.clip_source,
    "clip.analyze": clips.clip_analyze,
    "clip.render": clips.clip_render,
}
CLIP_STATUS = {"clip.render": "rendering"}  # a clip's status while each of its jobs runs; else analyzing
REVIEW = Stage("clip.review", "llm", (), True)  # an edited clip checked again, beside its render (SPEC 14.4)
# An account's own version of a clip, for its posts (SPEC 11); its ref is "<clip id>/<account id>"
VARIANT = Stage("clip.variant", "cpu", (), True)
# A render whose gameplay, music or voiceover left the library as it started runs once more, as the
# sentence it stops with says; nothing else a render raises can be tried again.
RENDER_TRIES = {"clip.render": 2, VARIANT.kind: 2}
USER_PRIORITY = 2000  # a render the user asks for goes ahead of the clips rendering on their own
POST_KINDS = {"post.browser": "post", "post.api": "post_api"}  # a post's job and its lane (SPEC 10.7)
# An account's login and self-test open its Chrome window, so they share the browser posts' lane
ACCOUNT_KINDS = {"account.login": "post", "account.selftest": "post"}
WATCH_KINDS = {"watch.check": "io"}  # a clipping channel's check (SPEC 10.9)
LIBRARY_KINDS = {"library.starter": "io"}  # a starter gameplay video's download (SPEC 12)
WATCHED_PRIORITY = -5000  # a clipping channel's projects wait behind anything started by hand
RENDER_COUNTS = {"top5": 5, "top10": 10, "all": None, "none": 0}  # SPEC 7.11 auto-render
STAGE_FUNCTIONS: dict[str, Callable[[Engine, JobContext], dict[str, Any] | None]] = {
    "ingest.metadata": ingest.metadata,
    "ingest.audio": ingest.audio,
    "ingest.captions": ingest.captions,
    "ingest.extras": ingest.extras,
    "ingest.video": ingest.video,
    "asr.transcribe": asr.transcribe,
    "signals.compute": signals.compute,
    "brain.select": brain.select,
    "brain.package": brain.package,
    "asis.make": asis.make,
}


def flow_for(project: Project) -> Flow:
    """The pipeline ``project`` runs: Post as it is's short one, or the one that finds clips."""
    return AS_IS if project.options.as_is else FLOW


class _State:
    """A project's jobs as the pipeline sees them. A stage's current job is its newest one, unless a stage
    it waits on has run again since that job was queued: then it has none, and runs again in its turn."""

    def __init__(self, jobs: list[Job], flow: Flow = FLOW):
        self.flow = flow
        self.by_kind = {stage.kind: stage for stage in flow.stages}
        self.newest = {job.kind: job for job in jobs if job.kind in self.by_kind}  # jobs come oldest first
        self._current: dict[str, Job | None] = {}

    def current(self, kind: str) -> Job | None:
        if kind not in self._current:
            job = self.newest.get(kind)
            for before in self.by_kind[kind].after:
                prior = self.current(before)
                if job is not None and (prior is None or prior.id > job.id):
                    job = None
            self._current[kind] = job
        return self._current[kind]

    def finished(self, kind: str) -> bool:
        """Done, or for an optional stage also failed or cancelled: either way its turn is over."""
        job = self.current(kind)
        if job is None:
            return False
        optional = not self.by_kind[kind].required
        return job.status == "done" or (optional and job.status in ("failed", "cancelled"))

    def stopped(self) -> tuple[Stage, Job] | None:
        """The first required stage whose current job failed or was cancelled."""
        for stage in self.flow.stages:
            job = self.current(stage.kind)
            if stage.required and job is not None and job.status in ("failed", "cancelled"):
                return stage, job
        return None

    def can_queue(self, stage: Stage) -> bool:
        if self.current(stage.kind) is not None or not all(self.finished(k) for k in stage.after):
            return False
        if stage.kind == "ingest.video" and not stage.required:  # wanted only when the metadata says so
            meta = self.current("ingest.metadata")
            return bool(meta and meta.result and meta.result.get("video"))
        return True

    def waiting_on(self) -> str | None:
        """What the project waits on: the first required stage not finished, or an optional stage that
        one still waits for."""
        for stage in self.flow.stages:
            if stage.required and not self.finished(stage.kind):
                return next((k for k in stage.after if not self.finished(k)), stage.kind)
        return None

    def progress(self) -> float:
        return round(sum(weight for kind, weight in self.flow.weights.items() if self.finished(kind)), 4)


def current_jobs(jobs: list[Job], flow: Flow = FLOW) -> dict[str, Job | None]:
    """Each of ``flow``'s stages' current job, as the pipeline sees them (oldest first in, as ``jobs_for``
    gives them)."""
    state = _State(jobs, flow)
    return {stage.kind: state.current(stage.kind) for stage in flow.stages}


def _set(engine: Engine, project: Project, **fields: Any) -> None:
    """Writes only the fields that change, so nothing is published when nothing moved."""
    changed = {name: value for name, value in fields.items() if getattr(project, name) != value}
    if changed:
        engine.update_project(project.id, **changed)


def _priority(project: Project) -> int:
    return WATCHED_PRIORITY if project.clip_channel_id else 0


def _queue(
    runner: JobRunner, stage: Stage, project_id: str, payload: dict | None = None, *, priority: int = 0
) -> int | None:
    return runner.enqueue(
        stage.kind,
        project_id,
        payload,
        priority=priority,
        rerun=True,
        max_attempts=TRIES.get(stage.lane, 1),
    )


def advance(engine: Engine, project_id: str) -> None:
    """Queues every stage whose turn has come and brings the project's status, stage and progress up to
    date: ``failed`` (or ``refused``, for a source that can't be clipped) with the job's message when a
    required stage failed, ``ready`` once its flow's last stage is done. Otherwise the status is left as the
    stages set it."""
    with engine.lock:
        project = engine.project(project_id)
        if project is None:
            return
        flow = flow_for(project)
        state = _State(engine.runner.jobs_for(project_id), flow)
        stop = state.stopped()
        if stop is not None:
            stage, job = stop
            code = job.error_code or "cancelled"
            status = "refused" if code in REFUSED_CODES else "failed"
            error = job.error or STOPPED
            details = job.error_details
            _set(
                engine,
                project,
                status=status,
                stage=stage.kind,
                error_code=code,
                error=error,
                error_details=details,
            )
            return
        priority = _priority(project)
        queued = [
            _queue(engine.runner, stage, project_id, priority=priority)
            for stage in flow.stages
            if state.can_queue(stage)
        ]
        if any(job_id is not None for job_id in queued):
            state = _State(engine.runner.jobs_for(project_id), flow)
        if state.finished(flow.last):
            was_ready = project.status == "ready"
            _set(engine, project, status="ready", stage=None, progress=1.0)
            if not was_ready and flow is FLOW:  # an as-is clip is its video, rendered already
                render_queue(engine, project_id)
        else:
            _set(engine, project, stage=state.waiting_on(), progress=state.progress())


def start(engine: Engine, project_id: str) -> None:
    """Sets the project going: from the beginning, or, for one that stopped, from the stage that failed."""
    with engine.lock:
        project = engine.project(project_id)
        if project is None:
            return
        if project.status in ("failed", "refused"):
            stop = _State(engine.runner.jobs_for(project_id), flow_for(project)).stopped()
            if stop is not None:
                _queue(engine.runner, stop[0], project_id, priority=_priority(project))
            engine.update_project(
                project_id, status="queued", error=None, error_code=None, error_details=None
            )
        advance(engine, project_id)


def queue_rerun(
    runner: JobRunner, conn: sqlite3.Connection, project_id: str, *, provider: str | None = None
) -> bool:
    """Queues the clips to be made again from the selection on, with ``provider`` from now on when given.
    It only changes the database, so it works from a second window too: the one with ClipForge open runs
    the jobs. A selection or packaging still queued, such as one waiting out a usage limit, is woken up
    instead. False when nothing was queued: one is running, or the pipeline hasn't got that far yet."""
    project = repo.get_project(conn, project_id)
    if project is None:
        return False
    if provider is not None:
        repo.update_project(
            conn, project_id, options=project.options.model_copy(update={"provider": provider})
        )
    jobs = runner.jobs_for(project_id)
    brain_jobs = [job for job in jobs if job.kind in BRAIN]
    if any(job.status == "running" for job in brain_jobs):
        return False
    if any(job.status == "queued" for job in brain_jobs):
        runner.wake(project_id)
    elif _State(jobs).finished("signals.compute"):
        _queue(runner, BY_KIND["brain.select"], project_id, {"rerun": True}, priority=_priority(project))
    else:
        return False
    repo.update_project(
        conn, project_id, status="queued", message=None, error=None, error_code=None, error_details=None
    )
    return True


def handlers(engine: Engine) -> dict[str, tuple[str, Handler]]:
    """Every stage's lane and job handler, for the engine's runner."""
    own = {stage.kind: (stage.lane, _handler(engine, stage.kind)) for stage in (*STAGES, *AS_IS.stages)}
    clip = {stage.kind: (stage.lane, _clip_handler(engine, stage)) for stage in CLIP_STAGES}
    post = {kind: (lane, _post_handler(engine)) for kind, lane in POST_KINDS.items()}
    account = {kind: (lane, _account_handler(engine)) for kind, lane in ACCOUNT_KINDS.items()}
    watch = {kind: (lane, _watch_handler(engine)) for kind, lane in WATCH_KINDS.items()}
    library = {kind: (lane, _library_handler(engine)) for kind, lane in LIBRARY_KINDS.items()}
    review = {REVIEW.kind: (REVIEW.lane, _review_handler(engine))}
    variant = {VARIANT.kind: (VARIANT.lane, _variant_handler(engine))}
    return own | clip | review | variant | post | account | watch | library


def _post_handler(engine: Engine) -> Handler:
    from clipforge.post import queue  # the queue reads this module's kinds

    def run(ctx: JobContext) -> None:
        queue.run_post(engine, ctx)

    return run


def _account_handler(engine: Engine) -> Handler:
    from clipforge.post import accounts  # the accounts module reads this module's kinds

    def run(ctx: JobContext) -> dict[str, Any] | None:
        return accounts.run_job(engine, ctx)

    return run


def _watch_handler(engine: Engine) -> Handler:
    from clipforge.watch import check  # it starts projects through the engine, which needs this module

    def run(ctx: JobContext) -> dict[str, Any] | None:
        with ctx.beating():  # a check waits between YouTube requests, so it can outlast STUCK_S
            done = check.run_check(engine, ctx.job.payload["check"])
        return {"found": done.found, "clipped": done.clipped}

    return run


def _library_handler(engine: Engine) -> Handler:
    from clipforge.library import starter  # it queues its downloads at this module's priority

    def run(ctx: JobContext) -> dict[str, Any] | None:
        return starter.download_starter(engine, ctx)

    return run


def _handler(engine: Engine, kind: str) -> Handler:
    fn = STAGE_FUNCTIONS[kind]

    def run(ctx: JobContext) -> dict[str, Any] | None:
        if ctx.cancelled():  # cancelled as it was picked up, as deleting its project does
            return None
        pid = ctx.job.ref_id
        project = engine.project(pid)
        stages = (FLOW if project is None else flow_for(project)).stages
        required = any(s.kind == kind and s.required for s in stages)  # the video is required as it is
        if required:
            engine.update_project(pid, status="running", stage=kind)
        try:
            if kind in share.WAITS_ON:
                share.borrow(engine, ctx.job)  # another project of the video's files, or a wait for them
            return fn(engine, ctx)
        except WaitUntil as err:  # a usage limit, or that wait: the runner queues the job again for then
            if required:
                engine.update_project(pid, status="waiting", message=err.message)
            raise

    return run


def queue_only_runner(paths: Paths, settings: Settings) -> JobRunner:
    """A runner for a second window, which only queues jobs: the window with ClipForge open runs them."""

    def never(ctx: JobContext) -> None:
        raise RuntimeError("this runner only queues jobs")

    return JobRunner(
        paths.db,
        {stage.kind: (stage.lane, never) for stage in (*STAGES, *AS_IS.stages, *CLIP_STAGES, REVIEW)},
        paths=paths,
        settings=settings,
    )


def on_finished(engine: Engine, job: Job) -> None:
    """The runner's hook: a clip's job moves its clip on, any other job its project. A re-check moves
    nothing on: one that failed or was stopped notes that the clip went unchecked, and one that read edits
    changed since is queued again for them. After a job of a clipping channel's project or its clips, the
    check that started it may make its drafts."""
    if job.kind in CLIP_FUNCTIONS:
        clip_advance(engine, job.ref_id)
        _settle(engine, (job.payload or {}).get("project"))
        return
    if job.kind in POST_KINDS:
        from clipforge.post import queue

        queue.finished(engine, job)
        return
    if job.kind == VARIANT.kind:
        return  # the post waiting on it looks again when it next wakes
    if job.kind in ACCOUNT_KINDS:
        from clipforge.post import accounts

        accounts.finished(engine, job)
        return
    if job.kind in WATCH_KINDS:
        from clipforge.watch import check

        check.finished(engine, job)
        return
    if (
        job.kind in LIBRARY_KINDS
    ):  # a download belongs to no project, and its job's own event tells the window
        return
    if job.kind == REVIEW.kind:
        if job.status in ("failed", "cancelled"):
            review_clip.skipped(engine, job.ref_id)
        elif job.status == "done" and (job.result or {}).get("stale"):
            with engine.lock:
                queue_review(engine.runner, engine.db(), job.ref_id, engine.settings)
        _settle(engine, (job.payload or {}).get("project"))
        return
    if job.kind in BRAIN and job.status == "done":
        engine.events.publish("clips", {"project_id": job.ref_id})  # new ideas, or new titles for them
    advance(engine, job.ref_id)
    _settle(engine, job.ref_id)


def _settle(engine: Engine, project_id: str | None) -> None:
    if project_id:
        from clipforge.watch import drafts

        drafts.settle(engine, project_id)


def _render_count(engine: Engine, project: Project) -> int | None:
    """How many of the project's clips render on their own (None: all of them). A clipping channel's
    project renders at least as many as one platform's accounts take from it in a day, whatever Settings
    or the project say, because its drafts are made from renders."""
    count = RENDER_COUNTS[engine.settings.auto_render] if project.options.render else 0
    if project.clip_channel_id is None or count is None:
        return count
    from clipforge.watch import drafts
    from clipforge.watch import store as watch_store

    channel = watch_store.get_channel(engine.db(), project.clip_channel_id)
    return count if channel is None else max(count, drafts.day_room(engine, channel))


def render_queue(engine: Engine, project_id: str) -> list[str]:
    """Queues the clips that render on their own: the top 5, the top 10 or all of those that pass the
    gates and weren't discarded, in rank order (Settings auto_render), or none, or none at all for a
    --no-render project; a clipping channel's project renders what its drafts need either way. Returns
    the clips chosen."""
    with engine.lock:
        project = engine.project(project_id)
        count = None if project is None else _render_count(engine, project)
        if project is None or count == 0:
            return []
        kept = [c for c in engine.clips(project_id) if c.discarded_at is None]
        ranked = sorted((c for c in kept if c.passes_gates and c.rank is not None), key=lambda c: c.rank)
        chosen = ranked if count is None else ranked[:count]
        for clip in chosen:
            _queue_clip(engine, CLIP_STAGES[0], clip.id, project_id, 1000 - clip.rank + _priority(project))
            if clip.status in ("idea", "packaged"):
                engine.update_clip(clip.id, status="queued")
        return [clip.id for clip in chosen]


def clip_advance(engine: Engine, clip_id: str) -> None:
    """Queues the clip's next job, or marks the clip failed with its job's sentence when one failed; the
    project and its other clips are left alone. A job is out of date once the job before it has run again
    (a render asked for again starts from the source), and is queued again unless it is still to run.
    Once every job is done and up to date, the render has set the clip's status. A re-check beside the
    render is no part of it."""
    with engine.lock:
        clip = repo.get_clip(engine.db(), clip_id)
        jobs = [job for job in engine.runner.jobs_for(clip_id) if job.kind in CLIP_FUNCTIONS]
        if clip is None or not jobs:
            return
        newest = {job.kind: job for job in jobs}  # oldest first, so the newest of each kind wins
        prior: Job | None = None
        for stage in CLIP_STAGES:
            job = newest.get(stage.kind)
            stale = job is not None and prior is not None and prior.id > job.id
            if job is None or (stale and job.status not in ("queued", "running")):
                _queue_clip(engine, stage, clip_id, clip.project_id, jobs[-1].priority, rerun=True)
                break
            if job.status in ("failed", "cancelled"):
                engine.update_clip(clip_id, status="failed", error=job.error or STOPPED)
                return
            if job.status != "done":
                break
            prior = job
        else:
            return
        if clip.status != "queued" and not any(job.status == "running" for job in jobs):
            engine.update_clip(clip_id, status="queued")


def queue_render(
    runner: JobRunner, conn: sqlite3.Connection, clip_id: str, edits: dict[str, Any] | None = None
) -> bool:
    """Saves ``edits`` on the clip (an edit set to None is taken off) and queues the clip to render again,
    ahead of the clips rendering on their own. It starts from the clip's source, so a changed layout is
    chosen again before the render (the analysis is cached), and SPEC 14.4's previous version stays
    current until the new one is done. It only changes the database, so it works from a second window
    too. False when there is no such clip, or it is a video posted as it is, which has nothing to render."""
    clip = repo.get_clip(conn, clip_id)
    if clip is None or clip.mode == "as_is":
        return False
    merged = {k: v for k, v in {**(clip.edits or {}), **(edits or {})}.items() if v is not None}
    fields: dict[str, Any] = {"edits": merged or None, "error": None}
    if clip.status not in ("analyzing", "rendering"):
        fields["status"] = "queued"
    repo.update_clip(conn, clip_id, **fields)
    runner.enqueue(
        CLIP_STAGES[0].kind,
        clip_id,
        {"project": clip.project_id},
        priority=USER_PRIORITY,
        rerun=True,
        max_attempts=TRIES.get(CLIP_STAGES[0].lane, 1),
    )
    return True


def queue_review(runner: JobRunner, conn: sqlite3.Connection, clip_id: str, settings: Settings) -> bool:
    """Queues a check of the clip as the user edited it (SPEC 14.4), to run beside its render, when the
    LLM is on, the brain packaged the clip and what the review reads of it changed since it was last
    checked: its range, the words struck from it, its title, its fact cards or its post copy. It only
    changes the database, so it works from a second window too. False when nothing was queued."""
    clip = repo.get_clip(conn, clip_id)
    project = repo.get_project(conn, clip.project_id) if clip else None
    if clip is None or project is None or not clip.packaging:
        return False
    if (project.options.provider or settings.llm_provider) == "heuristic":
        return False
    edits = clip.edits or {}
    checked = (clip.review or {}).get("key")  # what the last re-check read; none as the brain made it
    if (checked is None and not changes_review(edits)) or review_key(clip, edits) == checked:
        return False
    payload = {"project": clip.project_id}
    return runner.enqueue(REVIEW.kind, clip_id, payload, priority=USER_PRIORITY, rerun=True) is not None


def _queue_clip(
    engine: Engine, stage: Stage, clip_id: str, project_id: str, priority: int, *, rerun: bool = False
) -> int | None:
    return engine.runner.enqueue(
        stage.kind,
        clip_id,
        {"project": project_id},
        priority=priority,
        rerun=rerun,
        max_attempts=RENDER_TRIES.get(stage.kind) or TRIES.get(stage.lane, 1),
    )


def _clip_handler(engine: Engine, stage: Stage) -> Handler:
    fn = CLIP_FUNCTIONS[stage.kind]
    status = CLIP_STATUS.get(stage.kind, "analyzing")

    def run(ctx: JobContext) -> dict[str, Any] | None:
        with engine.lock:  # checked under the lock, as discarding the clip cancels its jobs under it
            if ctx.cancelled():
                return None
            engine.update_clip(ctx.job.ref_id, status=status)
        return fn(engine, ctx)

    return run


def _variant_handler(engine: Engine) -> Handler:
    """An account's version of a clip: the clip itself doesn't say it's rendering, as its own render is
    unchanged."""

    def run(ctx: JobContext) -> dict[str, Any] | None:
        if ctx.cancelled():
            return None
        return clips.clip_variant(engine, ctx)

    return run


def _review_handler(engine: Engine) -> Handler:
    def run(ctx: JobContext) -> dict[str, Any] | None:
        if ctx.cancelled():
            return None
        return review_clip.clip_review(engine, ctx)  # a usage limit's WaitUntil goes to the runner

    return run
