"""What the window is shown (SPEC 14, 18): each project as JSON with its steps, each clip as its card and
its editor show it, and every piece of text that came from outside (titles, channels, messages, file
names, transcripts, the AI's words) made safe to show first."""

from __future__ import annotations

import functools
import os
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple
from zoneinfo import ZoneInfo

from clipforge import repo
from clipforge.brain.snap import INSTAGRAM_MAX_S, TOO_LONG_S, TOO_SHORT_S, length_flags
from clipforge.brain.wording import credit_line
from clipforge.campaigns import money, tracker
from clipforge.campaigns import store as campaigns
from clipforge.campaigns.ending import campaign_state, end_reason
from clipforge.campaigns.links import KINDS, SOUND, WITHDRAW
from clipforge.campaigns.rules import campaign_of
from clipforge.edits import apply_packaging_edits, changes_review, effective_edits, render_key
from clipforge.errors import ClipForgeError
from clipforge.ingest.urls import canonical_url
from clipforge.jobs.pipeline import AS_IS, CLIP_FUNCTIONS, Flow, current_jobs, flow_for
from clipforge.jobs.stages import complete_file, read_meta, read_transcript
from clipforge.models import Packaging, ProjectOptions
from clipforge.post import store
from clipforge.post.lifecycle import NO_RETRY
from clipforge.post.models import PlanRequest
from clipforge.post.plan import build_plan, posts_today, who
from clipforge.post.publisher import is_experimental
from clipforge.render.edl import EDL, clip_words, sentence_ids
from clipforge.render.qa import QAResult
from clipforge.today.value import comparable
from clipforge.trends import store as trends_store
from clipforge.trends.shortlist import UNTITLED
from clipforge.untrusted import CONTROLS, TURNS, clean_lines, clean_text
from clipforge.watch import schedule
from clipforge.watch import store as watch_store
from clipforge.watch.models import SKIP_SENTENCES

if TYPE_CHECKING:
    from clipforge.app import Engine
    from clipforge.campaigns.models import Campaign, RuleCheck, Submission
    from clipforge.jobs.runner import Job
    from clipforge.models import ClipRow, Project, RenderRow
    from clipforge.post.models import Account, Plan, PlannedPost, PlanWarning, Post
    from clipforge.today.models import TodoItem
    from clipforge.trends.models import HashtagSet, Sound
    from clipforge.trends.recommend import Suggestion
    from clipforge.watch.models import ClipChannel, WatchCheck, WatchedSource, WatchItem

_NAME = str.maketrans(dict.fromkeys(CONTROLS) | TURNS | {ord(":"): None})  # a file name loses ":" too
NAME_MAX = 120

STEPS = (  # SPEC 14.3: each step on a project's card and the jobs it is made of
    ("metadata", "Metadata", ("ingest.metadata",)),
    ("download", "Download", ("ingest.audio", "ingest.captions", "ingest.extras")),
    ("transcribe", "Transcribe", ("asr.transcribe",)),
    ("signals", "Signals", ("signals.compute",)),
    ("select", "Find moments", ("brain.select",)),
    ("package", "Package", ("brain.package",)),
)
AS_IS_STEPS = (  # Post as it is's (SPEC 14.2): nothing listens to the video or cuts it, and nothing renders
    ("metadata", "Metadata", ("ingest.metadata",)),
    ("download", "Download", ("ingest.video",)),
    ("make", "Make the clip", ("asis.make",)),
)
JOB_LABELS = {  # what each job is doing, in the jobs list's words
    "ingest.metadata": "Reading the video's details",
    "ingest.audio": "Downloading the audio",
    "ingest.captions": "Getting YouTube's captions",
    "ingest.extras": "Getting comments and chapters",
    "ingest.video": "Downloading the video",
    "asr.transcribe": "Transcribing",
    "signals.compute": "Measuring the video's signals",
    "brain.select": "Finding the best moments",
    "brain.package": "Writing titles and captions",
    "asis.make": "Making the clip as it is",
    "clip.source": "Getting the clip's video",
    "clip.analyze": "Framing the clip",
    "clip.render": "Rendering",
    "clip.variant": "Rendering an account's version",
    "clip.review": "Re-checking the edited clip",
    "post.browser": "Posting in Chrome",
    "post.api": "Posting through Instagram",
    "account.login": "Log in in the Chrome window.",
    "account.selftest": "Checking the account's page",
    "watch.check": "Looking for new uploads",
    "library.starter": "Downloading starter gameplay",
}
# A login waits its turn in Chrome's lane, behind a post or a check, before its window opens.
QUEUED_LABELS = {
    "account.login": "Waiting for Chrome. The login window opens when the job before it is done."
}
WANTED = ("queued", "analyzing", "rendering", "rendered", "failed")  # clips asked to render, or rendered
IN_FLIGHT = ("queued", "analyzing", "rendering")
WINDOW_S = 30.0  # the transcript the editor shows either side of the clip
FLAGS = {  # SPEC 9.3: what each of the AI's flags means, as the card says it
    "meaning_distortion": "The cut makes the speaker seem to say something they didn't mean.",
    "no_faithful_hook": "No hook title fits it without exaggerating.",
    "missing_context": "It leaves out context viewers need, more than a fact card can give.",
    "defamation": "It rests on an unproven claim of wrongdoing against a real person.",
    "harmful_misinformation": "It may spread harmful misinformation or show a dangerous act.",
    "unsafe_content": "It has hate, harassment, sexual content or graphic violence, or features a minor.",
    "third_party_media": "It has music, TV, film, sports footage or another creator's video in it.",
    "private_info": "It shares a private person's personal information.",
    "political_campaign": "It campaigns for a party, a candidate or a vote.",
    "too_short": f"It is under {TOO_SHORT_S:g} seconds.",
    "too_long": f"It is over {TOO_LONG_S:g} seconds.",
    "incomplete_story": "The story isn't complete within the clip.",
    "duplicate": "It repeats a moment that was already posted.",
    "sponsor_read": "It has a sponsor read or an ad in it.",
    "crosstalk": "People talk over each other for a long stretch.",
    "early_profanity": "There is swearing in the first 3 seconds.",
    "long_monologue": "One person talks for over 20 seconds without a beat.",
    "jargon": "It uses terms a general viewer won't know.",
    "over_90s_instagram": f"It is over {INSTAGRAM_MAX_S:g} seconds, which is long for Instagram.",
}
LENGTH_FLAGS = ("too_short", "too_long", "over_90s_instagram")  # from where the clip is now, not where it was
UNCHECKED = "Not re-checked after your edits."  # a re-check of the clip as edited went unanswered
HELD = "{name} is paused, so this waits until you resume it in Accounts."  # a post its account holds


