import json
import os
import plistlib
import signal
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from clipforge import assets, cli, launcher
from clipforge.app import BUSY, take_home
from clipforge.errors import ClipForgeError
from clipforge.paths import Paths
from clipforge.server.app import PORT_TAKEN
from tests.unit.test_assets import fake_model

ROOT = Path(__file__).resolve().parents[2]
CHROME = ["open", "-na", "Google Chrome", "--args"]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def answering(body: bytes, port: int = 0):
    """A small server on 127.0.0.1 giving ``body`` for every GET, standing in for whatever has the port."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


CLIPFORGE = json.dumps({"app": "clipforge", "version": "0.1.0"}).encode()


class Clock:
    """A clock that only moves when ``sleep`` is called, so waiting 15 s takes no time."""

    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def __call__(self) -> float:
        return self.now


class Runs:
    """Records each command, answering with the return code ``codes`` gives for it (0 when not named)."""

    def __init__(self, codes=None):
        self.calls: list[list[str]] = []
        self.codes = codes or {}

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        code = self.codes.get(argv[0] if argv[:2] != ["open", "-na"] else "chrome", 0)
        if isinstance(code, BaseException):
            raise code
        return subprocess.CompletedProcess(argv, code, "", "")


class Proc:
    def __init__(self, code=None):
        self.code = code

    def poll(self):
        return self.code


def no_popen(*args, **kwargs):
    raise AssertionError("started a server")


def test_health_counts_only_a_clipforge_answer():
    with answering(CLIPFORGE) as port:
        assert launcher.health(f"http://127.0.0.1:{port}/") == {"app": "clipforge", "version": "0.1.0"}
    with answering(b'{"app": "other"}') as port:
        assert launcher.health(f"http://127.0.0.1:{port}/") is None
    with answering(b"<html>not json</html>") as port:
        assert launcher.health(f"http://127.0.0.1:{port}/") is None
    assert launcher.health(f"http://127.0.0.1:{free_port()}/") is None


def test_open_app_reuses_a_running_server(tmp_path):
    runs = Runs()
    with answering(CLIPFORGE) as port:
        assert launcher.open_app(Paths(tmp_path), port, run=runs, popen=no_popen) == 0
    assert runs.calls == [[*CHROME, f"--app=http://127.0.0.1:{port}/"]]


def test_open_app_starts_a_server_and_waits(tmp_path):
    paths = Paths(tmp_path / "home")
    paths.logs.mkdir(parents=True)
    (paths.logs / "server.log").write_text("an earlier start\n")
    port, clock, runs, started = free_port(), Clock(), Runs(), []
    servers = []

    def popen(argv, **kwargs):
        started.append((argv, kwargs))
        return Proc()

    def sleep(seconds):
        clock.sleep(seconds)
        if len(clock.sleeps) == 3:  # the server answers on the fourth look
            servers.append(answering(CLIPFORGE, port))
            servers[-1].__enter__()

    try:
        code = launcher.open_app(paths, port, run=runs, popen=popen, sleep=sleep, clock=clock)
    finally:
        for server in servers:
            server.__exit__(None, None, None)
    assert code == 0
    [(argv, kwargs)] = started
    assert argv == [sys.executable, "-m", "clipforge", "serve", "--port", str(port)]
    assert kwargs["start_new_session"] is True
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.STDOUT
    assert Path(kwargs["stdout"].name) == paths.logs / "server.log"
    assert "a" in kwargs["stdout"].mode and kwargs["stdout"].closed
    assert (paths.logs / "server.log").read_text().startswith("an earlier start\n")
    env = kwargs["env"]
    assert env["CLIPFORGE_HOME"] == str(paths.home)
    assert {"/opt/homebrew/bin", str(Path.home() / ".local" / "bin")} <= set(env["PATH"].split(os.pathsep))
    assert clock.sleeps == [0.2, 0.2, 0.2]
    assert runs.calls == [[*CHROME, f"--app=http://127.0.0.1:{port}/"]]


def test_open_app_reports_a_server_that_never_starts(tmp_path, capsys):
    paths, clock, runs = Paths(tmp_path / "home"), Clock(), Runs()
    code = launcher.open_app(
        paths, free_port(), run=runs, popen=lambda *a, **k: Proc(), sleep=clock.sleep, clock=clock
    )
    assert code == 1
    assert capsys.readouterr().err == f"ClipForge didn't start. The log is at {paths.logs / 'server.log'}.\n"
    assert 15.0 <= clock.now < 15.5
    assert runs.calls == []


def test_open_app_stops_waiting_when_the_server_exits(tmp_path, capsys):
    paths, clock = Paths(tmp_path / "home"), Clock()
    code = launcher.open_app(
        paths, free_port(), run=Runs(), popen=lambda *a, **k: Proc(1), sleep=clock.sleep, clock=clock
    )
    assert code == 1
    assert "ClipForge didn't start." in capsys.readouterr().err
    assert clock.now < 1.0


def test_open_app_reports_another_app_on_the_port(tmp_path, capsys):
    runs = Runs()
    with answering(b'{"app": "something else"}') as port:
        assert launcher.open_app(Paths(tmp_path), port, run=runs, popen=no_popen) == 1
    assert capsys.readouterr().err == PORT_TAKEN.format(port=port) + "\n"
    assert runs.calls == []


def test_open_app_waits_for_a_clipforge_that_is_starting(tmp_path):
    """Two clicks on the app: the second finds the engine taken and waits for it rather than starting one."""
    paths = Paths(tmp_path / "home")
    paths.ensure()
    port, clock, runs, servers = free_port(), Clock(), Runs(), []

    def sleep(seconds):
        clock.sleep(seconds)
        if len(clock.sleeps) == 2:
            servers.append(answering(CLIPFORGE, port))
            servers[-1].__enter__()

    fd = take_home(paths)
    try:
        code = launcher.open_app(paths, port, run=runs, popen=no_popen, sleep=sleep, clock=clock)
    finally:
        os.close(fd)
        for server in servers:
            server.__exit__(None, None, None)
    assert code == 0
    assert runs.calls == [[*CHROME, f"--app=http://127.0.0.1:{port}/"]]


def test_open_app_reports_clipforge_busy_in_terminal(tmp_path, capsys):
    paths = Paths(tmp_path / "home")
    paths.ensure()
    clock = Clock()
    fd = take_home(paths)  # a clipforge run in Terminal holds the engine and serves nothing
    try:
        code = launcher.open_app(
            paths, free_port(), run=Runs(), popen=no_popen, sleep=clock.sleep, clock=clock
        )
    finally:
        os.close(fd)
    assert code == 1
    assert capsys.readouterr().err == BUSY + "\n"


def test_open_window_falls_back_to_the_default_browser():
    url = "http://127.0.0.1:8723/"
    runs = Runs({"chrome": 1})
    launcher.open_window(url, run=runs)
    assert runs.calls == [[*CHROME, f"--app={url}"], ["open", url]]

    runs = Runs({"chrome": OSError("no open")})
    launcher.open_window(url, run=runs)
    assert runs.calls == [[*CHROME, f"--app={url}"], ["open", url]]

    runs = Runs()
    launcher.open_window(url, run=runs)
    assert runs.calls == [[*CHROME, f"--app={url}"]]


def test_open_when_ready_opens_once_the_server_answers():
    port, clock, runs, servers = free_port(), Clock(), Runs(), []

    def sleep(seconds):
        clock.sleep(seconds)
        if len(clock.sleeps) == 1:
            servers.append(answering(CLIPFORGE, port))
            servers[-1].__enter__()

    try:
        launcher.open_when_ready(f"http://127.0.0.1:{port}/", run=runs, sleep=sleep, clock=clock)
    finally:
        for server in servers:
            server.__exit__(None, None, None)
    assert runs.calls == [[*CHROME, f"--app=http://127.0.0.1:{port}/"]]

    runs, clock = Runs(), Clock()
    launcher.open_when_ready(f"http://127.0.0.1:{free_port()}/", run=runs, sleep=clock.sleep, clock=clock)
    assert runs.calls == [] and clock.now >= 15.0


def test_cli_serve_and_open_arguments(monkeypatch, clipforge_home):
    import clipforge.server.app as server_app

    served, opened, waited = [], [], []
    monkeypatch.setattr(server_app, "serve", lambda paths=None, *, port: served.append((paths, port)) or 0)
    monkeypatch.setattr(launcher, "open_app", lambda paths, port: opened.append((paths.home, port)) or 0)
    monkeypatch.setattr(launcher, "open_when_ready", lambda url: waited.append(url))

    assert cli.main(["serve"]) == 0
    assert served[-1][1] == 8723 and waited == []
    assert cli.main(["serve", "--port", "9000", "--open"]) == 0
    assert served[-1][1] == 9000
    for _ in range(50):  # the window opens from a thread while the server runs
        if waited:
            break
        time.sleep(0.01)
    assert waited == ["http://127.0.0.1:9000/"]

    assert cli.main(["open"]) == 0
    assert cli.main(["open", "--port", "9001"]) == 0
    assert opened == [(clipforge_home, 8723), (clipforge_home, 9001)]

    for bad in (["open", "--port", "0"], ["serve", "--port", "65536"], ["open", "--port", "x"]):
        with pytest.raises(SystemExit) as exit_:
            cli.main(bad)
        assert exit_.value.code == 2


# A swiftc that writes a stand-in program where -o says, and a codesign that signs nothing; each writes
# the arguments it was given to <name>.log beside it.
SWIFTC = r"""#!/bin/bash
printf '%s\n' "$@" > "$(dirname "$0")/swiftc.log"
while (($#)); do
  if [[ "$1" == -o ]]; then printf '#!/bin/sh\n' > "$2"; chmod 755 "$2"; fi
  shift
done
"""
CODESIGN = r"""#!/bin/bash
printf '%s\n' "$@" > "$(dirname "$0")/codesign.log"
"""
HELPER = Path("ClipForge.app", "Contents", "Helpers", "ClipForge Notifier.app")
NOTIFIER = ROOT / "scripts" / "notifier" / "ClipForgeNotifier.swift"


def build_tools(tools: Path) -> dict[str, str]:
    tools.mkdir(exist_ok=True)
    for name, text in (("swiftc", SWIFTC), ("codesign", CODESIGN)):
        (tools / name).write_text(text)
        (tools / name).chmod(0o755)
    return {"SWIFTC": str(tools / "swiftc"), "CODESIGN": str(tools / "codesign")}


def make_app(tmp_path: Path, **env: str) -> subprocess.CompletedProcess:
    """scripts/make_app.sh into tmp_path/Applications, with the fake swiftc and codesign unless ``env``
    says otherwise."""
    return subprocess.run(
        ["bash", str(ROOT / "scripts" / "make_app.sh")],
        env={
            **os.environ,
            "APP_DIR": str(tmp_path / "Applications"),
            **build_tools(tmp_path / "tools"),
            **env,
        },
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_make_app_builds_a_valid_bundle(tmp_path):
    apps = tmp_path / "Applications"
    for _ in range(2):  # the second build replaces the first rather than nesting inside it
        done = make_app(tmp_path)
        assert done.returncode == 0, done.stderr
    assert [p.name for p in apps.iterdir()] == ["ClipForge.app"]
    contents = apps / "ClipForge.app" / "Contents"
    assert sorted(p.name for p in contents.iterdir()) == ["Helpers", "Info.plist", "MacOS"]
    info = plistlib.loads((contents / "Info.plist").read_bytes())
    assert info["CFBundleName"] == info["CFBundleDisplayName"] == "ClipForge"
    assert info["CFBundleIdentifier"] == "local.clipforge.launcher"
    assert info["CFBundleExecutable"] == "ClipForge"
    assert info["CFBundlePackageType"] == "APPL"
    assert info["LSUIElement"] is True
    assert info["LSMinimumSystemVersion"] == "13.0"
    exe = contents / "MacOS" / "ClipForge"
    assert os.access(exe, os.X_OK)
    text = exe.read_text()
    assert f'"{ROOT}/.venv/bin/clipforge" open' in text
    assert subprocess.run(["bash", "-n", str(exe)]).returncode == 0
    assert not list(tmp_path.glob("**/ClipForge.app/**/ClipForge.app"))


def test_make_app_builds_the_notifier_helper(tmp_path):
    done = make_app(tmp_path)
    assert done.returncode == 0, done.stderr
    helper = tmp_path / "Applications" / HELPER / "Contents"
    info = plistlib.loads((helper / "Info.plist").read_bytes())
    assert info["CFBundleIdentifier"] == "local.clipforge.launcher.notifier"
    assert info["CFBundleExecutable"] == "ClipForge Notifier"
    assert info["CFBundleDisplayName"] == info["CFBundleName"] == "ClipForge"  # the name on its notifications
    assert info["CFBundlePackageType"] == "APPL"
    assert info["LSUIElement"] is True
    assert os.access(helper / "MacOS" / "ClipForge Notifier", os.X_OK)
    compiled = (tmp_path / "tools" / "swiftc.log").read_text().splitlines()
    assert compiled[0] == "-O" and str(NOTIFIER) in compiled
    signed = (tmp_path / "tools" / "codesign.log").read_text().splitlines()
    assert signed[:3] == ["--force", "--sign", "-"] and signed[3].endswith("/" + str(HELPER))


def test_make_app_without_swiftc_skips_the_helper(tmp_path):
    done = make_app(tmp_path, SWIFTC=str(tmp_path / "no-swift" / "swiftc"))
    assert done.returncode == 0, done.stderr
    assert "xcode-select --install" in done.stdout
    contents = tmp_path / "Applications" / "ClipForge.app" / "Contents"
    assert sorted(p.name for p in contents.iterdir()) == ["Info.plist", "MacOS"]
    assert not (tmp_path / "tools" / "codesign.log").exists()


def test_make_app_carries_on_when_the_helper_wont_build(tmp_path):
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "swiftc").write_text("#!/bin/sh\necho 'error: no such module' >&2\nexit 1\n")
    (broken / "swiftc").chmod(0o755)
    done = make_app(tmp_path, SWIFTC=str(broken / "swiftc"))
    assert done.returncode == 0, done.stderr
    assert "couldn't be built" in done.stdout
    assert not (tmp_path / "Applications" / "ClipForge.app" / "Contents" / "Helpers").exists()


def test_notifier_opens_only_clipforge_pages_in_chrome():
    text = NOTIFIER.read_text()
    assert r'#"\Ahttp://127\.0\.0\.1:[0-9]{2,5}/[A-Za-z0-9/_-]*\z"#' in text  # notify.py's pattern
    assert '"/usr/bin/open"' in text and '["-na", "Google Chrome", "--args", "--app=\\(url)"]' in text
    assert "requestAuthorization" in text and "+ 30" in text


@pytest.mark.slow
def test_notifier_swift_typechecks():
    """The helper's code as the Swift compiler sees it. Only checked, never built or run."""
    if subprocess.run(["xcode-select", "-p"], capture_output=True).returncode != 0:
        pytest.skip("no Command Line Tools on this Mac")
    done = subprocess.run(
        ["/usr/bin/swiftc", "-typecheck", str(NOTIFIER)], capture_output=True, text=True, timeout=180
    )
    assert done.returncode == 0, done.stderr


def test_make_app_leaves_another_app_alone(tmp_path):
    other = tmp_path / "Applications" / "ClipForge.app" / "Contents"
    other.mkdir(parents=True)
    (other / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "com.example.clipforge"}))
    done = make_app(tmp_path)
    assert done.returncode == 1
    assert "is another app, so it was left alone" in done.stderr
    assert plistlib.loads((other / "Info.plist").read_bytes()) == {
        "CFBundleIdentifier": "com.example.clipforge"
    }
    assert sorted(p.name for p in (tmp_path / "Applications").iterdir()) == ["ClipForge.app"]


