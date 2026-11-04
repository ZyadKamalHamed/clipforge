"""A campaign list built by its page's scripts, read headless (SPEC 10.11): a new, empty profile that
Playwright deletes, other sites' requests and pictures, fonts and stylesheets aborted, GET only, and the
read counted as one brief page."""

import logging
import shutil
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from clipforge.briefs import fetch, store
from clipforge.today.day import today_of
from tests.fakes import fake_engine

LISTING = Path(__file__).parents[1] / "mock_sites" / "briefs"


class Listing:
    """The look-alike list on 127.0.0.1, served from a copy in ``folder``, with the requests it was sent as
    (method, path). ``moves`` are paths that redirect, each to its address."""

    def __init__(self, folder: Path, moves: dict[str, str] | None = None) -> None:
        shutil.copy(LISTING / "listing.html", folder / "listing.html")
        self.folder = folder
        self.moves = dict(moves or {})
        self.served: list[tuple[str, str]] = []

    def __enter__(self) -> "Listing":
        served, moves = self.served, self.moves

        class Handler(SimpleHTTPRequestHandler):
            def log_message(self, format, *args):  # quiet
                pass

            def do_GET(self):
                served.append(("GET", self.path))
                if self.path in moves:
                    self.send_response(302)
                    self.send_header("Location", moves[self.path])
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                super().do_GET()

            def do_POST(self):
                served.append(("POST", self.path))
                self.send_error(405)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(self.folder)))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}{path}"


def folders(home: Path) -> set[Path]:
    return {p for p in home.rglob("*") if p.is_dir()}


def test_scripted_listing_reads_in_a_fresh_profile(caplog, tmp_path):
    caplog.set_level(logging.INFO, logger="clipforge.briefs.fetch")
    eng = fake_engine()
    eng.brief_transport = None  # robots.txt comes from the look-alike itself, on this Mac
    try:
        before = folders(eng.paths.home)
        with Listing(tmp_path) as site:
            assert len(fetch.Polite(eng, site.url("/")).get(site.url("/listing.html"), page=False).links) == 0
            got = fetch.scripted(eng, site.url("/listing.html"))
        assert got.url == site.url("/listing.html")
        assert got.links == [site.url(f"/campaign/{n}") for n in (1, 2, 3)]
        assert "Campaign 2" in got.text and "<ul" in got.html
        assert "other.example" in caplog.text  # its script was asked for and stopped
        paths = [path for _, path in site.served]
        assert "/logo.png" not in paths and "/style.css" not in paths
        assert ("POST", "/api/more") not in site.served
        assert paths.count("/listing.html") == 2 and paths.count("/robots.txt") == 2
        assert store.site_read(eng.db(), "127.0.0.1", today_of(eng).isoformat())[0] == 1
        assert folders(eng.paths.home) == before
    finally:
        eng.close()


def test_scripted_keeps_to_the_rules(tmp_path):
    eng = fake_engine()
    eng.brief_transport = None
    try:
        with Listing(tmp_path) as site:
            with pytest.raises(fetch.Stop) as stop:
                fetch.scripted(eng, "https://other.example/list", polite=fetch.Polite(eng, site.url("/")))
            assert stop.value.result == "blocked" and site.served == []
            (tmp_path / "robots.txt").write_text("User-agent: *\nDisallow: /listing\n")
            with pytest.raises(fetch.Stop) as stop:
                fetch.scripted(eng, site.url("/listing.html"))
            assert stop.value.result == "blocked" and [p for _, p in site.served] == ["/robots.txt"]
    finally:
        eng.close()


class Elsewhere:
    """Another site on this Mac, addressed as localhost so the reader counts it as off the site, with every
    path it was asked for (a WebSocket's opening request too)."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def __enter__(self) -> "Elsewhere":
        asked = self.asked

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):  # quiet
                pass

            def do_GET(self):
                asked.append(self.path)
                body = b"<!doctype html><title>Elsewhere</title><p>Somewhere else.</p>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()

    def url(self, path: str) -> str:
        return f"http://localhost:{self.server.server_address[1]}{path}"


HOPS = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Open campaigns</title></head>
<body>
<ul id="list"></ul>
<script>
  const list = document.getElementById("list");
  for (const n of [1, 2, 3]) {
    const link = document.createElement("a");
    link.href = "/campaign/" + n;
    link.textContent = "Campaign " + n;
    list.append(link);
  }
  fetch("/hop").catch(() => {});
  try { new WebSocket("WS"); } catch (err) {}
</script>
</body>
</html>
"""
BIG = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Open campaigns</title></head>
<body><script>document.body.textContent = "x".repeat(6 * 1024 * 1024);</script></body>
</html>
"""


def test_scripted_never_reaches_another_site(tmp_path):
    eng = fake_engine()
    eng.brief_transport = None
    try:
        with Elsewhere() as other:
            moves = {"/hop": other.url("/hopped"), "/away": other.url("/landing")}
            (tmp_path / "hops.html").write_text(HOPS.replace("WS", other.url("/ws").replace("http", "ws", 1)))
            with Listing(tmp_path, moves) as site:
                got = fetch.scripted(eng, site.url("/hops.html"))
                assert got.links == [site.url(f"/campaign/{n}") for n in (1, 2, 3)]
                assert ("GET", "/hop") in site.served  # its own request went, its hop to elsewhere didn't
                with pytest.raises(fetch.Stop) as stop:
                    fetch.scripted(eng, site.url("/away"))
                assert (stop.value.result, stop.value.message) == ("blocked", fetch.ELSEWHERE)
            assert other.asked == []
    finally:
        eng.close()


def test_scripted_stops_at_5_mb(tmp_path):
    eng = fake_engine()
    eng.brief_transport = None
    try:
        (tmp_path / "big.html").write_text(BIG)
        with Listing(tmp_path) as site, pytest.raises(fetch.Stop) as stop:
            fetch.scripted(eng, site.url("/big.html"))
        assert (stop.value.result, stop.value.message) == ("changed", fetch.TOO_BIG)
    finally:
        eng.close()