def job_json(engine: Engine, job: Job) -> dict[str, Any]:
    """A job as the jobs list shows it: what it is doing and the project (and clip) it is for."""
    conn = engine.db()
    clip_id = job.payload.get("clip_id", job.ref_id) if job.kind == "clip.variant" else job.ref_id
    clip = repo.get_clip(conn, clip_id) if job.kind.startswith("clip.") else None
    project = repo.get_project(conn, clip.project_id if clip else job.ref_id)
    return {
        "id": job.id,
        "kind": job.kind,
        "label": (job.status == "queued" and QUEUED_LABELS.get(job.kind))
        or JOB_LABELS.get(job.kind, "Working"),
        "ref_id": job.ref_id,
        "project": project.id if project else None,
        "project_title": clean_text(project.title) if project and project.title else None,
        "clip_rank": clip.rank if clip else None,
        "status": job.status,
        "progress": job.progress,
        "message": clean_text(job.message) if job.message else None,
        "started_at": job.started_at,
    }


def web_link(url: str | None) -> str | None:
    """``url`` when it is a plain web link, so nothing else (a ``javascript:`` link, say) becomes one."""
    if url is None or not url.lower().startswith(("https://", "http://")):
        return None
    return clean_text(url)


def clean_file_name(name: str) -> str:
    """A dropped file's name safe to keep: no folders, control characters, turning marks or ``:``, at most
    120 characters with its extension kept, and "video" when nothing is left of it."""
    name = name.replace(chr(92), "/").rpartition("/")[2].translate(_NAME)  # chr(92): Windows' separator
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    stem = stem.strip(" .") or "video"
    ext = f".{ext}" if dot else ""
    return stem[: max(1, NAME_MAX - len(ext))] + ext


def thumb_url(engine: Engine, project: Project) -> str | None:
    """Where the window gets the project's thumbnail: there once it is made, or once it can be (the
    metadata is saved and it hasn't failed before), as the first request makes it."""
    pp = engine.paths.project(project.id)
    if pp.thumb.is_file() or (pp.meta_json.is_file() and project.id not in engine.thumb_misses):
        return f"/media/projects/{project.id}/source/thumb.jpg"
    return None


def project_json(engine: Engine, project: Project) -> dict[str, Any]:
    """The project as the window shows it (SPEC 14.3). Discarded clips aren't counted."""
    clips = engine.clips(project.id)
    kept = [c for c in clips if c.discarded_at is None]
    return {
        "id": project.id,
        "title": clean_text(project.title),
        "channel": clean_text(project.channel),
        "channel_url": web_link(project.channel_url),
        "source_url": clean_text(project.source_url),
        "source_kind": project.source_kind,
        "duration_s": project.duration_s,
        "language": clean_text(project.language),
        "rights_basis": project.rights_basis,
        "campaign_id": project.campaign_id,
        "status": project.status,
        "stage": project.stage,
        "progress": project.progress,
        "message": clean_text(project.message),
        "error": clean_text(project.error),
        "error_code": project.error_code,
        "error_details": clean_text(project.error_details),
        "options": project.options.model_dump(mode="json"),
        "created_at": project.created_at,
        "updated_at": project.updated_at,
        "thumb": thumb_url(engine, project),
        "clip_count": len(kept),
        "rendered_count": sum(c.status == "rendered" for c in kept),
        "rerun_drops": repo.rerun_drops(clips, repo.posted_clips(engine.db(), project.id)),
        "source_on_disk": complete_file(engine.paths.project(project.id).source, "video") is not None,
        "steps": project_steps(project, engine.runner.jobs_for(project.id), clips),
    }


def project_steps(project: Project, jobs: list[Job], clips: list[ClipRow]) -> list[dict[str, Any]]:
    """The project's steps, each done, running, waiting, failed or todo with how far it is (0 to 1): the
    pipeline's from its jobs (oldest first, as ``jobs_for`` gives them) and Render from its clips. A
    project posted as it is has its own three, and no Render."""
    flow = flow_for(project)
    current = current_jobs(jobs, flow)
    now = time.time()
    steps = [
        {"key": key, "label": label, **_step([(kind, current.get(kind)) for kind in kinds], flow, now)}
        for key, label, kinds in (AS_IS_STEPS if flow is AS_IS else STEPS)
    ]
    if flow is AS_IS:
        return steps
    return [*steps, {"key": "render", "label": "Render", **_render_step(clips)}]


def _job_state(required: bool, job: Job | None, now: float) -> str:
    if job is None:
        return "todo"
    if job.status == "done" or (not required and job.status in ("failed", "cancelled")):
        return "done"  # an optional job that failed has had its turn
    if job.status in ("failed", "cancelled"):
        return "failed"
    if job.status == "running":
        return "running"
    return "waiting" if job.not_before > now else "queued"


