"""Stand-ins for the parts of ClipForge that are slow or outside it, and small builders for the brain's
inputs."""

from __future__ import annotations

import copy
import json
import os
import pwd
import re
import shutil
import threading
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

from clipforge.asr.base import ASRResult, RawWord
from clipforge.brain.context import BrainContext
from clipforge.brain.llm.base import LLMRequest, LLMResponse
from clipforge.config import Settings
from clipforge.errors import ClipForgeError
from clipforge.ingest.urls import youtube_id
from clipforge.ingest.ytdlp import DriveFolder
from clipforge.models import Comment, ProjectOptions, Sentence, Signals, SourceMeta, Transcript, Word
from clipforge.watch.models import ChannelListing
from tests.helpers import make_tone_wav

Handler = Callable[[LLMRequest], "dict | Exception"]

FIXTURES = Path(__file__).parent / "fixtures"
INFO_MIN = FIXTURES / "info_min.json"
SAMPLE_JSON3 = FIXTURES / "sample.json3"
LIVE_CHAT = FIXTURES / "live_chat_sample.json"
TITLE = "Why the deadline never scares him"
SCORES = {"C1": 7, "C2": 6, "C3": 6, "C4": 5, "C5": 5, "C6": 5, "C7": 4, "C9": 3}


class FakeProvider:
    """Answers each request with the handler for its pass: a dict is the answer, an exception is raised.
    Every request is recorded, from any thread."""

    name = "fake"

    def __init__(self, handlers: dict[str, Handler]):
        self.handlers = handlers
        self.requests: list[LLMRequest] = []
        self._lock = threading.Lock()

    def complete_json(self, req: LLMRequest) -> LLMResponse:
        with self._lock:
            self.requests.append(req)
        answer = self.handlers[req.pass_name](req)
        if isinstance(answer, Exception):
            raise answer
        return LLMResponse(data=answer, meta={})


def make_transcript(
    texts: list[str], *, seconds_each: float = 5.0, starts: list[float] | None = None
) -> Transcript:
    """Sentences S1 on, one per text, each ``seconds_each`` long with its words spread evenly. They run back
    to back unless ``starts`` gives each one's start."""
    starts = starts if starts is not None else [k * seconds_each for k in range(len(texts))]
    words, sentences = [], []
    for k, (text, start) in enumerate(zip(texts, starts, strict=True)):
        parts, w0 = text.split(), len(words)
        step = seconds_each / max(1, len(parts))
        for j, part in enumerate(parts):
            words.append(Word(i=len(words), text=part, start=start + j * step, end=start + (j + 1) * step))
        end = start + seconds_each
        sentences.append(Sentence(id=f"S{k + 1}", start=start, end=end, w0=w0, w1=len(words) - 1, text=text))
    return Transcript(
        engine="parakeet",
        engine_version="test",
        language="en",
        duration=starts[-1] + seconds_each if texts else 0.0,
        words=words,
        sentences=sentences,
    )


def quiet_signals() -> Signals:
    """No heatmap, comments, chat or caption events."""
    return Signals(
        has_heatmap=False,
        has_comments=False,
        has_chat=False,
        has_events=False,
        rms=[],
        heat_p80=None,
        max_bucket_weight=0.0,
        sentences={},
    )


def make_meta(tr: Transcript, **fields) -> SourceMeta:
    base = {
        "source_kind": "youtube",
        "video_id": "arj7oStGLkU",
        "title": "Inside the mind of a master procrastinator",
        "channel": "TED",
        "channel_url": "https://www.youtube.com/@TED",
        "duration_s": tr.duration,
        "description": "Tim Urban knows that procrastination doesn't make sense.",
        "language": "en",
    }
    return SourceMeta(**(base | fields))


def make_context(tr: Transcript, *, provider=None, **fields) -> BrainContext:
    """A run of project p0123456789 on Claude Code (answered by ``provider``), or Basic mode without one."""
    base = {
        "project_id": "p0123456789",
        "meta": make_meta(tr),
        "transcript": tr,
        "signals": quiet_signals(),
        "options": ProjectOptions(),
        "settings": Settings(),
        "provider": provider,
        "provider_name": "claude_cli" if provider is not None else "heuristic",
    }
    return BrainContext(**(base | fields))


