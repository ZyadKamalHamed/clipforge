"""The background service (SPEC 10.7): the LaunchAgent's plist and the launchctl calls, all through a
recording runner and a folder under tmp_path, so no test touches the real launchctl or LaunchAgents."""

from __future__ import annotations

import plistlib
import subprocess

import pytest

from clipforge import service
from clipforge.errors import ClipForgeError
from clipforge.paths import Paths

UID = 501


class Recorder:
    def __init__(self, answers: dict[str, tuple[int, str]] | None = None):
        self.calls: list[list[str]] = []
        self.answers = answers or {}

    def __call__(self, args: list[str]) -> subprocess.CompletedProcess:
        self.calls.append(args)
        code, out = self.answers.get(args[1], (0, ""))
        return subprocess.CompletedProcess(args, code, out, "")


def _paths(tmp_path, name="home") -> Paths:
    paths = Paths(tmp_path / name)
    paths.ensure()
    return paths


def test_plist_holds_only_the_engine_env(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", "tok-not-for-disk")
    paths = _paths(tmp_path, "Zyad's Mac stuff")
    agents = tmp_path / "agents"
    service.install(paths, agents=agents, run=Recorder(), uid=UID, python="/py/bin/python")
    got = plistlib.loads(service.plist_path(agents).read_bytes())
    assert got["Label"] == service.LABEL == "com.clipforge.engine"
    assert got["ProgramArguments"] == ["/py/bin/python", "-m", "clipforge", "serve", "--port", "8723"]
    assert got["RunAtLoad"] is True
    assert got["KeepAlive"] == {"SuccessfulExit": False}
    assert got["ThrottleInterval"] == 30
    assert got["WorkingDirectory"] == str(paths.home)
    assert got["StandardOutPath"] == got["StandardErrorPath"] == str(paths.logs / "server.log")
    assert set(got["EnvironmentVariables"]) == {"CLIPFORGE_HOME", "PATH", "PYTHONUNBUFFERED"}
    assert got["EnvironmentVariables"]["CLIPFORGE_HOME"] == str(paths.home)
    assert "tok-not-for-disk" not in service.plist_path(agents).read_text()


def test_install_twice_and_uninstall_nothing(tmp_path):
    paths = _paths(tmp_path)
    agents = tmp_path / "agents"
    run = Recorder({"bootout": (3, "")})  # 3: nothing was loaded, which is fine
    target = str(service.plist_path(agents))
    for _ in range(2):
        assert service.install(paths, agents=agents, run=run, uid=UID) == service.INSTALLED
    assert (
        run.calls
        == [
            ["launchctl", "bootout", f"gui/{UID}/{service.LABEL}"],
            ["launchctl", "bootstrap", f"gui/{UID}", target],
        ]
        * 2
    )
    assert service.uninstall(paths, agents=agents, run=run, uid=UID) == service.REMOVED
    assert not service.plist_path(agents).exists()
    run.calls.clear()
    assert service.uninstall(paths, agents=agents, run=run, uid=UID) == service.NOT_INSTALLED
    assert service.NOT_INSTALLED == "The background service isn't installed."
    assert run.calls == []


def test_failed_bootstrap_is_a_sentence(tmp_path):
    run = Recorder({"bootstrap": (5, "")})
    with pytest.raises(ClipForgeError) as caught:
        service.install(_paths(tmp_path), agents=tmp_path / "agents", run=run, uid=UID)
    assert caught.value.code == "service_failed"
    assert caught.value.message == service.FAILED.format(code=5)


def test_status_reads_launchctl_print(tmp_path):
    paths = _paths(tmp_path)
    agents = tmp_path / "agents"
    off = service.status(paths, agents=agents, run=Recorder(), uid=UID)
    assert (off.installed, off.running, off.pid, off.sentence) == (False, False, None, service.OFF)
    service.install(paths, agents=agents, run=Recorder(), uid=UID)
    printed = "gui/501/com.clipforge.engine = {\n\tstate = running\n\tpid = 4242\n}\n"
    on = service.status(paths, agents=agents, run=Recorder({"print": (0, printed)}), uid=UID)
    assert (on.installed, on.running, on.pid, on.sentence) == (True, True, 4242, service.RUNNING)
    down = service.status(paths, agents=agents, run=Recorder({"print": (113, "")}), uid=UID)
    assert (down.installed, down.running, down.pid, down.sentence) == (True, False, None, service.STOPPED)


def test_missing_launchctl_is_a_sentence(tmp_path):
    def missing(args):
        raise FileNotFoundError(args[0])

    with pytest.raises(ClipForgeError) as caught:
        service.install(_paths(tmp_path), agents=tmp_path / "agents", run=missing, uid=UID)
    assert caught.value.code == "no_launchctl"
    assert caught.value.message == (
        "macOS's launchctl isn't on this Mac, so the background service can't be added."
    )


def test_the_service_never_boots_itself_out_while_answering(tmp_path, monkeypatch):
    """Removing the service from the engine it started answers first, then stops that engine."""
    paths = _paths(tmp_path)
    agents = tmp_path / "agents"
    service.install(paths, agents=agents, run=Recorder(), uid=UID)
    monkeypatch.setenv("XPC_SERVICE_NAME", service.LABEL)
    later: list = []
    run = Recorder()
    got = service.uninstall(paths, agents=agents, run=run, uid=UID, later=later.append)
    assert got == service.REMOVED_SELF == "Removed. ClipForge stops now; open it again from the app."
    assert run.calls == [] and not service.plist_path(agents).exists()
    later[0]()
    assert run.calls == [["launchctl", "bootout", f"gui/{UID}/{service.LABEL}"]]
    assert service.install(paths, agents=agents, run=run, uid=UID) == service.ALREADY


def test_default_runner_is_refused_in_tests(tmp_path):
    with pytest.raises(AssertionError):
        service.RUN(["launchctl", "print", "gui/501/com.clipforge.engine"])


def test_the_service_waits_quietly_for_the_window_engine(tmp_path, capsys):
    """Installed while the window's engine holds the home, the service's engine says so once and waits,
    rather than failing every 30 seconds into the log."""
    answers = iter([True, True, False])
    naps: list[float] = []
    service.wait_for_home(_paths(tmp_path), in_use=lambda paths: next(answers), sleep=naps.append)
    assert naps == [service.HANDOVER_S, service.HANDOVER_S]
    assert capsys.readouterr().err.strip() == service.WAITING
    service.wait_for_home(_paths(tmp_path), in_use=lambda paths: False, sleep=naps.append)
    assert capsys.readouterr().err == ""


def test_only_the_service_waits_for_the_home(tmp_path, monkeypatch):
    from clipforge.server import app as server_app

    waited: list = []
    monkeypatch.setattr(service, "wait_for_home", lambda paths: waited.append(paths.home))
    monkeypatch.setattr(server_app, "_serve", lambda paths, port: 7)
    assert server_app.serve(_paths(tmp_path), port=1) == 7 and waited == []
    monkeypatch.setenv("XPC_SERVICE_NAME", service.LABEL)
    assert server_app.serve(_paths(tmp_path), port=1) == 7 and waited == [tmp_path / "home"]