def _step(jobs: list[tuple[str, Job | None]], flow: Flow, now: float) -> dict[str, Any]:
    required = {stage.kind: stage.required for stage in flow.stages}
    states = [_job_state(required[kind], job, now) for kind, job in jobs]
    if all(s == "done" for s in states):
        return {"status": "done", "progress": 1.0}
    made = sum(  # each job's share of the step by its weight: a done one in full, a running one so far
        flow.weights[kind] * (1.0 if state == "done" else job.progress if state == "running" else 0.0)
        for (kind, job), state in zip(jobs, states, strict=True)
    )
    total = sum(flow.weights[kind] for kind, _ in jobs)
    if "failed" in states:
        status = "failed"
    elif "running" in states:
        status = "running"
    elif "waiting" in states:
        status = "waiting"
    elif "done" in states:
        status = "running"  # part of it is done and the rest is on its way
    else:
        status = "todo"
    return {"status": status, "progress": round(made / total, 4)}


def _render_step(clips: list[ClipRow]) -> dict[str, Any]:
    wanted = [c for c in clips if c.discarded_at is None and c.status in WANTED]
    rendered = sum(c.status == "rendered" for c in wanted)
    if not wanted:
        status = "todo"
    elif any(c.status in IN_FLIGHT for c in wanted):
        status = "running"
    elif rendered == 0:
        status = "failed"
    else:
        status = "done"  # every clip asked for has had its turn, and some came out
    return {"status": status, "progress": round(rendered / len(wanted), 4) if wanted else 0.0}


# The clips


class EdlView(NamedTuple):
    """What the window needs from a render's edit list."""

    notes: tuple[str, ...]
    outs: dict[int, float]  # each word the render shows, by its place in the transcript: when it is said
    fps: float


def read_edl(path: str | None) -> EdlView | None:
    """The render's edit list as the window needs it, read again only once the file changes. None when
    it is missing or can't be read."""
    if not path:
        return None
    try:
        changed = os.stat(path).st_mtime_ns
    except OSError:
        return None
    return _edl_at(path, changed)


@functools.lru_cache(maxsize=256)
def _edl_at(path: str, changed: int) -> EdlView | None:
    try:
        edl = EDL.model_validate_json(Path(path).read_bytes())
    except (OSError, ValueError):
        return None
    return EdlView(tuple(edl.notes), {w.i: w.start for w in edl.words}, edl.fps)


def flag_text(code: str) -> str:
    """The flag as the card says it; one ClipForge doesn't know is the AI's code in words."""
    return (
        FLAGS.get(code) or f"The AI flagged this clip: {(clean_text(code) or '').replace('_', ' ').strip()}."
    )


def clip_flags(clip: ClipRow) -> list[dict[str, str]]:
    """The clip's red flags, then its soft ones, each once and as a sentence. The flags for its length are
    worked out from where it is now, so a clip that was moved has the right ones."""
    stored = clip.flags or {}
    red, soft = length_flags(clip.start_s, clip.end_s)
    levels: dict[str, str] = {}
    for level, codes in (
        ("red", [*_kept(stored.get("red")), *red]),
        ("soft", [*_kept(stored.get("soft")), *soft]),
    ):
        for code in codes:
            levels.setdefault(code, level)
    return [
        {"code": clean_text(code), "text": flag_text(code), "level": level} for code, level in levels.items()
    ]


def _kept(codes: list[str] | None) -> list[str]:
    return [code for code in codes or [] if isinstance(code, str) and code not in LENGTH_FLAGS]


def hook_title(clip: ClipRow) -> str | None:
    """The title the clip goes out with: the user's own, else the packaged one, else Discover's idea."""
    own = (clip.edits or {}).get("hook_title")
    packaged = (clip.packaging or {}).get("hook_title")
    return clean_text(own or packaged or (clip.hook_titles[0] if clip.hook_titles else None))


def media_url(engine: Engine, path: str | None) -> str | None:
    """Where the window gets one of a render's files, while it is there."""
    if not path:
        return None
    try:
        rel = Path(path).relative_to(engine.paths.home)
    except ValueError:
        return None
    return f"/media/{rel.as_posix()}" if (engine.paths.home / rel).is_file() else None


def _qa(render: RenderRow) -> QAResult | None:
    try:
        return QAResult.model_validate(render.qa) if render.qa else None
    except ValueError:
        return None


def render_json(engine: Engine, render: RenderRow) -> dict[str, Any]:
    """A rendered version as the window plays it: its video, poster and thumbnail, and whether its checks
    passed."""
    qa = _qa(render)
    return {
        "id": render.id,
        "version": render.version,
        "poster": media_url(engine, render.cover_path),
        "thumb": media_url(engine, render.thumb_path),
        "video": media_url(engine, render.path),
        "duration_s": render.duration_s,
        "size_bytes": render.size_bytes,
        "qa_ok": qa.passed if qa is not None else render.status == "ready",
    }


def render_notes(render: RenderRow | None) -> list[str]:
    """What the card says about the current render: what its edit list had to leave out, then the problems
    its checks found (the ones that stop a post first), each once and as a sentence."""
    if render is None:
        return []
    edl = read_edl(render.edl_path)
    qa = _qa(render)
    notes = [*(edl.notes if edl else ()), *(qa.problems() if qa else ())]
    sentences = (_sentence(clean_text(note) or "") for note in notes)
    return list(dict.fromkeys(s for s in sentences if s))


def recheck_notes(clip: ClipRow) -> list[str]:
    """What the card says when the clip's edits went unchecked (SPEC 14.4). A review the brain itself left
    out, before any edits, is not this."""
    edited = changes_review(clip.edits or {}) or "key" in (clip.review or {})
    return [UNCHECKED] if edited and "review_skipped" in (clip.flags or {}).get("notes", []) else []


def _sentence(text: str) -> str:
    text = text.strip()
    return text if not text or text.endswith((".", "!", "?")) else f"{text}."


