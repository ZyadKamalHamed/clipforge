from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx
import keyring
import pytest

from tests.fakes import MemoryKeyring

_THIS_MAC = {"127.0.0.1", "localhost", "::1"}


@pytest.fixture(autouse=True)
def clipforge_home(tmp_path, monkeypatch):
    """Every test gets its own ClipForge home so the real ~/ClipForge is never touched."""
    home = tmp_path / "clipforge-home"
    monkeypatch.setenv("CLIPFORGE_HOME", str(home))
    return home


@pytest.fixture(autouse=True)
def no_real_scout(tmp_path, monkeypatch):
    """Campaign Scout's file is a path in the test's own folder, so no test reads HeyClicky's real file."""
    path = tmp_path / "campaign-scout" / "campaigns.csv"
    monkeypatch.setenv("CLIPFORGE_SCOUT_FILE", str(path))
    return path


@pytest.fixture(autouse=True)
def memory_keyring():
    """Tokens and keys go to a dict for the test, never to the real Keychain."""
    before = keyring.get_keyring()
    fake = MemoryKeyring()
    keyring.set_keyring(fake)
    yield fake
    keyring.set_keyring(before)


@pytest.fixture(autouse=True)
def no_platform_network(monkeypatch):
    """Any real HTTP request to a host other than this Mac fails the test: no test reaches TikTok, Meta or
    anywhere else."""
    send = httpx.HTTPTransport.handle_request
    send_async = httpx.AsyncHTTPTransport.handle_async_request

    def guarded(self, request):
        if request.url.host not in _THIS_MAC:
            raise AssertionError(f"a test left this Mac for {request.url.host}")
        return send(self, request)

    async def guarded_async(self, request):
        if request.url.host not in _THIS_MAC:
            raise AssertionError(f"a test left this Mac for {request.url.host}")
        return await send_async(self, request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", guarded)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", guarded_async)


@pytest.fixture(autouse=True)
def no_real_commands(monkeypatch):
    """The engine's commands (``open`` for Finder, ``caffeinate`` to stay awake) never start for real in a
    test: one that reaches the real launcher fails."""

    def refused(args):
        raise AssertionError(f"a test started a real command: {args[0]}")

    monkeypatch.setattr("clipforge.post.power.launch", refused)


@pytest.fixture(autouse=True)
def no_real_launchctl(tmp_path, monkeypatch):
    """No test runs the real launchctl or writes to the real ~/Library/LaunchAgents: the background
    service's runner refuses, and its folder is one under the test's own."""
    from clipforge import service

    def refused(args):
        raise AssertionError(f"a test started a real command: {args[0]}")

    monkeypatch.setattr(service, "RUN", refused)
    monkeypatch.setattr(service, "AGENTS", tmp_path / "LaunchAgents")


@dataclass
class BrowserNetwork:
    """The hosts a test's browser windows asked for, and the ones that were stopped."""

    hosts: list[str] = field(default_factory=list)
    blocked: set[str] = field(default_factory=set)

    def route(self, route) -> None:
        host = urlsplit(route.request.url).hostname or ""
        self.hosts.append(host)
        if host in _THIS_MAC:
            route.continue_()
        else:
            self.blocked.add(host)
            route.abort()


@pytest.fixture(autouse=True)
def browser_network(monkeypatch):
    """Every request a ClipForge browser window makes in a test is routed: one for a host other than this
    Mac is stopped and fails the test, so no test reaches TikTok or Instagram."""
    from clipforge.post.browser import session

    seen = BrowserNetwork()
    launch = session.launch

    def guarded(pw, profile_dir, *, headless):
        context = launch(pw, profile_dir, headless=headless)
        context.route("**/*", seen.route)
        return context

    monkeypatch.setattr(session, "launch", guarded)
    yield seen
    assert not seen.blocked, f"a test's browser asked for {sorted(seen.blocked)}"
