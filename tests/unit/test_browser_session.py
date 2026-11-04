"""The browser route's own machinery (SPEC 10.3, 17): one Chrome window at a time, each account's profile
folder private, a short random pause between steps, and the selectors that point at TikTok or, in tests,
at the look-alike pages on this Mac."""

import random
import stat
import threading
import time
from contextlib import contextmanager

import pytest

from clipforge.paths import Paths
from clipforge.post.browser.pacing import Pacer
from clipforge.post.browser.selectors import Selectors
from clipforge.post.browser.session import BrowserSessions
from clipforge.post.models import Account


class FakeContext:
    def __init__(self, log):
        self.log = log
        self.pages = []
        self.closed = False

    def set_default_timeout(self, ms):
        self.log.append(("timeout", ms))

    def route(self, pattern, handler):
        pass

    def on(self, name, handler):
        pass

    def new_page(self):
        page = object()
        self.pages.append(page)
        return page

    def close(self):
        self.closed = True


class FakePlaywright:
    def __init__(self, log):
        self.log = log
        self.chromium = self

    def launch_persistent_context(self, *args, **kwargs):
        self.log.append(("launch", args, kwargs))
        return FakeContext(self.log)


def fake_factory(log):
    @contextmanager
    def factory():
        yield FakePlaywright(log)

    return factory


def account(n: int = 1) -> Account:
    return Account(id=f"a{n}", platform="tiktok", method="browser", handle=f"h{n}", created_at=0.0)


def test_one_window_at_a_time(tmp_path):
    log = []
    sessions = BrowserSessions(Paths(tmp_path), playwright_factory=fake_factory(log))
    spans = []

    def use(n):
        with sessions.open(account(n)) as context:
            start = time.monotonic()
            time.sleep(0.15)
            spans.append((start, time.monotonic()))
            assert not context.closed

    threads = [threading.Thread(target=use, args=(n,)) for n in (1, 2, 3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    spans.sort()
    assert len(spans) == 3
    assert all(a_end <= b_start for (_, a_end), (b_start, _) in zip(spans, spans[1:], strict=False))
    launches = [entry for entry in log if entry[0] == "launch"]
    assert len(launches) == 3
    for _, args, kwargs in launches:
        assert args == (str(tmp_path / "browser" / args[0].rsplit("/", 1)[-1]),)
        assert kwargs == {"channel": "chrome", "headless": False, "locale": "en-US"}


def test_the_window_closes_when_the_block_ends(tmp_path):
    log = []
    sessions = BrowserSessions(Paths(tmp_path), headless=True, playwright_factory=fake_factory(log))
    with pytest.raises(RuntimeError), sessions.open(account()) as context:
        raise RuntimeError("the publish failed")
    assert context.closed
    with sessions.open(account()) as again:  # and the lock was let go
        assert not again.closed
    assert log[0][2]["headless"] is True


def test_profile_dir_is_private(tmp_path):
    paths = Paths(tmp_path)
    (paths.browser / "a1").mkdir(parents=True)
    paths.browser.chmod(0o755)
    (paths.browser / "a1").chmod(0o755)
    sessions = BrowserSessions(paths, playwright_factory=fake_factory([]))
    with sessions.open(account(1)):
        pass
    for folder in (paths.browser, paths.browser / "a1"):
        assert stat.S_IMODE(folder.stat().st_mode) == 0o700, folder
    assert sessions.profile_dir(account(2)) == paths.browser / "a2"
    bad = account(1).model_copy(update={"id": "../a1"})
    with pytest.raises(ValueError):
        sessions.profile_dir(bad)


def test_pacing_between_steps():
    slept = []
    pacer = Pacer(random.Random(7), sleep=slept.append)
    waits = [pacer.pause() for _ in range(200)]
    assert slept == waits
    assert all(0.4 <= w <= 1.5 for w in waits)
    assert max(waits) - min(waits) > 0.8  # spread over the range, not one fixed wait
    default = BrowserSessions(Paths("/nowhere")).pacer
    assert (default.low, default.high) == (0.4, 1.5)


def test_selectors_rebase_onto_the_test_origin():
    real = Selectors.load("tiktok")
    assert real.url("upload") == "https://www.tiktok.com/tiktokstudio/upload?from=upload&lang=en"
    test = Selectors.load("tiktok", base_url="http://127.0.0.1:5123/")
    assert test.url("upload") == "http://127.0.0.1:5123/tiktokstudio/upload?from=upload&lang=en"
    assert test.url("content") == "http://127.0.0.1:5123/tiktokstudio/content"
    assert test.url("login").startswith("http://127.0.0.1:5123/login")
    assert test.required == ["file_input"]
    assert test.candidates("file_input") == ["input[type=file]", "input[type='file'][accept='video/*']"]
    assert test.candidates("visibility_option", label="Friends") == [
        "xpath=//div[@role='option' and contains(., 'Friends')]"
    ]
    with pytest.raises(KeyError):
        test.url("nowhere")