def clip_card(engine: Engine, clip: ClipRow, project: Project | None = None) -> dict[str, Any]:
    """The clip as its card shows it (SPEC 14.4). ``edited`` is whether it has edits of the user's own (a
    moved range is one; another stretch of gameplay isn't), and ``stale`` whether its current render was
    made from other edits than it has now."""
    project = project or engine.project(clip.project_id)
    render = repo.get_render(engine.db(), clip.current_render_id) if clip.current_render_id else None
    jobs = engine.runner.jobs_for(clip.id)
    running = [job for job in jobs if job.status == "running" and job.kind in CLIP_FUNCTIONS]  # its render
    options = project.options if project is not None else ProjectOptions()
    made_from = render.edits_key if render is not None else None
    as_is = clip.mode == "as_is"
    return {
        "id": clip.id,
        "project_id": clip.project_id,
        "rank": clip.rank,
        "start_s": clip.start_s,
        "end_s": clip.end_s,
        "duration_s": round(clip.end_s - clip.start_s, 3),
        "final_score": clip.final_score,
        "tier": clip.tier,
        "passes_gates": clip.passes_gates,
        "archetype": clean_text(clip.archetype),
        "reason": clean_text(clip.reason),
        "hook_title": hook_title(clip),
        "layout": clean_text(clip.layout),
        "layout_reason": clean_text(clip.layout_reason),
        "flags": clip_flags(clip),
        "notes": [*render_notes(render), *recheck_notes(clip)],
        "status": clip.status,
        "error": clean_text(clip.error),
        "render": render_json(engine, render) if render is not None else None,
        "progress": running[-1].progress if running else None,
        "edited": any(key != "gameplay_seed" for key in clip.edits or {}),
        "stale": (
            not as_is
            and bool(made_from)
            and made_from != render_key(clip, effective_edits(options, clip.edits))
        ),
        "discarded": clip.discarded_at is not None,
        "posted_to": posted_to(engine, clip.id),
        "as_is": as_is,  # the whole video as it is (SPEC 14.2): nothing edits or renders it
    }


def clip_detail(engine: Engine, clip: ClipRow) -> dict[str, Any]:
    """The clip as its editor shows it (SPEC 14.5): its card with the AI's scores, evidence and review, its
    packaging as the AI made it and with the user's edits over it, its edits, every version rendered
    (oldest first, the clip's own: an account's version of it isn't listed), its project and the line its
    cold open would play."""
    project = engine.project(clip.project_id)
    ai = Packaging.model_validate(clip.packaging) if clip.packaging else None
    packaging = apply_packaging_edits(ai, clip.edits or {}) if ai is not None else None
    renders = [r for r in repo.list_renders(engine.db(), clip.id) if r.account_id is None]
    return clip_card(engine, clip, project) | {
        "scores": clip.scores,
        "evidence": cleaned(clip.evidence),
        "packaging": cleaned(packaging.model_dump(mode="json")) if packaging is not None else None,
        "packaging_ai": cleaned(ai.model_dump(mode="json")) if ai is not None else None,
        "edits": cleaned(clip.edits or {}),
        "renders": [render_json(engine, r) | {"created_at": r.created_at} for r in renders],
        "review": cleaned(clip.review),
        "sentence_ids": clip.sentence_ids,
        "project": _project_line(engine, project),
        "cold_open_line": _cold_open_line(engine, clip, ai),
    }


def _cold_open_line(engine: Engine, clip: ClipRow, packaging: Packaging | None) -> str | None:
    """The text of the line the AI picked for the clip's cold open, for the Look tab to quote."""
    sid = packaging.cold_open_sid if packaging is not None else None
    if sid is None:
        return None
    try:
        line = read_transcript(engine.paths.project(clip.project_id)).sentence(sid).text
    except (ClipForgeError, KeyError):  # no transcript, or no such line in it
        return None
    return clean_text(line) or None


def _project_line(engine: Engine, project: Project | None) -> dict[str, Any] | None:
    if project is None:
        return None
    try:
        credit = credit_line(
            read_meta(engine.paths.project(project.id)), project.rights_basis, project.options
        )
    except ClipForgeError:  # the metadata isn't there
        credit = None
    return {
        "id": project.id,
        "title": clean_text(project.title),
        "duration_s": project.duration_s,
        "rights_basis": project.rights_basis,
        "credit": clean_text(credit) or None,
    }


def transcript_window(engine: Engine, clip: ClipRow) -> dict[str, Any]:
    """The transcript around the clip for the editor (SPEC 14.5): 30 s either side of it within the video,
    each word with its sentence, whether it is struck, its fix, and when the current render says it (None
    for a word the render doesn't show), and each sentence with whether it is in the clip."""
    pp = engine.paths.project(clip.project_id)
    transcript = read_transcript(pp)
    try:
        duration = read_meta(pp).duration_s or transcript.duration
    except ClipForgeError:
        duration = transcript.duration
    w0, w1 = max(0.0, clip.start_s - WINDOW_S), min(duration, clip.end_s + WINDOW_S)
    words = [w for w in transcript.words if w.end > w0 and w.start < w1]
    sids = sentence_ids(transcript, words)
    inside = set(sentence_ids(transcript, clip_words(transcript, clip)).values())
    render = repo.get_render(engine.db(), clip.current_render_id) if clip.current_render_id else None
    edl = read_edl(render.edl_path) if render is not None else None
    edits = clip.edits or {}
    struck, fixes = set(edits.get("struck") or []), edits.get("word_fixes") or {}
    return {
        "range": [clip.start_s, clip.end_s],
        "orig_range": edits.get("orig_range"),
        "window": [round(w0, 3), round(w1, 3)],
        "fps": edl.fps if edl is not None else None,
        "words": [
            {
                "i": w.i,
                "text": clean_text(w.text),
                "start": w.start,
                "end": w.end,
                "sid": sids.get(w.i),
                "filler": w.filler,
                "struck": w.i in struck,
                "fix": clean_text(fixes.get(str(w.i))),
                "out": edl.outs.get(w.i) if edl is not None else None,
            }
            for w in words
        ],
        "sentences": [
            {"id": s.id, "start": s.start, "end": s.end, "in_clip": s.id in inside}
            for s in transcript.sentences
            if s.end > w0 and s.start < w1
        ],
    }


LINES = {"tiktok_caption", "instagram_caption"}  # written in lines, which the Copy tab shows and saves back


