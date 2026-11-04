"""The window in headless Chrome against a live server over the test home, with the fakes. Every test
here needs the built UI, and any console error it causes (a CSP violation is one) fails it."""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from clipforge import repo
from clipforge.briefs import run as brief_run
from clipforge.doctor import Check
from clipforge.jobs import pipeline
from clipforge.server.app import STATIC
from clipforge.server.routes import system
from tests.fakes import fake_engine
from tests.helpers import make_test_video
from tests.server import LiveServer
from tests.ui.seed import picture, render_clip

HERE = Path(__file__).parent
WEB = HERE.parents[1] / "web"
BUILD_FIRST = "Build the UI first: npm --prefix web run build"
BUILD_INPUTS = ("index.html", "vite.config.ts", "tsconfig.json", "package.json", "package-lock.json")


def pytest_collection_modifyitems(items):
    for item in items:
        if HERE in item.path.parents:
            item.add_marker(pytest.mark.ui)


def built_is_current() -> bool:
    """Whether the built UI is newer than everything it is built from (the tests beside the code aside)."""
    index = STATIC / "index.html"
    if not index.is_file():
        return False
    sources = [WEB / name for name in BUILD_INPUTS]
    for folder in (WEB / "src", WEB / "public"):
        sources += [p for p in folder.rglob("*") if p.is_file() and not p.name.endswith(".test.ts")]
    return index.stat().st_mtime >= max(p.stat().st_mtime for p in sources if p.exists())


@pytest.fixture(scope="module")
def browser():
    """Chrome for one test module, so no Playwright loop is left running under the other tests. Its sound
    goes to Chrome's own silent output rather than the Mac's, so a sound plays to its end without being
    heard, whatever the Mac's output is doing."""
    if not built_is_current():
        pytest.fail(BUILD_FIRST, pytrace=False)
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        chrome = pw.chromium.launch(channel="chrome", headless=True, args=["--disable-audio-output"])
        yield chrome
        chrome.close()


@dataclass
class Console:
    """The page's console errors, and the ones a test expects (a 401 while the server restarts, say)."""

    errors: list[str] = field(default_factory=list)
    allowed: list[re.Pattern[str]] = field(default_factory=list)
    listening: bool = True

    def record(self, text: str) -> None:
        if self.listening:
            self.errors.append(text)

    def allow(self, pattern: str) -> None:
        self.allowed.append(re.compile(pattern))

    def unexpected(self) -> list[str]:
        return [e for e in self.errors if not any(p.search(e) for p in self.allowed)]


@pytest.fixture
def console() -> Console:
    return Console()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    """Only what happens while the test runs counts: a server stopped after it cuts the window's event
    stream, which is no fault of the window's."""
    try:
        return (yield)
    finally:
        console = item.funcargs.get("console")
        if isinstance(console, Console):
            console.listening = False


@pytest.fixture
def page(browser, console):
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    tab = context.new_page()
    tab.on("console", lambda m: console.record(m.text) if m.type == "error" else None)
    tab.on("pageerror", lambda e: console.record(f"uncaught: {e}"))
    yield tab
    context.close()
    assert not console.unexpected(), "console errors: " + " | ".join(console.unexpected())


def never_update():
    raise AssertionError(
        "A UI test reached the real yt-dlp update. Give it a fake: server.app.state.update_ytdlp"
    )


def listening(engine, timeout: float = 10.0) -> None:
    """Wait until the window's event stream is subscribed, so an event published next reaches it."""
    until = time.monotonic() + timeout
    while not engine.events._subs:
        assert time.monotonic() < until, "The window never opened its event stream."
        time.sleep(0.02)


@contextmanager
def live_server(engine, port: int = 0) -> Iterator[LiveServer]:
    """The window's server over ``engine``, whose yt-dlp update fails the test unless the test swaps in a
    fake: the real one changes this checkout's packages."""
    with LiveServer(engine, port, static_dir=STATIC) as live:
        live.app.state.update_ytdlp = never_update
        yield live


# Every Doctor check passing: a page that asks never runs the real ffmpeg, yt-dlp or claude, and the
# Setup needed notice stays away unless a test swaps in a failing check.
ALL_OK = [
    Check("ffmpeg", True, "/opt/homebrew/bin/ffmpeg"),
    Check("yt-dlp", True, "2026.10.10"),
    Check("claude", True, "2.1.0 (Claude Code)"),
]


@pytest.fixture(autouse=True)
def doctor_ok(monkeypatch):
    monkeypatch.setattr(system, "run_checks", lambda paths, settings: list(ALL_OK))


@pytest.fixture(autouse=True)
def no_brief_reads(monkeypatch):
    """The server's timer never reads the brief sources by itself: a test has no Campaign Scout file, so the
    read would put "HeyClicky's campaign file is missing" on every Today. A test sets a source's result."""
    monkeypatch.setattr(brief_run, "tick", lambda engine: 0)


@pytest.fixture
def ui_engine():
    engine = fake_engine()
    # A thumbnail that can't be had is a 404, which Chrome logs as a console error, so here YouTube's is
    # made on the spot rather than fetched.
    engine.thumb_fetch = picture
    yield engine
    engine.close()


@pytest.fixture
def ui_server(ui_engine):
    with live_server(ui_engine) as server:
        yield server


@pytest.fixture
def server(ui_engine, monkeypatch):
    """The window with nothing running, so every project, clip and job stays as the test sets it."""
    monkeypatch.setattr(ui_engine.runner, "start", lambda: None)
    with live_server(ui_engine) as live:
        yield live


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    """A 2 s portrait video Chrome plays, made once for the module."""
    path = tmp_path_factory.mktemp("media") / "clip.mp4"
    make_test_video(path, seconds=2.0, size="360x640")
    return path


@pytest.fixture
def renders(monkeypatch, video):
    """An engine whose clips render to their next version, with ``video`` as its video, each render
    stopping halfway until the test lets it go on, and the window over it with its jobs running."""
    gate = threading.Event()

    def nothing(engine, ctx):
        return None

    def render(engine, ctx):
        ctx.progress(0.5)
        gate.wait(20)
        version = len(repo.list_renders(engine.db(), ctx.job.ref_id)) + 1
        render_clip(engine, ctx.job.payload["project"], ctx.job.ref_id, version=version, video=video)
        return None

    for kind, fn in (("clip.source", nothing), ("clip.analyze", nothing), ("clip.render", render)):
        monkeypatch.setitem(pipeline.CLIP_FUNCTIONS, kind, fn)
    engine = fake_engine()
    engine.thumb_fetch = picture
    with live_server(engine) as live:
        try:
            yield engine, live, gate
        finally:
            gate.set()
    engine.close()