def lines(n: int) -> list[str]:
    """``n`` different sentences of a talk, each read in 5 s by FakeSpeech."""
    return [f"Part {k} of the talk says the deadline is coming." for k in range(1, n + 1)]


class _Calls:
    """A record of the calls made to a fake, from any thread."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self._lock = threading.Lock()

    def _record(self, *call) -> None:
        with self._lock:
            self.calls.append(call)

    def called(self, name: str) -> list[tuple]:
        return [call for call in self.calls if call[0] == name]


class FakeYouTube(_Calls):
    """A YouTubeClient for one video: the sample info dict with these changes, a steady tone ``seconds``
    long as its audio, the captions, comments and live chat given (None for none), and ``video`` as its
    video download when given."""

    def __init__(
        self,
        *,
        seconds: float = 180.0,
        captions: Path | dict | None = SAMPLE_JSON3,
        comments: list[Comment] | tuple = (),
        live_chat: Path | None = None,
        video: Path | None = None,
        **info,
    ):
        super().__init__()
        base = json.loads(INFO_MIN.read_text(encoding="utf-8"))
        self.info = base | {"duration": seconds, "heatmap": [], "chapters": []} | info
        self.seconds = seconds
        self.captions = captions
        self.comments = list(comments)
        self.live_chat = live_chat
        self.video = video  # the file a video download gives, else a stand-in that is no video
        self.listing: ChannelListing | Exception | None = None  # what list_channel answers
        self.folder: DriveFolder | Exception | None = None  # what list_drive_folder answers
        self.videos: dict[str, dict | Exception] = {}  # other videos' changes to the info, by id

    def fetch_info(self, url: str) -> dict:
        self._record("fetch_info", url)
        video_id = youtube_id(url)
        if video_id is None or video_id not in self.videos:
            return copy.deepcopy(self.info)
        changes = self.videos[video_id]
        if isinstance(changes, Exception):
            raise changes
        return copy.deepcopy(self.info | {"id": video_id, "title": f"Video {video_id}"} | changes)

    def list_channel(self, url: str, limit: int) -> ChannelListing:
        self._record("list_channel", url, limit)
        if isinstance(self.listing, Exception):
            raise self.listing
        if self.listing is None:
            raise ClipForgeError("unavailable", "This fake has no channel listing.")
        return self.listing.model_copy(update={"entries": self.listing.entries[:limit]})

    def list_drive_folder(self, url: str, limit: int) -> DriveFolder:
        self._record("list_drive_folder", url, limit)
        if isinstance(self.folder, Exception):
            raise self.folder
        if self.folder is None:
            raise ClipForgeError("unavailable", "This fake has no Drive folder.")
        files = self.folder.files
        return replace(self.folder, files=files[:limit], more=self.folder.more or len(files) > limit)

    def download_audio(self, url: str, dest_dir: Path, *, progress=None) -> Path:
        self._record("download_audio", url)
        path = make_tone_wav(Path(dest_dir) / "audio.wav", self.seconds)
        if progress is not None:
            progress(1.0)
        return path

    def download_captions(self, url: str, dest_dir: Path, track: str) -> Path | None:
        self._record("download_captions", track)
        if self.captions is None:
            return None
        data = self.captions if isinstance(self.captions, dict) else json.loads(self.captions.read_text())
        path = Path(dest_dir) / f"subs.{track}.json3"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def fetch_comments(self, url: str, max_comments: int = 300) -> list[Comment]:
        self._record("fetch_comments", url)
        return self.comments[:max_comments]

    def download_live_chat(self, url: str, dest_dir: Path, *, timeout_s: float = 600.0) -> Path | None:
        self._record("download_live_chat", url)
        if self.live_chat is None:
            return None
        return Path(shutil.copyfile(self.live_chat, Path(dest_dir) / "chat.live_chat.json"))

    def download_video(self, url: str, dest_dir: Path, quality: str, *, progress=None) -> Path:
        self._record("download_video", quality)
        path = Path(dest_dir) / "video.mp4"
        if self.video is not None:
            shutil.copyfile(self.video, path)
        else:
            path.write_bytes(b"a stand-in for the video")
        if progress is not None:
            progress(1.0)
        return path

    def download_sections(
        self, url: str, dest_dir: Path, ranges, quality: str, *, cancelled=None
    ) -> list[Path]:
        self._record("download_sections", quality)
        return []


class FakeSpeech(_Calls):
    """A SpeechEngine that hears ``script``: each line is a sentence ``seconds_each`` long, back to back
    from ``start``, its words spread evenly. Asked about a window, it gives the words starting in it."""

    def __init__(
        self,
        script: list[str],
        *,
        seconds_each: float = 5.0,
        start: float = 0.0,
        name: str = "parakeet",
        language: str | None = None,
        ok: bool = True,
    ):
        super().__init__()
        self.name = name
        self.script = list(script)
        self.seconds_each = seconds_each
        self.start = start
        self.language = language
        self.ok = ok

    def available(self) -> bool:
        return self.ok

    def transcribe(self, wav: Path, *, language=None, window=None, beam=False, timeout_s=None) -> ASRResult:
        self._record("transcribe", window, beam)
        words = []
        for k, line in enumerate(self.script):
            t0, parts = self.start + k * self.seconds_each, line.split()
            step = self.seconds_each / max(1, len(parts))
            words += [
                RawWord(text=part, start=t0 + j * step, end=t0 + (j + 1) * step, conf=0.9)
                for j, part in enumerate(parts)
            ]
        if window is not None:
            words = [w for w in words if window[0] <= w.start < window[1]]
        return ASRResult(
            engine=self.name, engine_version="fake 1.0", language=self.language or language, words=words
        )


class NoFaces:
    """A face finder that finds nothing, so clip analysis runs offline."""

    def detect(self, frame, t) -> list:
        return []


def offline(url: str, max_bytes: int) -> bytes:
    raise OSError("the tests never fetch from the web")


class Clock:
    """A clock for the engine that only moves when a test moves it."""

    def __init__(self, at: datetime):
        self.at = at

    def __call__(self) -> datetime:
        return self.at

    def set(self, at: datetime) -> None:
        self.at = at

    def advance(self, **delta: float) -> datetime:
        self.at += timedelta(**delta)
        return self.at


class Pauses:
    """The engine's waits between requests to a site, kept instead of slept; each moves ``clock`` on by the
    wait, when there is one, as a real wait would."""

    def __init__(self, clock: Clock | None = None):
        self.clock = clock
        self.seconds: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.seconds.append(seconds)
        if self.clock is not None:
            self.clock.advance(seconds=seconds)


def no_campaign_sites(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"a test fetched {request.url} without a look-alike site")


class FakeProcess:
    """What a FakeCommands run gives back: running until a test or ``terminate`` ends it."""

    def __init__(self, args: list[str]):
        self.args = list(args)
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        if self.returncode is None:
            self.returncode = -15

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class FakeCommands:
    """The engine's ``run_cmd`` in tests: records each command and never starts a real one, so no test
    opens Finder or keeps the Mac awake."""

    def __init__(self) -> None:
        self.procs: list[FakeProcess] = []

    def __call__(self, args: list[str]) -> FakeProcess:
        proc = FakeProcess(args)
        self.procs.append(proc)
        return proc

    @property
    def calls(self) -> list[list[str]]:
        return [p.args for p in self.procs]

    def alive(self) -> list[FakeProcess]:
        return [p for p in self.procs if p.returncode is None]


class FakePublisher:
    """A publisher that posts nothing. Each publish records a copy of the post and takes the next outcome:
    a PublishResult to give back, an exception to raise, or a function of (post, report) to run. Once the
    outcomes run out, every publish succeeds. ``find_link`` records the post it looked for and takes the
    next of ``links``: a PublishResult, an exception to raise, or None (no row), which it gives once they run
    out."""

    def __init__(self, outcomes=(), *, platform: str = "tiktok", method: str = "browser"):
        from clipforge.post.plan import CAPS

        self.platform = platform
        self.method = method
        self.caps = CAPS[f"{platform}:{method}"]
        self.outcomes = list(outcomes)
        self.published: list = []
        self.checked: list = []
        self.links: list = []
        self.looked: list = []

    def check(self, account):
        from clipforge.post.models import Health

        self.checked.append(account)
        return Health(status="connected", sentence="Connected.")

    def publish(self, post, report):
        from clipforge.post.models import PublishResult

        self.published.append(post.model_copy(deep=True))
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if outcome is None:
            return PublishResult(external_id=f"x{len(self.published)}", url=None)
        if isinstance(outcome, BaseException):
            raise outcome
        if callable(outcome):
            return outcome(post, report)
        return outcome

    def find_link(self, post, report):
        self.looked.append(post.id)
        outcome = self.links.pop(0) if self.links else None
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def refuse_real_home() -> None:
    """Stop before an Engine opens your real ~/ClipForge: CLIPFORGE_HOME must name a test home, as
    tests/conftest.py sets it for every test. A probe or script outside the suite sets it to a temporary
    folder first."""
    from clipforge.paths import Paths

    set_to = os.environ.get("CLIPFORGE_HOME")
    real = {(home / "ClipForge").resolve() for home in (Path.home(), Path(pwd.getpwuid(os.getuid()).pw_dir))}
    if not set_to or Paths.from_env().home.resolve() in real:
        raise RuntimeError(
            "fake_engine opens only a test home: set CLIPFORGE_HOME to a temporary folder first"
        )


def fake_engine(
    *,
    youtube: FakeYouTube | None = None,
    speech: FakeSpeech | None = None,
    whisper: FakeSpeech | None = None,
    provider: FakeProvider | None = None,
    provider_factory: Callable | None = None,
    verbose: bool = False,
    publishers: dict | None = None,
    run_cmd: Callable | None = None,
    now: Callable[[], datetime] | None = None,
    **settings,
):
    """An Engine on the test home with fakes for YouTube, speech and the LLM. Without a provider every
    project runs in Basic mode, as it does with the provider "heuristic". Settings get min_free_gb 0, so a
    full test disk never stops a project, and Today's guide off unless ``today`` turns it on, so the path's
    steps join only the lists of the tests about it. ``publishers`` are keyed "<platform>:<method>";
    commands only ever reach a FakeCommands (the given one, else a new one at ``engine.run_cmd``)."""
    from clipforge.app import Engine  # here, so the tests that only need the fakes never import the app
    from clipforge.post.browser.session import BrowserSessions

    refuse_real_home()

    def pick(settings, name):
        return None if provider is None or name == "heuristic" else provider

    engine = Engine.open(
        settings_overrides={"min_free_gb": 0.0}
        | settings
        | {"today": {"guide": False} | settings.get("today", {})},
        youtube=youtube or FakeYouTube(),
        asr_engines={
            "parakeet": speech or FakeSpeech([]),
            "whisper": whisper or FakeSpeech([], name="whisper", ok=False),
        },
        provider_factory=provider_factory or pick,
        verbose=verbose,
        publishers=publishers,
        run_cmd=run_cmd or FakeCommands(),
        now=now,
    )
    engine.face_detector = NoFaces()  # never fetch the real model in tests
    engine.emoji_fetch = offline  # nor emoji pictures
    engine.thumb_fetch = offline  # nor thumbnails
    # Never a Chrome window on screen: a test that reaches the real TikTok route without the look-alike
    # pages runs it headless, and tests/conftest.py fails it for asking for www.tiktok.com.
    engine.browser = BrowserSessions(engine.paths, headless=True)
    # Campaign pages come from a test's look-alike site, and the waits between them are kept, not slept.
    engine.brief_transport = httpx.MockTransport(no_campaign_sites)
    engine.pause = Pauses(now if isinstance(now, Clock) else None)
    # Notifications look for the helper in the test's home, so ClipForge.app installed on this Mac never
    # changes what a test records: without it they go by osascript, recorded like every command.
    engine.notifier.app_dir = engine.paths.home / "Applications"
    return engine


def make_json3(texts: list[str], *, seconds_each: float = 5.0) -> dict:
    """A json3 caption file with one cue per text, back to back from 0, its words spread evenly."""
    events = []
    for k, text in enumerate(texts):
        parts = text.split()
        step = round(seconds_each * 1000 / max(1, len(parts)))
        segs = [
            {"utf8": part if j == 0 else f" {part}", "tOffsetMs": j * step} for j, part in enumerate(parts)
        ]
        events.append(
            {
                "tStartMs": round(k * seconds_each * 1000),
                "dDurationMs": round(seconds_each * 1000),
                "segs": segs,
            }
        )
    return {"events": events}


# Answers for every brain pass that hold up, for any transcript


def discover_answer(req: LLMRequest) -> dict:
    """A moment of seven sentences every ten sentences of the part asked about."""
    sids = re.findall(r"^(S\d+) \[", req.prompt, re.M)
    items = [
        {
            "start_sid": sids[k],
            "end_sid": sids[k + 6],
            "scores": SCORES,
            "evidence": [],
            "archetype": "story",
            "red_flags": [],
            "soft_flags": [],
            "hook_titles": [f"What happens at {sids[k]}", "The deadline is coming"],
            "topic_tags": ["deadlines"],
            "cold_open_sid": None,
            "reason": f"A setup at {sids[k]} and its payoff six sentences later.",
        }
        for k in range(0, len(sids) - 6, 10)
    ]
    return {"candidates": items}


def rerank_answer(req: LLMRequest) -> dict:
    """Every candidate asked about, scored lower the later it is listed."""
    ids = re.findall(r"^(k\d+) \[", req.prompt, re.M)
    return {
        "ranking": [
            {"id": cid, "score": max(0, 80 - 5 * n), "why": "A clear hook."} for n, cid in enumerate(ids)
        ]
    }


def rerank_about(*ids: str) -> Handler:
    """A rerank answer like ``rerank_answer`` that finds only the candidates ``ids`` on the topic asked
    for."""

    def handler(req: LLMRequest) -> dict:
        answer = rerank_answer(req)
        for item in answer["ranking"]:
            item["on_topic"] = item["id"] in ids
        return answer

    return handler


def package_answer(req: LLMRequest) -> dict:
    """Every clip asked about, on its own bounds, titled TITLE."""
    found = re.findall(r"^Clip (k\d+) \(\w+\) runs from (S\d+) to (S\d+) now", req.prompt, re.M)
    clips = [
        {
            "id": cid,
            "start_sid": first,
            "end_sid": last,
            "hook_title": TITLE,
            "hook_alts": ["He waited until the last day", "Deadlines are not the problem"],
            "mood": "curious",
            "energy": 3,
            "tiktok_caption": "The deadline is coming. Would you start today?",
            "tiktok_hashtags": ["#deadlines", "#procrastination"],
            "instagram_caption": "Why the deadline never scares him, in one minute.",
            "instagram_hashtags": ["#deadlines", "#procrastination"],
            "keywords": ["deadlines"],
        }
        for cid, first, last in found
    ]
    return {"clips": clips}


def review_answer(req: LLMRequest) -> dict:
    """Every clip asked about is complete and faithful, its first title true."""
    ids = re.findall(r"^Clip (k\d+) \(", req.prompt, re.M)
    verdict = {"notes": "", "complete_story": True, "meaning_changed": False, "true_title_index": 0}
    return {"clips": [{"id": cid, **verdict, "cards_supported": []} for cid in ids]}


def brain_handlers(**overrides: Handler) -> dict[str, Handler]:
    """FakeProvider handlers for every pass; ``overrides`` replace some."""
    handlers = {
        "discover": discover_answer,
        "rerank": rerank_answer,
        "package": package_answer,
        "review": review_answer,
    }
    return handlers | overrides


class MemoryKeyring(KeyringBackend):
    """A Keychain in a dict, so tests never read or write the real one."""

    priority = 1

    def __init__(self):
        super().__init__()
        self.items: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.items.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.items[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if self.items.pop((service, username), None) is None:
            raise PasswordDeleteError("not found")