def cleaned(value: Any, key: Any = None) -> Any:
    """``value`` with every piece of text in it made safe to show, however deep it sits. A caption keeps its
    line breaks, so the editor's next save doesn't flatten them."""
    if isinstance(value, str):
        return clean_lines(value) if key in LINES else clean_text(value)
    if isinstance(value, list):
        return [cleaned(v, key) for v in value]
    if isinstance(value, dict):
        return {cleaned(k): cleaned(v, k) for k, v in value.items()}
    return value


def posted_to(engine: Engine, clip_id: str) -> list[dict[str, Any]]:
    """Where the clip went up, oldest first, still said after the account is removed."""
    conn = engine.db()
    where = []
    for post in store.posts_for_clip(conn, clip_id):
        if post.status != "posted":
            continue
        account = store.get_account(conn, post.account_id, deleted=True)
        where.append(
            {
                "post_id": post.id,
                "account_id": post.account_id,
                "platform": post.platform,
                "handle": clean_text(account.handle) if account else None,
                "url": web_link(post.url),
            }
        )
    return where


def account_json(engine: Engine, account: Account) -> dict[str, Any]:
    """An account as its card shows it (SPEC 14.7): how it is, today's posts against its cap and its last
    post. Never its token, its Keychain item, its Chrome profile or Meta's ids for it."""
    went = store.list_posts(engine.db(), status=("posted",), account_id=account.id)
    last = max(went, key=lambda p: p.posted_at or 0.0, default=None)
    return {
        "id": account.id,
        "platform": account.platform,
        "method": account.method,
        "label": clean_text(account.label),
        "handle": clean_text(account.handle),
        "display_name": clean_text(account.display_name),
        "avatar": media_url(engine, account.avatar_path),
        "status": account.status,
        "status_note": clean_text(account.status_note),
        "paused": account.paused,
        "ig_mode": account.ig_mode,
        "experimental": is_experimental(engine, account),
        "token_expires_at": account.token_expires_at,
        "last_checked_at": account.last_checked_at,
        "created_at": account.created_at,
        "settings": cleaned(account.settings.model_dump(mode="json")),
        "today": posts_today(engine, account),
        "last_post": None
        if last is None
        else {
            "id": last.id,
            "clip_title": clean_text(last.clip_title),
            "url": web_link(last.url),
            "posted_at": last.posted_at,
        },
    }


def waits_for_you(post: Post) -> str | None:
    """What the post's Chrome window waits for the person to do, while the site has the post: None
    otherwise, whatever a run that stopped left."""
    said = (post.state or {}).get("waiting")
    return clean_text(said) if post.status in ("uploading", "processing") and isinstance(said, str) else None


def post_json(engine: Engine, post: Post) -> dict[str, Any]:
    """A post as the Queue shows it (SPEC 14.6): what it says, where it goes, how it went and what the
    person can do about it."""
    account = store.get_account(engine.db(), post.account_id, deleted=True)
    state = post.state or {}
    actions = state.get("actions")
    actions = [a for a in actions if isinstance(a, str)] if isinstance(actions, list) else []
    if post.status == "needs_you" and account and account.method == "browser" and not account.deleted_at:
        actions += ["login"] if account.status == "needs_login" else []  # its Log in, till the login is back
    shot = state.get("screenshot")
    held = post.status == "scheduled" and account is not None and account.paused and not account.deleted_at
    return {
        "id": post.id,
        "batch_id": post.batch_id,
        "clip_id": post.clip_id,
        "clip_title": clean_text(post.clip_title),
        "account_id": post.account_id,
        "account_name": clean_text(who(account)) if account else None,
        "platform": post.platform,
        "method": post.method,
        "caption": clean_lines(post.caption),
        "hashtags": cleaned(post.hashtags),
        "options": post.options.model_dump(mode="json"),
        "scheduled_at": post.scheduled_at,
        "status": post.status,
        "attempts": post.attempts,
        "url": web_link(post.url),
        "error": clean_text(post.error),
        "error_code": clean_text(post.error_code),
        "details": clean_lines(post.details),
        "note": clean_text(HELD.format(name=who(account)) if held else post.note),
        "waiting": waits_for_you(post),
        "actions": actions,
        "retry_helps": not (post.status == "failed" and post.error_code in NO_RETRY),
        # TikTok Studio was given it: never uploaded again, so only Mark as posted and Cancel are offered
        "on_tiktok": post.status == "scheduled_on_tiktok" or bool(state.get("on_tiktok")),
        "screenshot": media_url(engine, shot) if isinstance(shot, str) else None,
        "posted_at": post.posted_at,
        "created_at": post.created_at,
        "updated_at": post.updated_at,
    }


def rule_json(check: RuleCheck) -> dict[str, Any]:
    return check.model_dump() | {"label": clean_text(check.label), "sentence": clean_text(check.sentence)}


def plan_json(engine: Engine, plan: Plan) -> dict[str, Any]:
    """A plan as the Post dialog shows it: the captions in their lines, and each post's campaign by name,
    with its rules."""
    conn = engine.db()
    linked = {p.campaign_id for p in plan.posts if p.campaign_id}
    names = {cid: c.name for cid in linked if (c := campaigns.get_campaign(conn, cid)) is not None}
    return {
        "plan_id": plan.plan_id,
        "posts": [
            p.model_dump(mode="json")
            | {
                "caption": clean_lines(p.caption),
                "hashtags": cleaned(p.hashtags),
                "rules": [rule_json(r) for r in p.rules],
                "campaign": {"id": p.campaign_id, "name": clean_text(names[p.campaign_id])}
                if p.campaign_id in names
                else None,
            }
            for p in plan.posts
        ],
        "warnings": [w.model_dump(mode="json") | {"sentence": clean_text(w.sentence)} for w in plan.warnings],
        "blocked": plan.blocked,
        "renders": plan.renders,
    }


# Clipping channels and their drafts (SPEC 10.9, 14.9)

