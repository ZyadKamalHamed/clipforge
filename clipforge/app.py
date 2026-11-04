"""The engine (SPEC 6 and 18): ClipForge's home, its database, the job runner with every stage of the
pipeline, the event bus, and the services the stages use (YouTube, speech to text, the LLM). The command
line drives ClipForge through it, and the web app will too. One engine at a time has a home open."""

from __future__ import annotations

import fcntl
import logging
import os
import shutil
import threading
import time
from collections.abc import Callable
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from clipforge import repo, storage
from clipforge.asr.parakeet import ParakeetEngine
from clipforge.asr.whisper_mlx import WhisperEngine
from clipforge.brain.llm.base import set_parallel
from clipforge.brain.llm.registry import get_provider
from clipforge.brain.wording import credit_line
from clipforge.config import Settings
from clipforge.db import connect, migrate, transaction
from clipforge.edits import apply_range, validate_edits
from clipforge.errors import ClipForgeError
from clipforge.events import EventBus
from clipforge.ingest.urls import (
    DRIVE_CUT,
    DRIVE_EMPTY,
    FOLDER_MAX,
    Rejected,
    SourceRef,
    canonical_url,
    drive_file_url,
    parse_inputs,
)
from clipforge.ingest.ytdlp import YtDlpClient, clean_info, meta_from_info
from clipforge.jobs import pipeline
from clipforge.jobs.runner import JobRunner
from clipforge.jobs.share import share_files
from clipforge.jobs.stages import read_meta, read_transcript, write_json, write_text
from clipforge.jobs.stages.clips import resting
from clipforge.launcher import DEFAULT_PORT
from clipforge.library import gameplay, starter
from clipforge.library.music import music_items
from clipforge.log import setup_logging
from clipforge.models import ClipRow, Project, ProjectOptions
from clipforge.notify import Notifier
from clipforge.paths import Paths
from clipforge.post import power, queue
from clipforge.post.browser.session import BrowserSessions
from clipforge.post.public_media import PublicMedia
from clipforge.post.service import Posting
from clipforge.today.changes import Changes
from clipforge.untrusted import clean_text
from clipforge.watch import drafts
from clipforge.watch import store as watch_store

if TYPE_CHECKING:
    import sqlite3

    from clipforge.asr.base import SpeechEngine
    from clipforge.assets import Fetch
    from clipforge.brain.llm.base import LLMProvider
    from clipforge.ingest.ytdlp import YouTubeClient
    from clipforge.watch.models import ClipChannel, WatchItem

log = logging.getLogger(__name__)

BUSY = "ClipForge is already running in another window. Use that one, or close it first."
NOT_HERE = "That isn't here."
NOT_STUCK = "This project isn't stuck."
AS_IS_FIXED = (
    "This clip is the video as it is, so there is nothing to edit or render. Add the video again with Post "
    "as it is off to cut clips from it."
)
EVENT_FIELDS = {
    "id",
    "status",
    "stage",
    "progress",
    "message",
    "mode",
    "error",
    "error_code",
    "error_details",
    "rights_basis",
}
CLIP_EVENT_FIELDS = {"id", "project_id", "status", "current_render_id", "error", "updated_at", "discarded_at"}
EVENT_TEXT = ("message", "error", "error_details")  # cleaned on the way out, as the API's JSON is

ProviderFactory = Callable[[Settings, str], "LLMProvider | None"]
Range = tuple[float, float] | None  # a clip's new start and end in seconds; None puts it back