def test_install_script_is_strict():
    script = ROOT / "scripts" / "install.sh"
    text = script.read_text()
    assert subprocess.run(["bash", "-n", str(script)]).returncode == 0
    assert text.splitlines()[1] == "set -euo pipefail"
    assert "sudo" not in text
    steps = [
        "uv sync --frozen",
        "npm --prefix web ci --ignore-scripts",
        "npm --prefix web run build",
        ".venv/bin/clipforge assets fetch",
        ".venv/bin/clipforge doctor",
        "scripts/make_app.sh",
    ]
    places = [text.find(step) for step in steps]
    assert -1 not in places and places == sorted(places)


def fake_tools(bin_dir: Path, **versions) -> None:
    """Commands that only say who they are, in ``bin_dir``: brew, uv, ffmpeg and xcode-select, and node at a
    version."""
    bin_dir.mkdir()
    for name in ("brew", "uv", "ffmpeg", "node", "xcode-select"):
        if versions.get(name) is False:
            continue
        say = versions.get(name, f"{name} 1.0")
        (bin_dir / name).write_text(f"#!/bin/sh\necho '{say}'\n")
        (bin_dir / name).chmod(0o755)


def check_install(tmp_path: Path, swiftc: Path | None = None, **versions) -> subprocess.CompletedProcess:
    home = tmp_path / "home"
    (home / "Applications" / "Google Chrome.app").mkdir(parents=True)
    fake_tools(tmp_path / "bin", **versions)
    if swiftc is None:
        swiftc = Path(build_tools(tmp_path / "tools")["SWIFTC"])
    return subprocess.run(
        ["bash", str(ROOT / "scripts" / "install.sh"), "--check"],
        env={"PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin", "HOME": str(home), "SWIFTC": str(swiftc)},
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_install_check_passes_with_everything_there(tmp_path):
    done = check_install(tmp_path, node="v22.11.0")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "Everything ClipForge needs is here." in done.stdout
    assert "Swift" not in done.stdout


def test_install_check_names_a_missing_swift_compiler(tmp_path):
    done = check_install(tmp_path, swiftc=tmp_path / "no-swift" / "swiftc", node="v22.11.0")
    assert done.returncode == 0, done.stdout + done.stderr  # notifications still come, through osascript
    assert "Swift compiler" in done.stdout and "xcode-select --install" in done.stdout
    assert "Everything ClipForge needs is here." in done.stdout


def test_install_check_names_what_is_missing(tmp_path):
    done = check_install(tmp_path, ffmpeg=False, node="v20.18.0")
    assert done.returncode == 1
    assert "brew install ffmpeg" in done.stdout
    assert "Node.js 22 or later" in done.stdout and "v20.18.0" in done.stdout
    assert "brew install uv" not in done.stdout  # uv is there, so it isn't named


class FakeSpeech:
    size = "about 2.5 GB"

    def __init__(self, ready=False, error=None):
        self.ready, self.error, self.prepared = ready, error, 0

    def model_ready(self):
        return self.ready

    def prepare(self):
        self.prepared += 1
        if self.error:
            raise self.error
        self.ready = True


def test_assets_fetch_command(monkeypatch, capsys, clipforge_home):
    fake_model(monkeypatch, b"model bytes")
    fetched, speech = [], FakeSpeech()
    monkeypatch.setattr(assets, "fetch_bytes", lambda url, max_bytes: fetched.append(url) or b"model bytes")
    monkeypatch.setattr(assets, "speech_engine", lambda paths: speech)

    assert cli.main(["assets", "fetch"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "Downloaded the face model.",
        "Downloading the speech model (about 2.5 GB). It takes a while the first time.",
        "Downloaded the speech model.",
    ]
    assert len(fetched) == 1 and speech.prepared == 1
    assert assets.model_ready(Paths(clipforge_home))

    assert cli.main(["assets", "fetch"]) == 0
    assert capsys.readouterr().out.splitlines() == ["The face model is ready.", "The speech model is ready."]
    assert len(fetched) == 1 and speech.prepared == 1


def test_assets_fetch_command_reports_a_failure(monkeypatch, capsys, clipforge_home):
    fake_model(monkeypatch, b"model bytes")
    monkeypatch.setattr(assets, "fetch_bytes", lambda url, max_bytes: b"wrong bytes")
    monkeypatch.setattr(assets, "speech_engine", lambda paths: FakeSpeech())
    assert cli.main(["assets", "fetch"]) == 1
    assert "Couldn't download the yunet model" in capsys.readouterr().err

    capsys.readouterr()
    monkeypatch.setattr(assets, "fetch_bytes", lambda url, max_bytes: b"model bytes")
    failing = FakeSpeech(error=ClipForgeError("asr_model_timeout", "The speech model took too long."))
    monkeypatch.setattr(assets, "speech_engine", lambda paths: failing)
    assert cli.main(["assets", "fetch"]) == 1
    assert capsys.readouterr().err == "The speech model took too long.\n"


def test_serve_end_to_end_on_a_free_port(clipforge_home):
    """A real server on a tmp home answers health, and SIGTERM stops it cleanly with the engine freed."""
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "clipforge", "serve", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 30
        while launcher.health(f"http://127.0.0.1:{port}/") is None:
            assert proc.poll() is None, proc.communicate()[0]
            assert time.monotonic() < deadline, "the server never answered"
            time.sleep(0.2)
        proc.send_signal(signal.SIGTERM)
        out, _ = proc.communicate(timeout=20)
        assert proc.returncode == 0, out
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    os.close(take_home(Paths(clipforge_home)))  # the lock is free again
    assert launcher.health(f"http://127.0.0.1:{port}/") is None


def test_scripts_are_executable():
    for name in ("install.sh", "make_app.sh"):
        assert os.access(ROOT / "scripts" / name, os.X_OK)