CHECK_STOPPED = "ClipForge stopped this check before it finished. The next check tries again."
CHECK_ERRORS = {"rate_limited": schedule.SLOWED}
SKIPPED_AS = {  # what a check's skips are called when they all had the one reason
    "short": ("as a Short", "as Shorts"),
    "too_short": ("as too short", "as too short"),
    "too_long": ("as too long", "as too long"),
}


def _counted(n: int, word: str) -> str:
    """``n`` of ``word``, as "no drafts", "1 draft" or "6 drafts"."""
    return f"no {word}s" if n == 0 else f"1 {word}" if n == 1 else f"{n} {word}s"


def check_line(engine: Engine, check: WatchCheck, items: list[WatchItem]) -> str:
    """A check in one line, as the Channels card says it: "6:00 am: 2 new videos, 1 skipped as a Short,
    6 drafts". One from an earlier day starts with its day; ``items`` are the check's own."""
    zone = ZoneInfo(engine.settings.posting.timezone)
    started = datetime.fromtimestamp(check.started_at, zone)
    when = f"{started:%-I:%M %p}".lower()
    if started.date() != engine.now().astimezone(zone).date():
        when = f"{started:%a %-d %b}, {when}"
    if check.finished_at is None:
        return f"{when}: checking now"
    parts = [_counted(check.found, "new video")]
    if check.skipped:
        why = {i.reason for i in items if i.status == "skipped"}
        words = SKIPPED_AS.get(next(iter(why))) if len(why) == 1 else None
        parts.append(f"{check.skipped} skipped" + (f" {words[check.skipped != 1]}" if words else ""))
    if check.drafts is None:
        parts.append(f"{check.clipped} being clipped")
    else:
        parts.append(_counted(check.drafts, "draft"))
    if check.error:
        parts.append("stopped early")
    return f"{when}: {', '.join(parts)}"


def check_json(engine: Engine, check: WatchCheck, items: list[WatchItem]) -> dict[str, Any]:
    return {
        "id": check.id,
        "manual": check.manual,
        "started_at": check.started_at,
        "finished_at": check.finished_at,
        "found": check.found,
        "skipped": check.skipped,
        "clipped": check.clipped,
        "drafts": check.drafts,
        "error": None if check.error is None else CHECK_ERRORS.get(check.error, CHECK_STOPPED),
        "line": check_line(engine, check, items),
    }


def item_json(engine: Engine, item: WatchItem, channel: ClipChannel) -> dict[str, Any]:
    """A video a check saw, as its history lists it: what became of it, and why one was skipped."""
    watch = engine.settings.watch
    said = SKIP_SENTENCES.get(item.reason) if item.reason else None
    video_id = clean_text(item.video_id) or ""
    return {
        "id": item.id,
        "video_id": video_id,
        "title": clean_text(item.title),
        "url": canonical_url(video_id),
        "published_at": item.published_at,
        "status": item.status,
        "code": item.reason,
        "reason": said.format(
            min=f"{watch.min_minutes:g}",
            max=f"{watch.max_hours:g}",
            n=channel.daily_video_limit,
            name=_campaign_name(engine, channel) if item.reason == "not_allowed" else "",
        )
        if said
        else None,
        "project_id": item.project_id,
    }


def _campaign_name(engine: Engine, channel: ClipChannel) -> str:
    campaign = campaigns.get_campaign(engine.db(), channel.campaign_id) if channel.campaign_id else None
    return clean_text(campaign.name) if campaign else "it was linked to"


def source_json(source: WatchedSource) -> dict[str, Any]:
    title = (clean_text(source.title) or "").strip()
    return {
        "id": source.id,
        "title": title,
        "url": web_link(source.url),
        "newest_title": clean_text(source.newest_title),
        "newest_at": source.newest_published_at,
        "error": clean_text(source.error),
        "letter": (title[:1] or "?").upper(),  # the avatar until there is one
    }


def channel_json(engine: Engine, channel: ClipChannel, drafts: int = 0) -> dict[str, Any]:
    """A clipping channel as its card shows it (SPEC 14.9): its settings, sources and last check, when it
    checks next and when Check now opens again; ``drafts`` waiting for review; and the campaign it is linked
    to, with why it ended once it has (10.10)."""
    conn = engine.db()
    now = engine.now()
    linked = campaigns.get_campaign(conn, channel.campaign_id) if channel.campaign_id else None
    ended = schedule.ended(engine, channel, now) if linked else None
    last = next(iter(watch_store.checks_for(conn, channel.id, limit=1)), None)
    items = watch_store.items_of_check(conn, last.id) if last else []
    upcoming = schedule.next_check_at(engine, channel, now)
    again = schedule.check_now_after(engine, channel, now)
    return {
        "id": channel.id,
        "name": clean_text(channel.name),
        "paused": channel.paused,
        "account_ids": list(channel.account_ids),
        "options": cleaned(channel.options.model_dump(mode="json")),
        "rights_basis": channel.rights_basis,
        "check_time": channel.check_time,
        "daily_video_limit": channel.daily_video_limit,
        "clips_per_day": channel.clips_per_day,
        "on_limit": channel.on_limit,
        "sources": [source_json(s) for s in watch_store.list_sources(conn, channel.id) if not s.is_footage],
        "last_check": check_json(engine, last, items) if last else None,
        "last_line": check_line(engine, last, items) if last else None,
        "next_check_at": upcoming.timestamp() if upcoming else None,
        "check_now_after": again.timestamp() if again else None,
        "drafts": drafts,
        "campaign": {"id": linked.id, "name": clean_text(linked.name), "ended": clean_text(ended)}
        if linked
        else None,
    }


def _project_of(engine: Engine, post: Post) -> Project | None:
    clip = repo.get_clip(engine.db(), post.clip_id) if post.clip_id else None
    return repo.get_project(engine.db(), clip.project_id) if clip else None


