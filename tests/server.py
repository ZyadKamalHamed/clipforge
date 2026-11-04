"""The web app for tests: a TestClient that passes the security checks, and a real uvicorn server on a
free port for the tests that need a browser or a live event stream."""

from __future__ import annotations

import http.client
import threading
import time
from pathlib import Path

import uvicorn
from starlette.testclient import TestClient

from clipforge.server.app import bind, create_app
from clipforge.server.security import SESSION_COOKIE


def api_client(
    engine,
    *,
    static_dir: Path | None = None,
    port: int = 8723,
    session: bool = True,
    origin: bool = True,
) -> TestClient:
    """A client for ``engine``'s web app that looks like ClipForge's own window: the right Host, a
    matching Origin and the session cookie, unless ``origin`` or ``session`` say otherwise. The app's
    lifespan (which starts the job runner) runs only inside ``with client:``."""
    app = create_app(engine, port=port, static_dir=static_dir)
    headers = {"Origin": f"http://127.0.0.1:{port}"} if origin else {}
    client = TestClient(app, base_url=f"http://127.0.0.1:{port}", headers=headers)
    if session:
        client.cookies.set(SESSION_COOKIE, app.state.session)
    return client


class LiveServer:
    """``engine``'s web app on a real socket, served from a thread. Leaving the block stops the server
    but not the engine, so a test can start another one on the same port, as a restart would."""

    def __init__(self, engine, port: int = 0, *, static_dir: Path | None = None):
        self.engine = engine
        self.port = port
        self.static_dir = static_dir
        self.app = None
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> LiveServer:
        sock = bind(self.port)
        self.port = sock.getsockname()[1]
        self.app = create_app(self.engine, port=self.port, static_dir=self.static_dir)
        config = uvicorn.Config(
            self.app, log_level="warning", access_log=False, lifespan="on", timeout_graceful_shutdown=1
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._server.run, kwargs={"sockets": [sock]}, name="live-server", daemon=True
        )
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if not self._thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("the live server didn't start")
            time.sleep(0.01)
        return self

    def raw(
        self, method: str, path: str, *, headers: dict[str, str] | None = None, session: bool = True
    ) -> tuple[int, dict[str, str], bytes]:
        """Send ``path`` exactly as written, with no client tidying of ``..`` or percent escapes, and
        return the status, the headers (lower-cased names) and the body."""
        sent = dict(headers or {})
        if session:
            sent.setdefault("Cookie", f"{SESSION_COOKIE}={self.app.state.session}")
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, headers=sent)
            r = conn.getresponse()
            return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
        finally:
            conn.close()

    def __exit__(self, *exc) -> None:
        assert self._server is not None and self._thread is not None
        self._server.should_exit = True
        self._thread.join(10)
