"""The `clipforge` command (SPEC 22): clips from links and files, followed as they are made, a look at what
came out, a clip rendered again with changes, and the gameplay library. Text from a video or a file (titles,
words, captions, reasons, credits) is printed with its control characters as spaces, so nothing in a video
can send the terminal escape codes."""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO, get_args

from clipforge import assets, launcher, repo, service
from clipforge.brain.text import shortened
from clipforge.config import ProviderName, Settings
from clipforge.db import connect, migrate
from clipforge.doctor import run_checks
from clipforge.errors import ClipForgeError
from clipforge.ingest.urls import NOTHING, file_from_url
from clipforge.launcher import DEFAULT_PORT
from clipforge.models import LENGTH_RANGES, ClipRow, Project, ProjectOptions, RenderRow, Transcript
from clipforge.paths import Paths
from clipforge.render.styles import PRESETS
from clipforge.vision.layout import NAMES as LAYOUT_NAMES
from clipforge.ytdlp_update import update_ytdlp

if TYPE_CHECKING:
    import sqlite3

    from clipforge.app import Engine
    from clipforge.events import Subscription

STAGE_TEXT = {
    "ingest.metadata": "Reading the video's details",
    "ingest.audio": "Getting the audio",
    "ingest.captions": "Getting the captions",
    "ingest.extras": "Getting the comments and live chat",
    "ingest.video": "Downloading the video",
    "asr.transcribe": "Transcribing",
    "signals.compute": "Reading the signals",
    "brain.select": "Finding the best moments",
    "brain.package": "Writing hook titles and captions",
    "asis.make": "Making the clip",
}
PROVIDER_TEXT = {
    "claude_cli": "Claude Code",
    "anthropic": "the Anthropic API",
    "gemini": "Gemini",
    "openai_compat": "the OpenAI compatible server",
    "heuristic": "Basic mode",
}
OFF_TOPIC = "Not about the topic you asked for."
FLAG_TEXT = {
    "meaning_distortion": "the cut changes what the speaker meant",
    "no_faithful_hook": "no hook title that the clip backs up",
    "missing_context": "it needs context the clip doesn't give",
    "defamation": "it could defame someone",
    "harmful_misinformation": "harmful misinformation",
    "unsafe_content": "unsafe content",
    "third_party_media": "someone else's music, film or TV in it",
    "private_info": "private information",
    "political_campaign": "political campaigning",
    "too_short": "too short",
    "too_long": "too long",
    "incomplete_story": "the story isn't complete",
    "duplicate": "the same moment as a better clip",
    "sponsor_read": "a sponsor read",
    "crosstalk": "people talking over each other",
    "early_profanity": "swearing near the start",
    "long_monologue": "a long stretch of one voice",
    "jargon": "jargon",
}
FINAL = ("ready", "failed", "refused")
REASON_CHARS = 60
RIGHT = {0, 2, 4}  # the table's number columns
PAD = " " * 7
INTERRUPTED = "Stopped. The work so far is saved and carries on the next time ClipForge runs."
OPEN_ELSEWHERE = "ClipForge is open, so update yt-dlp from its Doctor page, or quit ClipForge and try again."
SELFTEST_ELSEWHERE = (
    "ClipForge is open, so run the self-test from its Accounts page, or quit ClipForge and try again."
)
NO_WINDOW = "Chrome couldn't open for the self-test: {problem}"
STILL_VIDEO = "Still getting the full video, for rendering later."
NO_VIDEO = "This clip's video couldn't be made."
PLATFORM_TEXT = {"instagram": "Instagram", "tiktok": "TikTok"}
EMPTY_LIBRARY = (
    "The library is empty. Add gameplay with: clipforge library add FILE --credit TEXT --license TEXT"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="clipforge", description="Long videos in, short clips out.")
    sub = parser.add_subparsers(dest="command", required=True)
    providers = get_args(ProviderName)

    run = sub.add_parser("run", help="find the best clips in YouTube links or video files")
    run.add_argument("inputs", nargs="+", metavar="LINK_OR_FILE")
    run.add_argument("--no-render", action="store_true", help="find the clips without making their videos")
    run.add_argument("--clips", type=_count_arg, metavar="N", help="how many clips you want")
    run.add_argument(
        "--length",
        choices=list(LENGTH_RANGES),
        help=", ".join(f"{name} {low:g} to {high:g} s" for name, (low, high) in LENGTH_RANGES.items())
        + " (auto is the default)",
    )
    run.add_argument("--about", metavar="TEXT", help="what the clips should be about")
    run.add_argument(
        "--content", choices=["auto", "podcast", "solo", "stream", "other"], help="the kind of video"
    )
    run.add_argument(
        "--quality", choices=["best", "balanced", "fast"], help="the quality of the video download"
    )
    run.add_argument(
        "--provider", choices=providers, help="the AI that picks the moments (heuristic: Basic mode)"
    )
    run.add_argument(
        "--new", action="store_true", help="make new clips of a video that has a project already"
    )
    run.add_argument("--json", action="store_true", help="print the clips as JSON")
    run.add_argument("-v", "--verbose", action="store_true", help="show the log as it goes")

    show = sub.add_parser("show", help="the clips of a project")
    show.add_argument("project")
    show.add_argument("--all", action="store_true", help="also the clips marked Not recommended")
    show.add_argument("--text", action="store_true", help="each clip's words, review and captions too")
    show.add_argument("--json", action="store_true", help="every clip as JSON")

    sub.add_parser("projects", help="the projects, newest first")

    rerun = sub.add_parser("rerun", help="make a project's clips again from the selection on")
    rerun.add_argument("project")
    rerun.add_argument(
        "--provider", choices=providers, help="the AI to use from now on (heuristic: Basic mode)"
    )
    rerun.add_argument("-v", "--verbose", action="store_true", help="show the log as it goes")

    render = sub.add_parser("render", help="make a clip's video again, with your changes")
    render.add_argument("clip")
    render.add_argument(
        "--layout",
        choices=[*LAYOUT_NAMES, "auto"],
        help=", ".join(f"{name} ({text})" for name, text in LAYOUT_NAMES.items())
        + ", or auto to let ClipForge choose again",
    )
    render.add_argument(
        "--captions", choices=list(PRESETS), metavar="PRESET", help="the caption style: " + ", ".join(PRESETS)
    )
    render.add_argument("--best", action="store_true", help="encode with libx264: slower, a little sharper")
    render.add_argument("-v", "--verbose", action="store_true", help="show the log as it goes")

    library = sub.add_parser("library", help="the gameplay videos that can go under a speaker")
    action = library.add_subparsers(dest="action", required=True)
    add = action.add_parser("add", help="add a video file of your own, or one you may use, to the library")
    add.add_argument("file")
    add.add_argument("--kind", choices=["gameplay"], default="gameplay", help="what it is for")
    add.add_argument("--credit", required=True, metavar="TEXT", help="who made it")
    add.add_argument("--license", required=True, metavar="TEXT", help="what lets you use it")
    add.add_argument("--notes", metavar="TEXT")
    action.add_parser("list", help="the library's files, and how often each was used")

    serve = sub.add_parser("serve", help="run ClipForge's engine and window until stopped (Ctrl+C)")
    serve.add_argument("--port", type=_port_arg, default=DEFAULT_PORT, help=f"default {DEFAULT_PORT}")
    serve.add_argument("--open", action="store_true", help="open the window once it is ready")
    opener = sub.add_parser("open", help="open ClipForge's window, starting the engine if it isn't running")
    opener.add_argument("--port", type=_port_arg, default=DEFAULT_PORT, help=f"default {DEFAULT_PORT}")

    assets_cmd = sub.add_parser("assets", help="the models ClipForge downloads once")
    assets_cmd.add_subparsers(dest="action", required=True).add_parser(
        "fetch", help="download the face and speech models now, not on first use"
    )

    sub.add_parser("doctor", help="check that everything ClipForge needs is installed")
    service_cmd = sub.add_parser("service", help="run ClipForge in the background from when you log in")
    service_cmd.add_argument("action", choices=("install", "uninstall", "status"))
    sub.add_parser("update-ytdlp", help="update yt-dlp, which ClipForge downloads videos with")
    selftest = sub.add_parser(
        "selftest", help="check that TikTok's or Instagram's page still has what ClipForge posts with"
    )
    selftest.add_argument("account", help="the account's @handle, or its id from the Accounts page")
    return parser