def drafts_by_channel(engine: Engine) -> Counter[str | None]:
    """How many drafts wait for review, by the clipping channel that made them."""
    from clipforge.watch import drafts  # it imports this module

    counts: Counter[str | None] = Counter()
    for post in drafts.live_drafts(engine):
        project = _project_of(engine, post)
        counts[project.clip_channel_id if project else None] += 1
    return counts


def _review(engine: Engine, live: list[Post]) -> tuple[dict[str, PlannedPost], dict[str, list[PlanWarning]]]:
    """Each live draft as the review step would plan it now (its time and its campaign's rule checks) and
    its warnings, by plan key."""
    if not live:
        return {}, {}
    req = PlanRequest(draft_ids=[p.id for p in live])
    try:
        plan = build_plan(engine, req, plan_id="drafts", now=engine.now())
    except LookupError:  # one went while this was read; the next read leaves it out
        return {}, {}
    notes: dict[str, list[PlanWarning]] = defaultdict(list)
    for warning in plan.warnings:
        if warning.key is not None:
            notes[warning.key].append(warning)
    return {p.key: p for p in plan.posts}, notes


def _draft_card(
    engine: Engine,
    post: Post,
    clip: ClipRow,
    project: Project,
    planned: dict[str, PlannedPost],
    notes: dict[str, list[PlanWarning]],
) -> dict[str, Any]:
    key = f"{post.clip_id}:{post.account_id}"
    linked = None if post.state.get("off_campaign") else campaign_of(engine, project)
    render = repo.get_render(engine.db(), post.render_id) if post.render_id else None
    moved = next((w for w in notes.get(key, []) if w.code == "slot_moved"), None)
    return post_json(engine, post) | {
        "score": clip.final_score,
        "reasons": [clean_text(clip.reason)] if clip.reason else [],
        "media": render_json(engine, render) if render else None,
        "hook_title": hook_title(clip),
        "warnings": [
            w.model_dump(mode="json") | {"sentence": clean_text(w.sentence)}
            for w in notes.get(key, [])
            if w.code != "slot_moved"
        ],
        "slot_moved": {"at": planned[key].scheduled_at, "sentence": clean_text(moved.sentence)}
        if moved is not None and key in planned
        else None,
        "campaign": {"id": linked.id, "name": clean_text(linked.name)} if linked else None,
        "rules": [rule_json(r) for r in planned[key].rules] if linked and key in planned else [],
    }


def _video_json(engine: Engine, project: Project) -> dict[str, Any]:
    item = watch_store.item_of_project(engine.db(), project.id)
    return {
        "id": project.id,
        "title": clean_text(project.title),
        "channel": clean_text(project.channel),
        "thumb": thumb_url(engine, project),
        "published_at": item.published_at if item else None,
        "rights_basis": project.rights_basis,
    }


def _next_check(engine: Engine, channels: list[ClipChannel]) -> dict[str, Any] | None:
    """The clipping channel that checks next, and when; the first one on a tie."""
    now = engine.now()
    times = [(t, c) for c in channels if (t := schedule.next_check_at(engine, c, now)) is not None]
    if not times:
        return None
    at, channel = min(times, key=lambda tc: tc[0])
    return {"at": at.timestamp(), "name": clean_text(channel.name)}


def drafts_json(engine: Engine) -> dict[str, Any]:
    """The drafts to review (SPEC 14.9), grouped by clipping channel (oldest first, then any whose channel
    was removed), then by source video (its best clip first), each with its clip, score, warnings and
    where its slot moves when it must; and when the next check runs."""
    from clipforge.watch import drafts  # it imports this module

    conn = engine.db()
    live = drafts.live_drafts(engine)
    planned, notes = _review(engine, live)
    channels = watch_store.list_channels(conn)
    known = {c.id for c in channels}
    found: dict[str | None, dict[str, list[tuple[ClipRow, Post]]]] = defaultdict(lambda: defaultdict(list))
    projects: dict[str, Project] = {}
    for post in live:
        project = _project_of(engine, post)
        clip = repo.get_clip(conn, post.clip_id) if post.clip_id else None
        if project is None or clip is None:
            continue
        projects[project.id] = project
        cid = project.clip_channel_id if project.clip_channel_id in known else None
        found[cid][project.id].append((clip, post))

    def rank(pair: tuple[ClipRow, Post]) -> tuple[bool, float, float]:
        clip, post = pair
        return clip.final_score is None, -(clip.final_score or 0.0), post.scheduled_at

    groups = []
    for channel in [*channels, None]:
        videos = found.get(channel.id if channel else None)
        if not videos:
            continue
        shown = []
        for pid, cards in sorted(videos.items(), key=lambda pv: min(rank(c) for c in pv[1])):
            cards = sorted(cards, key=rank)
            shown.append(
                {
                    "project": _video_json(engine, projects[pid]),
                    "drafts": [
                        _draft_card(engine, post, clip, projects[pid], planned, notes) for clip, post in cards
                    ],
                }
            )
        named = {"id": channel.id, "name": clean_text(channel.name)} if channel else None
        groups.append({"channel": named, "videos": shown})
    return {"groups": groups, "count": len(live), "next_check": _next_check(engine, channels)}


MOVE_ORDER = ("to_submit", "submitted", "approved", "rejected", "paid")


def campaign_json(engine: Engine, campaign: Campaign) -> dict[str, Any]:
    """A campaign as its row on the Campaigns sheet shows it (SPEC 14.11): its fields made safe to show,
    why it ended once it has, the clipping channels linked to it, the ids of its links for Open, its
    money and counts so far, and what one of its currency is worth in US$ by your rates (1 with none), which
    the sheet sorts its amounts by."""
    conn = engine.db()
    today = tracker.day_of(engine, engine.now())
    linked = [watch_store.get_channel(conn, c) for c in campaigns.linked_channels(conn, campaign.id)]
    stored = {"page": campaign.campaign_url, "submit": campaign.submit_url}
    return cleaned(campaign.model_dump(mode="json", exclude={"notes", "rules_text"})) | {
        "campaign_url": web_link(campaign.campaign_url),
        "submit_url": web_link(campaign.submit_url),
        "notes": clean_lines(campaign.notes),
        "rules_text": clean_lines(campaign.rules_text),
        "ended": clean_text(end_reason(campaign, today)),
        "state": campaign_state(campaign, today),
        "channels": [{"id": c.id, "name": clean_text(c.name)} for c in linked if c is not None],
        "links": {kind: f"{campaign.id}:{kind}" if stored[kind] else None for kind in KINDS},
        "money": money.campaign_money(engine, campaign),
        "usd_rate": comparable(engine.settings, 1.0, campaign.currency)[0],
    }