class Engine:
    def __init__(
        self,
        paths: Paths,
        settings: Settings,
        *,
        youtube: YouTubeClient,
        asr_engines: dict[str, SpeechEngine],
        provider_factory: ProviderFactory,
        lock_fd: int,
        publishers: dict[str, Any] | None = None,
        run_cmd: power.RunCmd | None = None,
        now: Callable[[], datetime] | None = None,
    ):
        self.paths = paths
        self.settings = settings
        self.youtube = youtube
        self.asr_engines = asr_engines
        self.provider_factory = provider_factory
        self.events = EventBus()
        self.lock = threading.RLock()  # project updates and the pipeline's moves, one at a time
        self.face_detector: Any = None  # YuNet for clip analysis, loaded on first use
        self.emoji_fetch: Fetch | None = None  # how emoji pictures are fetched; None fetches from the web
        self.thumb_fetch: Fetch | None = None  # and sources' thumbnails, the same way
        self.thumb_misses: set[str] = set()  # projects whose thumbnail couldn't be made, till restart
        self._lock_fd: int | None = lock_fd
        self.runner = JobRunner(
            paths.db,
            pipeline.handlers(self),
            paths=paths,
            settings=settings,
            events=self.events,
            on_finished=lambda job: pipeline.on_finished(self, job),
            standing=(*pipeline.POST_KINDS, *pipeline.ACCOUNT_KINDS, *pipeline.WATCH_KINDS),
        )
        self.graph_transport: Any = None  # Meta's Graph API: tests swap in a look-alike
        self.feed_transport: Any = None  # YouTube's channel feeds, the same way
        self.public_media = PublicMedia()  # Mode B videos' public addresses, in memory only
        self.public_transport: Any = None  # the self-test's fetch of them: tests reach the app itself
        self.link_tickets: dict[str, tuple[float, list[Any]]] = {}  # accounts read from a pasted key
        self.browser = BrowserSessions(paths)  # Chrome windows for the browser route, one at a time
        self.site_base: str | None = None  # the sites' own addresses; tests point it at look-alike pages
        self.brief_transport: Any = (
            None  # campaign sites' pages (httpx's transport): tests swap in a look-alike
        )
        self.pause: Callable[[float], None] = time.sleep  # the wait between two requests to one site
        if publishers is None:
            # Here, as they need the engine's type
            from clipforge.post.browser.instagram import InstagramBrowserPublisher
            from clipforge.post.browser.tiktok import TikTokBrowserPublisher
            from clipforge.post.instagram_api import InstagramGraphPublisher

            publishers = {
                "instagram:api": InstagramGraphPublisher(self),
                "instagram:browser": InstagramBrowserPublisher(self),
                "tiktok:browser": TikTokBrowserPublisher(self),
            }
        self.publishers: dict[str, Any] = dict(publishers)  # each route, keyed "<platform>:<method>"
        self.run_cmd: power.RunCmd = run_cmd or (lambda args: power.launch(args))
        self.now: Callable[[], datetime] = now or (lambda: datetime.now(UTC))
        self.power = power.PowerAssertion(self.run_cmd)
        self.notifier = Notifier(
            paths, settings, port=DEFAULT_PORT, run=self.run_cmd, app_dir=Path.home() / "Applications"
        )
        self.events.listen(lambda event: self.notifier.hear(self, event.topic, event.data))
        self.changes = Changes(lambda: self.now().timestamp())  # what Today's items wait on, as it changes
        self.events.listen(lambda event: self.changes.heard(event.topic))
        self.posting = Posting(self)
        self._posting_held = ExitStack()  # nothing posts until start_posting: only the window posts
        self.awake: queue.Awake | None = None  # when ClipForge could post, from start_posting on
        for lane in sorted(set(pipeline.POST_KINDS.values())):
            self._posting_held.enter_context(self.runner.hold(lane))

    @classmethod
    def open(
        cls,
        paths: Paths | None = None,
        *,
        settings_overrides: dict[str, Any] | None = None,
        youtube: YouTubeClient | None = None,
        asr_engines: dict[str, SpeechEngine] | None = None,
        provider_factory: ProviderFactory | None = None,
        verbose: bool = False,
        publishers: dict[str, Any] | None = None,
        run_cmd: power.RunCmd | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> Engine:
        """The engine on ``paths`` (the home in $CLIPFORGE_HOME, else ~/ClipForge), with its database
        brought up to date and its settings loaded. The jobs the last run left running go back in the
        queue and every unfinished project carries on, once ``wait`` starts the workers. While another
        engine has the same home open, it is refused with "busy"."""
        paths = paths or Paths.from_env()
        paths.ensure()
        lock_fd = take_home(paths)
        engine = None
        try:
            conn = connect(paths.db)
            try:
                migrate(conn)
                stored = repo.load_settings(conn)
            finally:
                conn.close()
            settings = Settings.model_validate(stored.model_dump() | (settings_overrides or {}))
            set_parallel(settings.llm_parallel)
            engine = cls(
                paths,
                settings,
                youtube=youtube or YtDlpClient(),
                asr_engines=asr_engines or default_asr_engines(paths),
                provider_factory=provider_factory or default_provider_factory(paths),
                lock_fd=lock_fd,
                publishers=publishers,
                run_cmd=run_cmd,
                now=now,
            )
            setup_logging(paths, verbose=verbose)
            engine.runner.recover()
            starter.sweep(engine)
            for pid in repo.unfinished_project_ids(engine.db()):
                try:
                    pipeline.advance(engine, pid)
                except Exception:  # one broken project never keeps ClipForge from opening
                    log.exception("project %s could not carry on", pid)
            try:
                queue.recover_posts(engine)
                queue.sync_jobs(engine)
            except Exception:  # nor do posts that can't be matched to their jobs
                log.exception("posts could not be matched to their jobs")
        except BaseException:
            if engine is not None:
                engine.close()
            else:
                os.close(lock_fd)
            raise
        return engine

    # Projects

    def add_sources(
        self, text: str, options: ProjectOptions, *, new: bool = False
    ) -> tuple[list[Project], list[Rejected]]:
        """A project for each link and file in ``text``, and for each video in a Drive folder link, plus
        what couldn't be used, with why. A link to a video that already has a project with the same Post as
        it is choice gives that project back, unless ``new``. Otherwise the new project shares the files
        already downloaded for the video, which is how adding it again with the switch flipped works. Each
        file makes a project of its own."""
        refs, rejected = parse_inputs(text, allow_other_sites=self.settings.allow_other_sites)
        projects: list[Project] = []
        for ref in refs:
            if ref.kind != "drive_folder":
                projects.append(self._add(ref, options, new=new))
                continue
            found, problem = self._add_folder(ref, options, new=new)
            projects += found
            if problem is not None:
                rejected.append(problem)
        return projects, rejected

    def _add_folder(
        self, ref: SourceRef, options: ProjectOptions, *, new: bool
    ) -> tuple[list[Project], Rejected | None]:
        """A project for each video in a shared Drive folder, named after its file, and what to say about
        the folder: that it couldn't be listed, has no videos, or has more than ``FOLDER_MAX``."""
        try:
            folder = self.youtube.list_drive_folder(ref.url, FOLDER_MAX)
        except ClipForgeError as err:
            return [], Rejected(ref.url, err.message)
        found = [
            self._add(
                SourceRef("drive", drive_file_url(f.id), f.id, None, None), options, new=new, title=f.title
            )
            for f in folder.files
        ]
        if not found:
            return [], Rejected(ref.url, DRIVE_EMPTY)
        return found, Rejected(ref.url, DRIVE_CUT.format(n=FOLDER_MAX)) if folder.more else None

    def _add(self, ref: SourceRef, options: ProjectOptions, *, new: bool, title: str = "") -> Project:
        conn = self.db()
        with self.lock:
            earlier = repo.find_projects_by_video(conn, ref.video_id) if ref.video_id else []
            same = next((p for p in earlier if p.options.as_is == options.as_is), None)
            if same is not None and not new:
                return same
            if ref.kind == "file":
                path = ref.path.resolve()
                project = repo.create_project(
                    conn,
                    source_kind="file",
                    source_url=path.as_uri(),
                    video_id=None,
                    options=options,
                    focus_s=None,
                    prompt=options.prompt,
                    title=path.stem,
                )
            else:
                project = repo.create_project(
                    conn,
                    source_kind=ref.kind,
                    source_url=ref.url,
                    video_id=ref.video_id,
                    options=options,
                    focus_s=ref.focus_s,
                    prompt=options.prompt,
                    title=title or None,  # a Drive folder's file name, until the metadata comes in
                )
        pp = self.paths.project(project.id)
        pp.ensure()
        if earlier:
            share_files(self.paths.project(earlier[0].id).source, pp.source)
        return self.project(project.id)

    def add_watched(self, channel: ClipChannel, item: WatchItem, info: dict) -> Project:
        """The project a clipping channel's check makes for a new upload: the channel's options and rights,
        the info the check already looked up, and the files of any earlier project of the video, so nothing
        is fetched twice. Its jobs queue behind every project started by hand."""
        video_id = item.video_id
        ref = SourceRef(
            kind="youtube", url=canonical_url(video_id), video_id=video_id, path=None, focus_s=None
        )
        with self.lock:
            earlier = repo.find_projects_by_video(self.db(), video_id)
            for project in earlier:
                if project.clip_channel_id == channel.id:
                    return project
            with transaction(self.db()):
                project = self._add(ref, channel.project_options(), new=True)
                repo.update_project(
                    self.db(),
                    project.id,
                    clip_channel_id=channel.id,
                    rights_basis=channel.rights_basis,
                    campaign_id=channel.campaign_id,
                )
            self._publish(project.id)
        pp = self.paths.project(project.id)
        clean = clean_info(info)
        if not pp.info_json.is_file():
            write_json(pp.info_json, clean)
        if not pp.meta_json.is_file():
            write_text(pp.meta_json, meta_from_info(clean).model_dump_json())
        done = next(
            (p for p in map(self.paths.project, (e.id for e in earlier)) if p.signals_json.is_file()), None
        )
        if done is not None:
            pp.work.mkdir(parents=True, exist_ok=True)
            for name in ("audio16k", "transcript_json", "signals_json"):
                src, dst = getattr(done, name), getattr(pp, name)
                if src.is_file() and not dst.exists():
                    try:
                        os.link(src, dst)
                    except OSError as err:
                        log.info("%s is not shared (%s), so it is made again", dst.name, err)
        pipeline.start(self, project.id)
        made = self.project(project.id)
        assert made is not None
        return made

    def cleanup(self, now: float | None = None) -> storage.CleanResult:
        """SPEC 16's clean-up: old YouTube projects' source files and forgotten upload folders deleted."""
        now = time.time() if now is None else now
        with self.lock:  # so no job starts on a project as its files go
            found = storage.cleanable(self.db(), self.paths, self.settings, now)
            uploads = storage.stale_uploads(self.db(), self.paths, now)
            freed = sum(storage.clean_project(self.paths.project(pid)) for pid, _ in found)
            freed += sum(storage.remove(folder) for folder in uploads)
        if found or uploads:
            log.info("the clean-up freed %d bytes from %d projects", freed, len(found))
        return storage.CleanResult(freed, len(found))

    def add_file(self, path: Path, options: ProjectOptions) -> Project:
        """A new project for the video file at ``path``, taken as it is: no link parsing, so any name
        will do."""
        return self._add(SourceRef("file", None, None, Path(path), None), options, new=True)

    def start(self, project_id: str) -> None:
        """Sets the project going: from the beginning, or from the stage that stopped it."""
        pipeline.start(self, project_id)

    def retry(self, project_id: str) -> Project:
        """Sets a stuck project going again (SPEC 14.3): a failed or refused one from the stage that stopped
        it, a waiting one now rather than when its wait is over. Anything else is "not_stuck"."""
        with self.lock:
            project = self.project(project_id)
            if project is None:
                raise ClipForgeError("not_found", NOT_HERE)
            if project.status == "waiting":
                self.runner.wake(project_id)
                self.update_project(project_id, status="queued", message=None)
            elif project.status in ("failed", "refused"):
                pipeline.start(self, project_id)
            else:
                raise ClipForgeError("not_stuck", NOT_STUCK)
            return self.project(project_id)

    def delete_project(self, project_id: str, *, wait_s: float = 10.0) -> None:
        """Deletes the project, its clips with their renders, and its folder. Its jobs and its clips' are
        cancelled first, and one still running gets up to ``wait_s`` to let go of the files. The rows go
        before the jobs are cancelled, so nothing that finishes moves the project on. When a clipping
        channel's check was waiting for it, the check's drafts are made from the rest."""
        with self.lock:
            conn = self.db()
            refs = [project_id, *(c.id for c in self.clips(project_id))]
            item = watch_store.item_of_project(conn, project_id)
            with transaction(conn):
                repo.delete_project(conn, project_id)
            for ref in refs:
                for job in self.runner.jobs_for(ref):
                    if job.status in ("queued", "running"):
                        self.runner.cancel(job.id)
        deadline = time.monotonic() + wait_s
        while self.runner.busy(refs) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.runner.delete_jobs(refs)
        shutil.rmtree(self.paths.project(project_id).root, ignore_errors=True)
        self.thumb_misses.discard(project_id)
        self.events.publish("project", {"id": project_id, "status": "deleted"})
        if item is not None and item.check_id is not None:
            drafts.settle_check(self, item.check_id)

    def rerun_selection(self, project_id: str, *, provider: str | None = None) -> bool:
        """Makes the project's clips again from the selection on, with ``provider`` from now on when given.
        False when there is nothing to make again yet."""
        with self.lock:
            queued = pipeline.queue_rerun(self.runner, self.db(), project_id, provider=provider)
            self._publish(project_id)
            pipeline.advance(self, project_id)
        return queued

    def save_edits(
        self, clip_id: str, patch: dict[str, Any], range_: Range | Literal["keep"] = "keep"
    ) -> ClipRow:
        """Saves ``patch`` on the clip's edits (an edit set to None is taken off) and moves the clip to
        ``range_`` (None: back where it started), each checked first: EditError names the first that can't
        be used, and nothing is saved. "not_found" when there is no such clip."""
        with self.lock:
            clip = repo.get_clip(self.db(), clip_id)
            if clip is None:
                raise ClipForgeError("not_found", NOT_HERE)
            fixed(clip)
            saved = self.update_clip(clip_id, **checked_edits(self.db(), self.paths, clip, patch, range_))
            assert saved is not None  # the lock keeps it here
            return saved

    def render_clip(
        self, clip_id: str, edits: dict[str, Any] | None = None, range_: Range | Literal["keep"] = "keep"
    ) -> bool:
        """Saves ``edits`` and ``range_`` on the clip as ``save_edits`` does, then renders it again, ahead of
        the clips rendering on their own, and checks it again beside the render when the edits changed
        what the review read (SPEC 14.4). False when there is no such clip."""
        with self.lock:
            clip = repo.get_clip(self.db(), clip_id)
            if clip is None:
                return False
            fixed(clip)
            if edits or range_ != "keep":
                self.save_edits(clip_id, edits or {}, range_)
            queued = pipeline.queue_render(self.runner, self.db(), clip_id)
            pipeline.queue_review(self.runner, self.db(), clip_id, self.settings)
            self.publish_clip(clip_id)
        return queued

    def shuffle_gameplay(self, clip_id: str) -> ClipRow:
        """Another stretch of gameplay under the clip: its gameplay use is let go and its seed moved on, and
        it is rendered again. "not_found" when there is no such clip."""
        with self.lock:
            clip = repo.get_clip(self.db(), clip_id)
            if clip is None:
                raise ClipForgeError("not_found", NOT_HERE)
            fixed(clip)
            edits = (clip.edits or {}) | {"gameplay_seed": (clip.edits or {}).get("gameplay_seed", 0) + 1}
            with transaction(self.db()):
                repo.clear_gameplay_uses(self.db(), clip_id)
                repo.update_clip(self.db(), clip_id, edits=edits)
            pipeline.queue_render(self.runner, self.db(), clip_id)
            shuffled = self.publish_clip(clip_id)
            assert shuffled is not None
            return shuffled

    def discard_clip(self, clip_id: str) -> ClipRow:
        """Puts the clip aside (SPEC 14.4): its queued and running jobs are cancelled, it is listed only with
        the discarded clips, and it never renders on its own. One that was stopped rests as its last render
        left it. "not_found" when there is no such clip."""
        with self.lock:
            clip = repo.get_clip(self.db(), clip_id)
            if clip is None:
                raise ClipForgeError("not_found", NOT_HERE)
            if clip.discarded_at is None:  # first, so a job that stops doesn't move it on
                repo.update_clip(self.db(), clip_id, discarded_at=time.time())
            stopping = [job for job in self.runner.jobs_for(clip_id) if job.status in ("queued", "running")]
            for job in stopping:
                self.runner.cancel(job.id)
            if stopping:
                status, error = resting(self.db(), clip)
                repo.update_clip(self.db(), clip_id, status=status, error=error)
            discarded = self.publish_clip(clip_id)
            assert discarded is not None  # the lock keeps it here
            return discarded

    def restore_clip(self, clip_id: str) -> ClipRow:
        """Brings a discarded clip back; one whose jobs were stopped rests as its last render left it.
        "not_found" when there is no such clip."""
        with self.lock:
            clip = repo.get_clip(self.db(), clip_id)
            if clip is None:
                raise ClipForgeError("not_found", NOT_HERE)
            fields: dict[str, Any] = {"discarded_at": None}
            jobs = [job for job in self.runner.jobs_for(clip_id) if job.kind in pipeline.CLIP_FUNCTIONS]
            idle = all(job.status not in ("queued", "running") for job in jobs)  # a re-check is no render
            if jobs and jobs[-1].status == "cancelled" and idle:
                fields["status"], fields["error"] = resting(self.db(), clip)
            restored = self.update_clip(clip_id, **fields)
            assert restored is not None
            return restored

    def update_project(self, project_id: str, **fields: Any) -> None:
        """Changes the project's fields and tells everyone listening."""
        with self.lock:
            repo.update_project(self.db(), project_id, **fields)
            self._publish(project_id)

    def _publish(self, project_id: str) -> None:
        project = self.project(project_id)
        if project is not None:
            data = project.model_dump(include=EVENT_FIELDS)
            self.events.publish("project", data | {k: clean_text(data[k]) for k in EVENT_TEXT})

    def update_clip(self, clip_id: str, **fields: Any) -> ClipRow | None:
        """Changes the clip's fields and tells everyone listening. None when there is no such clip."""
        with self.lock:
            repo.update_clip(self.db(), clip_id, **fields)
            return self.publish_clip(clip_id)

    def publish_clip(self, clip_id: str) -> ClipRow | None:
        """Tells everyone listening how the clip is now, for a change already written (and committed)."""
        clip = repo.get_clip(self.db(), clip_id)
        if clip is not None:
            data = clip.model_dump(include=CLIP_EVENT_FIELDS)
            self.events.publish("clip", data | {"error": clean_text(data["error"])})
        return clip

    def project(self, project_id: str) -> Project | None:
        return repo.get_project(self.db(), project_id)

    def projects(self, limit: int = 50, status: list[str] | None = None) -> list[Project]:
        """The newest projects first, only those in one of ``status`` when it is given."""
        return repo.list_projects(self.db(), limit, status)

    def clips(self, project_id: str) -> list[ClipRow]:
        return repo.list_clips(self.db(), project_id)

    # Running

    def db(self) -> sqlite3.Connection:
        """This thread's connection to the database."""
        return self.runner.db()

    def update_settings(self, new: Settings) -> Settings:
        """Saves ``new`` and uses it from now on. Jobs already running keep the settings they started with."""
        with self.lock:
            repo.save_settings(self.db(), new)
            self.settings = self.runner.settings = self.notifier.settings = new
            set_parallel(new.llm_parallel)
        return new

    def wait(
        self,
        timeout_s: float | None = None,
        *,
        projects: list[str] | None = None,
        clips: list[str] | None = None,
    ) -> bool:
        """Runs the jobs (of ``projects`` and their clips, and of ``clips``, when either is given) until
        none is left, or for at most ``timeout_s`` seconds. True when none is left."""
        if projects is None and clips is None:
            return self.runner.run_until_idle(timeout_s)
        mine, theirs = projects or [], clips or []
        return self.runner.run_until_idle(
            timeout_s, refs=lambda: [*mine, *(c.id for p in mine for c in self.clips(p)), *theirs]
        )

    def start_posting(self) -> None:
        """Lets post jobs start. Only the window's server calls it, so ``clipforge run`` never posts."""
        queue.opened(self)
        self._posting_held.close()

    def close(self, timeout_s: float = 10.0) -> None:
        """Stops the workers, giving running jobs up to ``timeout_s`` to finish, and frees the home for
        another engine. A job still running carries on the next time an engine opens. Closing twice is
        harmless."""
        if self._lock_fd is None:
            return
        try:
            self.runner.close(timeout_s)
            self.power.release()
            setup_logging(self.paths, console=False, file=False)
        finally:
            os.close(self._lock_fd)  # which lets go of the home
            self._lock_fd = None


def fixed(clip: ClipRow) -> None:
    """Refuses to change a clip that is its video as it is (SPEC 14.2): "as_is" says why."""
    if clip.mode == "as_is":
        raise ClipForgeError("as_is", AS_IS_FIXED)


def checked_edits(
    conn: sqlite3.Connection,
    paths: Paths,
    clip: ClipRow,
    patch: dict[str, Any],
    range_: Range | Literal["keep"] = "keep",
) -> dict[str, Any]:
    """The clip's fields with ``patch`` checked and merged into its edits and, unless ``range_`` is "keep",
    the clip moved: its new edits, and its new start, end and sentences when it moves. Raises EditError for
    the first edit that can't be used."""
    pp = paths.project(clip.project_id)
    transcript, meta = read_transcript(pp), read_meta(pp)
    fields: dict[str, Any] = {}
    current = clip.edits or {}
    if range_ != "keep":
        fields, moved = apply_range(
            clip, transcript, range_, duration_s=meta.duration_s or transcript.duration
        )
        clip = clip.model_copy(update=fields)  # the patch is checked against where the clip is going
        current = {k: v for k, v in (current | moved).items() if v is not None}
    project = repo.get_project(conn, clip.project_id)
    credit = credit_line(meta, project.rights_basis, project.options) if project is not None else None
    library = {item.id for item in gameplay.usable(conn)}
    tracks = {item.id for item in music_items(conn)}
    folder = pp.clip_dir(clip.id) / "voiceover"
    takes = {p.name for p in folder.iterdir()} if folder.is_dir() else set()
    edits = validate_edits(
        clip, transcript, current, patch, library_ids=library, credit=credit, music_ids=tracks, takes=takes
    )
    return fields | {"edits": edits or None}


def take_home(paths: Paths) -> int:
    """A lock on the home, held for as long as the file stays open."""
    fd = os.open(paths.run_dir / "engine.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise ClipForgeError("busy", BUSY) from None
    return fd


def default_asr_engines(paths: Paths) -> dict[str, SpeechEngine]:
    return {"parakeet": ParakeetEngine(paths.hf_home), "whisper": WhisperEngine(paths.hf_home)}


def default_provider_factory(paths: Paths) -> ProviderFactory:
    def factory(settings: Settings, name: str) -> LLMProvider | None:
        return get_provider(settings, name, paths=paths)

    return factory