def _port_arg(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        number = 0
    if not 1 <= number <= 65535:
        raise argparse.ArgumentTypeError("give a port from 1 to 65535")
    return number


def _count_arg(value: str) -> int:
    """A whole number of clips, 1 or more."""
    try:
        number = int(value)
    except ValueError:
        number = 0
    if number < 1:
        raise argparse.ArgumentTypeError("give a whole number, 1 or more")
    return number


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    commands = {
        "run": cmd_run,
        "show": cmd_show,
        "projects": cmd_projects,
        "rerun": cmd_rerun,
        "render": cmd_render,
        "library": cmd_library,
        "selftest": cmd_selftest,
    }
    try:
        if args.command == "doctor":
            return cmd_doctor()
        if args.command == "update-ytdlp":
            return cmd_update_ytdlp()
        if args.command == "serve":
            return cmd_serve(args.port, open_window=args.open)
        if args.command == "open":
            return launcher.open_app(Paths.from_env(), args.port)
        if args.command == "assets":
            return cmd_assets_fetch()
        if args.command == "service":
            return cmd_service(args.action)
        return commands[args.command](args)
    except KeyboardInterrupt:  # a second Ctrl+C while the first one is being handled
        print(INTERRUPTED, file=sys.stderr)
        return 130


def open_engine(*, verbose: bool = False) -> Engine:
    """The engine on this Mac's ClipForge home."""
    from clipforge.app import Engine  # here, so doctor, show and projects start quickly

    return Engine.open(verbose=verbose)


def _db(paths: Paths) -> sqlite3.Connection:
    """A connection of our own to the database, for the commands that only read it."""
    conn = connect(paths.db)
    migrate(conn)
    return conn


# clipforge run


def cmd_run(args: argparse.Namespace) -> int:
    out = sys.stderr if args.json else sys.stdout  # with --json, stdout has only the JSON
    try:
        engine = open_engine(verbose=args.verbose)
    except ClipForgeError as err:
        print(err.message, file=sys.stderr)
        return 1
    try:
        events = engine.events.subscribe()
        began = time.time()
        found, rejected = engine.add_sources("\n".join(args.inputs), _options(args), new=args.new)
        for item in rejected:
            print(f"Can't use {clean(item.token)}: {item.reason}", file=sys.stderr)
        projects = list({p.id: p for p in found}.values())
        if not projects:
            print(NOTHING, file=sys.stderr)
            return 1
        for p in projects:
            print(f"Project {p.id}: {clean(source_of(p))}", file=out)
            if p.created_at < began:
                print(
                    f"Using the existing project {p.id} for this video. Add --new to make new clips.",
                    file=out,
                )
            engine.start(p.id)
        return _follow(engine, events, [p.id for p in projects], out=out, as_json=args.json)
    except KeyboardInterrupt:
        return _interrupted(engine)
    finally:
        engine.close()


def _options(args: argparse.Namespace) -> ProjectOptions:
    """The project's options from the flags given; the rest keep their defaults."""
    given = {
        "length": args.length,
        "clip_count": args.clips,
        "content_type": args.content,
        "prompt": args.about,
        "quality": args.quality,
        "provider": args.provider,
    }
    fields: dict[str, Any] = {name: value for name, value in given.items() if value is not None}
    if args.no_render:
        fields["render"] = False
    return ProjectOptions(**fields)


def _interrupted(engine: Engine) -> int:
    engine.close(timeout_s=2.0)
    print(INTERRUPTED, file=sys.stderr)
    return 130


def _follow(engine: Engine, events: Subscription, ids: list[str], *, out: TextIO, as_json: bool) -> int:
    """Prints each project's steps as they start and its clips once it is done, until none of their jobs
    is left. 0 when every project ends ready."""
    follower = _Follower(engine, ids, out=out, as_json=as_json)
    try:
        while True:
            idle = engine.wait(timeout_s=0.25, projects=ids)
            follower.take(events)
            if idle:
                break
    finally:
        events.close()
    follower.finish()
    follower.videos()
    if as_json:
        print(to_json(follower.rows))
    return 0 if all(follower.final.get(pid) == "ready" for pid in ids) else 1


class _Follower:
    """What ``clipforge run`` has said about each project so far."""

    def __init__(self, engine: Engine, ids: list[str], *, out: TextIO, as_json: bool):
        self.engine, self.ids, self.out, self.as_json = engine, ids, out, as_json
        self.stage: dict[str, str] = {}
        self.said: dict[str, set[str]] = {pid: set() for pid in ids}  # the waiting messages given
        self.final: dict[str, str] = {}  # the projects whose result is printed, and their status
        self.printed: dict[str, list[str]] = {}  # the video lines printed under each clip's row
        self.rows: list[dict[str, Any]] = []

    def say(self, pid: str, text: str) -> None:
        print(f"{pid}: {text}" if len(self.ids) > 1 else text, file=self.out, flush=True)

    def take(self, events: Subscription) -> None:
        while (event := events.get(timeout=0)) is not None:
            if event.topic == "project" and event.data.get("id") in self.said:
                self.seen(event.data)

    def seen(self, data: dict[str, Any]) -> None:
        pid = data["id"]
        if pid in self.final:
            return
        if data.get("status") in FINAL:
            self.done(pid)
            return
        stage = data.get("stage")
        if stage in STAGE_TEXT and stage != self.stage.get(pid):
            self.stage[pid] = stage
            self.say(pid, STAGE_TEXT[stage])
        message = data.get("message")
        if data.get("status") == "waiting" and message and message not in self.said[pid]:
            self.said[pid].add(message)
            self.say(pid, clean(message))

    def done(self, pid: str) -> None:
        with self.engine.lock:  # so the clips that render on their own are queued by now
            project = self.engine.project(pid)
            if project is None:
                return
            self.final[pid] = project.status
            clips = self.engine.clips(pid) if project.status == "ready" else []
            conn = self.engine.db()
            renders = {c.id: r for c in clips if (r := _render_of(conn, c)) is not None}
        lines = result_lines(project, clips, renders=renders)
        if self.as_json:
            lines = lines[:1]
        else:  # the clips the table lists
            self.printed |= {c.id: render_lines(c, renders.get(c.id)) for c in clips if c.tier != "no"}
        self.say(pid, lines[0])
        for line in lines[1:]:
            print(line, file=self.out)
        jobs = self.engine.runner.jobs_for(pid)
        if any(j.kind == "ingest.video" and j.status in ("queued", "running") for j in jobs):
            self.say(pid, STILL_VIDEO)
        making = sum(1 for c in clips if c.status in ("queued", "analyzing", "rendering"))
        if making:
            self.say(pid, f"Making the videos of {_clips(making)}.")
        self.out.flush()

    def videos(self) -> None:
        """Once the jobs are done: where each clip's video is, its layout and its problems, or why it
        failed, best first, for the clips whose lines changed since their row was printed. With --json,
        the clip rows as they are now, each with its current render."""
        for pid in self.ids:
            if self.final.get(pid) != "ready":
                continue
            conn = self.engine.db()
            clips = self.engine.clips(pid)
            if self.as_json:
                self.rows += [
                    {**c.model_dump(mode="json"), "render": r.model_dump(mode="json") if r else None}
                    for c in clips
                    for r in [_render_of(conn, c)]
                ]
                continue
            made = [c for c in clips if c.current_render_id or c.status == "failed"]
            rows = [(c, render_lines(c, _render_of(conn, c))) for c in made]
            rows = [(c, lines) for c, lines in rows if lines and lines != self.printed.get(c.id)]
            if rows:
                self.say(pid, "Videos:")
            for c, (first, *rest) in sorted(rows, key=lambda row: (row[0].rank is None, row[0].rank or 0)):
                print(f"{'-' if c.rank is None else c.rank:>5}  {first}", file=self.out)
                for line in rest:
                    print(f"{PAD}{line}", file=self.out)
        self.out.flush()

    def finish(self) -> None:
        """The result of each project not printed yet, such as one that was ready before it started."""
        for pid in self.ids:
            if pid not in self.final:
                self.done(pid)


# clipforge show and clipforge projects


def cmd_show(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    conn = _db(paths)
    try:
        project = repo.get_project(conn, args.project)
        clips = repo.list_clips(conn, args.project) if project else []
        renders = {c.id: r for c in clips if (r := _render_of(conn, c)) is not None}
    finally:
        conn.close()
    if project is None:
        print(f"No project {clean(args.project)}.", file=sys.stderr)
        return 1
    if args.json:
        print(to_json([c.model_dump(mode="json") for c in clips]))
        return 0
    print(f"Project {project.id}: {clean(project.title or source_of(project))}")
    if project.title:
        print(clean(source_of(project)))
    words = transcript_words(paths, project.id) if args.text else {}
    lines = result_lines(project, clips, show_all=args.all, text=args.text, words=words, renders=renders)
    for line in lines:
        print(line)
    return 0


def cmd_projects(args: argparse.Namespace) -> int:
    conn = _db(Paths.from_env())
    try:
        projects = repo.list_projects(conn)
    finally:
        conn.close()
    if not projects:
        print("No projects yet.")
    for p in projects:
        print(f"{p.id}  {p.status:<8}  {clean(p.title or source_of(p))}")
    return 0


def result_lines(
    project: Project,
    clips: list[ClipRow],
    *,
    show_all: bool = False,
    text: bool = False,
    words: dict[str, str] | None = None,
    renders: dict[str, RenderRow] | None = None,
) -> list[str]:
    """Where the project is, and once it is ready its clips, best first, each with its video when it has
    one (``renders``, by clip id). Clips marked Not recommended are counted, and listed only with
    ``show_all``."""
    if project.status == "failed":
        step = STAGE_TEXT.get(project.stage or "")
        where = f" while {step[0].lower()}{step[1:]}" if step else ""
        return [f"Stopped{where}: {clean(project.error or 'This project stopped.')}"]
    if project.status == "refused":
        return [f"Can't clip this video: {clean(project.error or 'ClipForge can not use it.')}"]
    if project.status == "waiting":
        return [f"Waiting: {clean(project.message)}" if project.message else "Waiting."]
    if project.status != "ready":
        step = STAGE_TEXT.get(project.stage or "", "waiting its turn")
        return [f"In progress ({round(project.progress * 100)}%): {step}."]
    if not clips:
        lines = ["Ready, but no moment in this video made a clip."]
        return [*lines, clean(project.message)] if project.message else lines
    lines = [f"Ready: {_clips(len(clips))}."]
    if project.message:
        lines.append(clean(project.message))
    shown = [c for c in clips if show_all or c.tier != "no"]
    if shown:
        lines += ["", *clip_table(shown, text=text, words=words or {}, renders=renders or {})]
    hidden = len(clips) - len(shown)
    if hidden:
        them = "it" if hidden == 1 else "them"
        lines.append(f"Left out: {_clips(hidden)} marked Not recommended. Add --all to see {them}.")
    return lines


def _clips(n: int) -> str:
    return "1 clip" if n == 1 else f"{n} clips"


def clip_table(
    clips: list[ClipRow],
    *,
    text: bool = False,
    words: dict[str, str] | None = None,
    renders: dict[str, RenderRow] | None = None,
) -> list[str]:
    """One row per clip, its columns lined up, its video (``renders``, by clip id) or why it failed under
    its row, and with ``text`` its details."""
    rows = [["#", "tier", "score", "time", "length", "hook title", "why"], *(_cells(c) for c in clips)]
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    lines = []
    for k, row in enumerate(rows):
        cells = [
            cell.rjust(w) if i in RIGHT else cell.ljust(w)
            for i, (cell, w) in enumerate(zip(row, widths, strict=True))
        ]
        lines.append("  ".join(cells).rstrip())
        if k:
            c = clips[k - 1]
            lines += [f"{PAD}{line}" for line in render_lines(c, (renders or {}).get(c.id))]
        if text and k:
            lines += details(clips[k - 1], words or {})
    return lines


def _cells(c: ClipRow) -> list[str]:
    title = (c.packaging or {}).get("hook_title") or next(iter(c.hook_titles), None) or "-"
    reason = " ".join((c.reason or "").split())
    if len(reason) > REASON_CHARS:
        reason = shortened(reason, REASON_CHARS - 3) + "..."
    return [
        "-" if c.rank is None else str(c.rank),
        c.tier or "-",
        "-" if c.final_score is None else str(round(c.final_score)),
        f"{clock(c.start_s)} to {clock(c.end_s)}",
        f"{round(c.end_s - c.start_s)} s",
        clean(title),
        clean(reason),
    ]


def render_lines(c: ClipRow, render: RenderRow | None) -> list[str]:
    """The clip's current video, its layout and why, and what QA found, the problems that block posting
    first; then, for a clip that failed for another reason, why. Its earlier video stays its current one
    until a new one is made, so it is the previous video then."""
    lines = []
    blocking: set[str] = set()
    if render is not None:
        failed = [check for check in render.qa.get("checks", []) if not check.get("ok")]
        hard = [check for check in failed if check.get("hard")]
        blocking = {check.get("message") or "" for check in hard}
        label = "Video" if c.status != "failed" or c.error in blocking else "Previous video"
        lines.append(f"{label}: {clean(render.path)}")
        if c.layout:
            why = clean(c.layout_reason or "").strip().rstrip(".")
            lines.append(f"Layout: {LAYOUT_NAMES.get(c.layout, c.layout)}." + (f" {why}." if why else ""))
        for check in hard:
            where = PLATFORM_TEXT.get(check.get("platform") or "", "posting")
            lines.append(f"Blocks {where}: {clean(check.get('message') or '')}")
        lines += [f"Note: {clean(check.get('message') or '')}" for check in failed if not check.get("hard")]
    if c.status == "failed" and (c.error or NO_VIDEO) not in blocking:
        lines.append(f"Failed: {clean(c.error or NO_VIDEO)}")
    return lines


def _render_of(conn: sqlite3.Connection, c: ClipRow) -> RenderRow | None:
    return repo.get_render(conn, c.current_render_id) if c.current_render_id else None


def details(c: ClipRow, words: dict[str, str]) -> list[str]:
    """A clip's words, what its review found, its flags and its post captions, indented under its row."""
    lines = [f"{PAD}{clean(words[sid])}" for sid in c.sentence_ids if sid in words]
    notes = (c.review or {}).get("notes")
    if notes:
        lines.append(f"{PAD}Review: {clean(notes)}")
    for label, kind in (("Not recommended", "red"), ("Watch out for", "soft")):
        flags = c.flags.get(kind) or []
        if flags:
            lines.append(f"{PAD}{label}: {'; '.join(FLAG_TEXT.get(f, f.replace('_', ' ')) for f in flags)}.")
    if "off_topic" in (c.flags.get("notes") or []):
        lines.append(f"{PAD}{OFF_TOPIC}")
    packaging = c.packaging or {}
    for label, key in (("TikTok", "tiktok"), ("Instagram", "instagram")):
        parts = [packaging.get(f"{key}_caption"), *(packaging.get(f"{key}_hashtags") or [])]
        caption = " ".join(part for part in parts if part)
        if caption:
            lines.append(f"{PAD}{label}: {clean(caption)}")
    if packaging.get("credit"):
        lines.append(f"{PAD}Credit: {clean(packaging['credit'])}")
    return [*lines, ""]


def transcript_words(paths: Paths, project_id: str) -> dict[str, str]:
    """Each sentence's words by its id, or nothing when the transcript isn't there or can't be read."""
    try:
        tr = Transcript.model_validate_json(
            paths.project(project_id).transcript_json.read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return {}
    return {s.id: s.text for s in tr.sentences}


# clipforge rerun


def cmd_rerun(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    conn = _db(paths)
    try:
        known = repo.get_project(conn, args.project) is not None
    finally:
        conn.close()
    if not known:
        print(f"No project {clean(args.project)}.", file=sys.stderr)
        return 1
    try:
        engine = open_engine(verbose=args.verbose)
    except ClipForgeError as err:
        if err.code != "busy":
            print(err.message, file=sys.stderr)
            return 1
        return _hand_over(paths, args.project, args.provider)
    try:
        events = engine.events.subscribe()
        if not engine.rerun_selection(args.project, provider=args.provider):
            print("This project's clips aren't made yet, so it carries on from where it stopped.")
            engine.start(args.project)
        return _follow(engine, events, [args.project], out=sys.stdout, as_json=False)
    except KeyboardInterrupt:
        return _interrupted(engine)
    finally:
        engine.close()


def _hand_over(paths: Paths, project_id: str, provider: str | None) -> int:
    """ClipForge is open in another window: the rerun goes in the database, and that window runs it."""
    from clipforge.jobs.pipeline import queue_only_runner, queue_rerun  # here: only this path needs them

    conn = _db(paths)
    runner = queue_only_runner(paths, repo.load_settings(conn))
    try:
        queued = queue_rerun(runner, conn, project_id, provider=provider)
    finally:
        runner.close()
        conn.close()
    if queued:
        print("ClipForge is open in another window, so the clips are made again there.")
        return 0
    if provider is not None:
        print(
            "ClipForge is open in another window and this project is still on its way there. Its next "
            f"steps use {PROVIDER_TEXT.get(provider, provider)}."
        )
    else:
        print(
            "ClipForge is open in another window and this project is still on its way there, so there is "
            "nothing to make again yet."
        )
    return 1


# clipforge render


def cmd_render(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    conn = _db(paths)
    try:
        known = repo.get_clip(conn, args.clip) is not None
    finally:
        conn.close()
    if not known:
        print(f"No clip {clean(args.clip)}.", file=sys.stderr)
        return 1
    edits = _edits(args)
    try:
        engine = open_engine(verbose=args.verbose)
    except ClipForgeError as err:
        if err.code != "busy":
            print(err.message, file=sys.stderr)
            return 1
        return _render_there(paths, args.clip, edits)
    try:
        try:
            queued = engine.render_clip(args.clip, edits)
        except ClipForgeError as err:  # an edit it can't use
            print(err.message, file=sys.stderr)
            return 1
        if not queued:  # removed by a new selection since
            print(f"No clip {clean(args.clip)}.", file=sys.stderr)
            return 1
        print(f"Making the video of clip {clean(args.clip)}.", flush=True)
        engine.wait(clips=[args.clip])
        conn = engine.db()
        clip = repo.get_clip(conn, args.clip)
        if clip is None:  # removed by a new selection while it rendered
            print(f"No clip {clean(args.clip)}.", file=sys.stderr)
            return 1
        for line in render_lines(clip, _render_of(conn, clip)):
            print(line)
        return 0 if clip.status == "rendered" else 1
    except KeyboardInterrupt:
        return _interrupted(engine)
    finally:
        engine.close()


def _edits(args: argparse.Namespace) -> dict[str, Any]:
    """The clip's edits from the flags given; --layout auto takes the layout edit off."""
    edits: dict[str, Any] = {}
    if args.layout:
        edits["layout"] = None if args.layout == "auto" else args.layout
    if args.captions:
        edits["preset"] = args.captions
    if args.best:
        edits["encoder"] = "best"
    return edits


def _render_there(paths: Paths, clip_id: str, edits: dict[str, Any]) -> int:
    """ClipForge is open in another window: the edits, checked here, and the render go in the database, and
    that window makes the video."""
    from clipforge.app import AS_IS_FIXED, checked_edits  # here: only this path needs them
    from clipforge.jobs.pipeline import queue_only_runner, queue_render, queue_review

    conn = _db(paths)
    settings = repo.load_settings(conn)
    runner = queue_only_runner(paths, settings)
    try:
        clip = repo.get_clip(conn, clip_id)
        if clip is not None and clip.mode == "as_is":
            print(AS_IS_FIXED, file=sys.stderr)
            return 1
        if clip is not None and edits:
            try:
                fields = checked_edits(conn, paths, clip, edits)
            except ClipForgeError as err:  # an edit it can't use
                print(err.message, file=sys.stderr)
                return 1
            repo.update_clip(conn, clip_id, **fields)
        queued = clip is not None and queue_render(runner, conn, clip_id)
        if queued:
            queue_review(runner, conn, clip_id, settings)  # checked again there too, as the window does
    finally:
        runner.close()
        conn.close()
    if not queued:
        print(f"No clip {clean(clip_id)}.", file=sys.stderr)
        return 1
    print("ClipForge is open in another window, so the clip renders there.")
    return 0


# clipforge library


def cmd_library(args: argparse.Namespace) -> int:
    from clipforge.library import gameplay  # here, so the other commands start quickly

    paths = Paths.from_env()
    conn = _db(paths)
    try:
        if args.action == "add":
            try:
                item = gameplay.add_gameplay(
                    conn, paths, args.file, credit=args.credit, license=args.license, notes=args.notes
                )
            except ClipForgeError as err:
                print(clean(err.message), file=sys.stderr)
                return 1
            print(f"Added {item.id}: {clean(item.name)} ({clock(item.duration_s or 0.0)}).")
            return 0
        items = repo.list_library(conn)
        if not items:
            print(EMPTY_LIBRARY)
        for item in items:
            n = len(gameplay.counted(conn, item.id))
            used = "used 1 time" if n == 1 else f"used {n} times"
            missing = "" if Path(item.path).is_file() else "  (its file is missing)"
            length = clock(item.duration_s or 0.0)
            print(f"{item.id}  {item.kind}  {clean(item.name)}  {length}  {used}{missing}")
            for label, value in (("Credit", item.credit), ("License", item.license), ("Notes", item.notes)):
                if value:
                    print(f"{PAD}{label}: {clean(value)}")
        return 0
    finally:
        conn.close()


# clipforge doctor


def cmd_doctor() -> int:
    checks = run_checks(Paths.from_env(), Settings())
    for c in checks:
        status = "ok" if c.ok else ("FAIL" if c.required else "note")
        print(f"{status:<5} {c.name:<13} {c.detail}")
        if not c.ok and c.fix:
            print(f"{'':<19}{c.fix}")
    failed = [c for c in checks if c.required and not c.ok]
    print(
        "All required checks passed." if not failed else "Fix the lines marked FAIL, then run doctor again."
    )
    return 1 if failed else 0


# clipforge service


def cmd_service(action: str) -> int:
    paths = Paths.from_env()
    try:
        if action == "status":
            now = service.status(paths)
            print(f"{now.sentence} (process {now.pid})" if now.pid else now.sentence)
            return 0
        paths.ensure()
        print(service.install(paths) if action == "install" else service.uninstall(paths))
        return 0
    except ClipForgeError as err:
        print(err.message, file=sys.stderr)
        return 1


def cmd_serve(port: int, *, open_window: bool = False) -> int:
    from clipforge.server.app import serve  # the web app loads only for the commands that serve it

    if open_window:
        threading.Thread(
            target=launcher.open_when_ready, args=(launcher.base_url(port),), daemon=True
        ).start()
    return serve(Paths.from_env(), port=port)


def cmd_assets_fetch() -> int:
    paths = Paths.from_env()
    paths.ensure()
    try:
        for line in assets.fetch_all(paths):
            print(line, flush=True)
    except ClipForgeError as err:
        print(err.message, file=sys.stderr)
        return 1
    return 0


# clipforge selftest


def cmd_selftest(args: argparse.Namespace) -> int:
    """The browser route's self-test (SPEC 17) on one account: a line per selector, then whether ClipForge
    can post there. Refused while ClipForge is open, as its window has the accounts' Chrome profiles."""
    try:
        engine = open_engine()
    except ClipForgeError as err:
        print(SELFTEST_ELSEWHERE if err.code == "busy" else err.message, file=sys.stderr)
        return 1
    try:
        from clipforge.post import store

        account, problem = _one_account(store.list_accounts(engine.db()), args.account)
        if account is None:
            print(problem, file=sys.stderr)
            return 1
        from playwright.sync_api import Error as PlaywrightError

        from clipforge.post.browser import instagram, selftest, tiktok

        words = {"tiktok": tiktok, "instagram": instagram}[account.platform]
        try:
            result = selftest.self_test(engine, account)
        except PlaywrightError as err:
            first = str(err).strip().splitlines()[:1] or ["it stopped without saying why"]
            print(NO_WINDOW.format(problem=clean(first[0])), file=sys.stderr)
            return 1
        if not result.logged_in:
            print(words.LOGGED_OUT.format(handle=clean(account.handle or account.label)))
            return 1
        for step in result.found:
            print(f"{'found':<8} {clean(step)}")
        for step in result.missing:
            print(f"{'MISSING':<8} {clean(step)}")
        print(words.CHANGED if result.missing else words.READY)
        return 1 if result.missing else 0
    finally:
        engine.close()


def _one_account(accounts: list[Any], given: str) -> tuple[Any, str]:
    """The browser account ``given`` names, by id or @handle (any case), or None and the sentence why not."""
    handle = given.removeprefix("@").casefold()
    named = [a for a in accounts if a.id == given or (a.handle and a.handle.casefold() == handle)]
    shown = clean(given)
    if not named:
        return None, f"No account is {shown}. The Accounts page lists them."
    if len(named) > 1:
        which = ", ".join(f"{a.id} ({PLATFORM_TEXT[a.platform]})" for a in named)
        return None, f"{shown} is more than one account: {which}. Give the one you mean by its id."
    account = named[0]
    if account.method != "browser":
        who = clean(f"@{account.handle}" if account.handle else account.label)
        site = PLATFORM_TEXT[account.platform]
        why = f"{who} posts through {site}'s API, so it has no page to check."
        return None, why + " Its card on the Accounts page checks it."
    return account, ""


def cmd_update_ytdlp() -> int:
    """The update, refused while ClipForge is open: its Doctor page updates without stopping a download."""
    from clipforge.app import take_home

    paths = Paths.from_env()
    paths.ensure()
    try:
        fd = take_home(paths)
    except ClipForgeError:
        print(OPEN_ELSEWHERE, file=sys.stderr)
        return 1
    try:
        result = update_ytdlp()
    finally:
        os.close(fd)
    if result.ok:
        print(result.message)
        return 0
    print(result.message, file=sys.stderr)
    for line in result.details.splitlines():
        print(f"  {clean(line)}", file=sys.stderr)
    return 1


# Printing what came from a video


def clean(text: object) -> str:
    """``text`` with each control character (escape codes, bells, line breaks) as a space."""
    return "".join(" " if unicodedata.category(ch) == "Cc" else ch for ch in str(text))


def to_json(value: object) -> str:
    """``value`` as JSON that keeps emoji and accents but has no raw control character in its strings."""
    text = json.dumps(value, indent=2, ensure_ascii=False)
    return "".join(
        f"\\u{ord(ch):04x}" if ch != "\n" and unicodedata.category(ch) == "Cc" else ch for ch in text
    )


def clock(seconds: float) -> str:
    """``m:ss``, or ``h:mm:ss`` from an hour on, to the nearest second."""
    hours, rest = divmod(round(max(0.0, seconds)), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def source_of(project: Project) -> str:
    """Where the project's video is from: its link, or for a file the file's path."""
    if project.source_kind == "file" and project.source_url:
        return str(file_from_url(project.source_url))
    return project.source_url or ""