def money_lines(engine: Engine) -> list[dict[str, Any]]:
    """The totals per marketplace and currency, each with the id of its Withdraw link when it has one."""
    return [
        line | {"withdraw_link": f"{WITHDRAW}{line['marketplace']}" if line["withdraw"] else None}
        for line in money.totals(engine)
    ]


def submission_json(
    engine: Engine, submission: Submission, campaign: Campaign | None = None
) -> dict[str, Any]:
    """A submission as its row on a campaign's page shows it (SPEC 14.11): where it stands and the moves it
    can make, the post it is of, the post's newest view count and what that should earn, and which count is
    due by hand today."""
    conn = engine.db()
    campaign = campaign or campaigns.get_campaign(conn, submission.campaign_id)
    post = store.get_post(conn, submission.post_id)
    account = store.get_account(conn, post.account_id, deleted=True) if post else None
    count = campaigns.latest_metric(conn, submission.post_id)
    views = count.views if count else None
    due = None
    if post is not None and campaign is not None:
        due = tracker.views_due(engine, post, campaign, tracker.day_of(engine, engine.now()))
    return submission.model_dump(mode="json") | {
        "reason": clean_text(submission.reason),
        "moves": [m for m in MOVE_ORDER if m in tracker.MOVES[submission.status]],
        "post": None
        if post is None
        else {
            "id": post.id,
            "platform": post.platform,
            "method": post.method,
            "account_name": clean_text(who(account)) if account else None,
            "clip_title": clean_text(post.clip_title),
            "posted_at": post.posted_at,
            "url": web_link(post.url),
        },
        "views": views,
        "views_source": count.source if count else None,
        "views_at": count.captured_at if count else None,
        "estimate": money.estimate(campaign, views) if campaign else None,
        "views_due": due,
    }


def todo_json(item: TodoItem) -> dict[str, Any]:
    """An item of the day's list as its row shows it (SPEC 14.10)."""
    return {
        "id": item.id,
        "kind": item.kind,
        "ref": clean_text(item.ref),
        "title": clean_text(item.title),
        "detail": clean_text(item.detail),
        "estimate_s": item.estimate_s,
        "value": item.value,
        "currency": clean_text(item.currency),
        "as_if_usd": item.as_if_usd,
        "guess": item.guess,
        "optional": item.optional,
        "due": item.due,
        "carried_over": item.carried_from is not None,
        "done": item.done,
        "done_how": item.done_how,
        "campaigns": dict(item.campaigns),
    }


def today_view(engine: Engine) -> dict[str, Any]:
    """The Today page (SPEC 14.10): the day's list in order with its 15 minutes, used and left, whether the
    morning has built it yet, the board around it and the path from nothing to your first payout."""
    # Here, as the day's needs reach this module through the drafts and the board reads its helpers
    from clipforge.today import board, day, path
    from clipforge.today import store as today_store

    tracker.sweep(engine)
    items = day.refresh(engine)
    today = day.today_of(engine)
    built = today_store.get_day(engine.db(), today.isoformat())
    return {
        "day": today.isoformat(),
        "budget_s": day.BUDGET_S,
        "used_s": day.used_s(engine, today.isoformat()),
        "left_s": day.left_s(items),
        "built": built is not None and built.notified_at is not None,
        "items": [todo_json(i) for i in items],
        "post": board.post_list(engine),
        "submit": board.submit_list(engine, today),
        "guidelines": board.guidelines(engine, today),
        "accounts": board.accounts(engine),
        "money": money_lines(engine),
        "best": board.best(engine, engine.now()),
        "path": path.view(engine, {i.kind for i in items if not i.done}),
    }


def _shown(text: str | None) -> str | None:
    """``text`` cleaned again on its way to the page, its runs of spaces made one; None when nothing is
    left."""
    return " ".join((clean_text(text) or "").split()) or None


def sound_json(sound: Sound, now: float) -> dict[str, Any]:
    """A shortlisted or trending sound as the Trends page and the pickers show it, its title and artist
    cleaned again on the way out, with whether it is stale and the id Open takes (None with no link)."""
    return sound.model_dump(mode="json") | {
        "title": _shown(sound.title) or UNTITLED,
        "artist": _shown(sound.artist),
        "stale": sound.stale(now),
        "link_id": f"{SOUND}{sound.id}" if sound.url else None,
    }


def suggestion_json(engine: Engine, suggestion: Suggestion) -> dict[str, Any]:
    """A track or sound suggested for a clip, its text cleaned again, with what a post needs to carry the
    sound (its id on the platform and its link) and the id Open takes. A track of yours has none of them."""
    sound = None if suggestion.kind == "music" else trends_store.get_sound(engine.db(), suggestion.id)
    return suggestion.model_dump(mode="json") | {
        "title": _shown(suggestion.title) or UNTITLED,
        "artist": _shown(suggestion.artist),
        "external_id": sound.external_id if sound else None,
        "url": sound.url if sound else None,
        "link_id": f"{SOUND}{sound.id}" if sound and sound.url else None,
    }


def set_json(tags: HashtagSet) -> dict[str, Any]:
    """A hashtag set, its text cleaned again on the way out."""
    return tags.model_dump(mode="json") | {
        "niche": _shown(tags.niche) or "",
        "name": _shown(tags.name) or "",
        "tags": [t for t in map(_shown, tags.tags) if t],
    }
