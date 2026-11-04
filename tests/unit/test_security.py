"""SPEC 17: the local server answers only ClipForge's own window."""

import re
import socket

import httpx
import pytest
from starlette.websockets import WebSocketDisconnect

from clipforge.app import BUSY
from clipforge.server.app import create_app, serve
from clipforge.server.security import SESSION_COOKIE, allowed_host, csp, same_origin
from tests.fakes import fake_engine
from tests.server import LiveServer, api_client

COMMON = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
    "cross-origin-opener-policy": "same-origin",
    "cross-origin-resource-policy": "same-origin",
}
BARE_CSP = "default-src 'none'; frame-ancestors 'none'"


@pytest.fixture
def engine():
    e = fake_engine()
    yield e
    e.close()


@pytest.fixture
def static(tmp_path):
    """A built UI in miniature: the placeholders Vite leaves in index.html, one script, one icon."""
    root = tmp_path / "static"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(
        '<!doctype html><html lang="en" data-theme="__THEME__"><head>'
        '<meta property="csp-nonce" nonce="__CSP_NONCE__">'
        '<script type="module" crossorigin nonce="__CSP_NONCE__" src="/assets/app.js"></script>'
        '</head><body><div id="root"></div></body></html>'
    )
    (root / "assets" / "app.js").write_text("console.log('ClipForge')")
    (root / "favicon.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    return root


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def nonce_of(policy: str) -> str:
    match = re.search(r"'nonce-([^']+)'", policy)
    assert match, policy
    return match.group(1)


def test_bad_host_gets_404(engine):
    client = api_client(engine)
    for host in ("evil.com", "127.0.0.1:9999", "[::1]:8723", "localhost", "127.0.0.1", "evil.com:8723"):
        r = client.get("/api/health", headers={"host": host})
        assert r.status_code == 404, host
        assert r.text == "Not found"
    assert not allowed_host(None, 8723)
    assert not allowed_host("", 8723)


def test_localhost_and_ip_hosts_pass(engine):
    client = api_client(engine)
    for host in ("127.0.0.1:8723", "localhost:8723"):
        r = client.get("/api/health", headers={"host": host})
        assert r.status_code == 200, host
        assert r.json()["app"] == "clipforge"


def test_state_change_needs_matching_origin(engine):
    client = api_client(engine, origin=False)
    for origin in (None, "http://evil.com", "http://localhost:8723", "https://127.0.0.1:8723", "null"):
        headers = {"origin": origin} if origin else {}
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            r = client.request(method, "/api/health", headers=headers)
            assert r.status_code == 403, (method, origin)
            assert r.json() == {
                "error": "origin",
                "message": "This request came from outside ClipForge, so it was refused.",
                "field": None,
            }
    # A matching Origin gets past the guard to the router, which has no POST here.
    assert client.post("/api/health", headers={"origin": "http://127.0.0.1:8723"}).status_code == 405
    assert same_origin("http://127.0.0.1:8723", "127.0.0.1:8723")
    assert same_origin("http://localhost:8723", "localhost:8723")
    assert not same_origin("http://localhost:8723", "127.0.0.1:8723")
    assert not same_origin(None, "127.0.0.1:8723")


def test_api_needs_the_session_cookie(engine):
    bare = api_client(engine, session=False)
    r = bare.get("/api/projects")
    assert r.status_code == 401
    assert r.json() == {
        "error": "session",
        "message": "ClipForge restarted. Reload the window.",
        "field": None,
    }
    bare.cookies.set(SESSION_COOKIE, "not-the-one")
    assert bare.get("/api/projects").status_code == 401
    assert bare.get("/media/projects/p1/source/thumb.jpg").status_code == 401
    odd = (SESSION_COOKIE + "=caf" + chr(0xE9)).encode("latin-1")  # not ASCII, still just wrong
    assert bare.get("/api/projects", headers={"cookie": odd}).status_code == 401
    r = api_client(engine).get("/api/nothing-here")
    assert r.status_code == 404
    assert r.json()["error"] == "not_found"


def test_health_needs_no_cookie(engine):
    r = api_client(engine, session=False).get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"app": "clipforge", "version": "0.1.0"}


def test_index_sets_the_session_cookie(engine, static):
    client = api_client(engine, static_dir=static, session=False)
    r = client.get("/")
    assert r.status_code == 200
    session = client.app.state.session
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}={session};")
    parts = {p.strip().lower() for p in cookie.split(";")}
    assert {"httponly", "path=/", "samesite=strict"} <= parts
    assert len(session) >= 43
    assert create_app(engine, port=8723, static_dir=static).state.session != session
    # The cookie the page set opens the API from then on.
    assert client.get("/api/nothing-here").status_code == 404


# What a tunnel or proxy adds on its way in; ClipForge's own Chrome window never sends any of them.
FORWARDED = (
    ("x-forwarded-for", "203.0.113.9"),
    ("x-forwarded-host", "clips.example"),
    ("forwarded", "for=203.0.113.9"),
    ("x-real-ip", "203.0.113.9"),
    ("via", "1.1 tunnel"),
    ("cf-connecting-ip", "203.0.113.9"),
    ("tailscale-funnel-request", "?1"),
)


def test_a_proxied_request_never_reaches_the_window(engine, static):
    """A tunnel that rewrites the Host to ClipForge's own (ngrok's host header rewrite, nginx's default
    proxy_pass) still leaves its forwarding headers: such a request gets a bare 404 and no session cookie,
    so the window and the API stay this Mac's only."""
    page = api_client(engine, static_dir=static, session=False)
    api = api_client(engine)
    for name, value in FORWARDED:
        r = page.get("/", headers={name: value})
        assert (r.status_code, r.text) == (404, "Not found"), name
        assert "set-cookie" not in r.headers, name
        assert api.get("/api/projects", headers={name: value}).status_code == 404, name
        assert api.get("/api/health", headers={name: value}).status_code == 404, name
    assert page.get("/").status_code == 200
    assert api.get("/api/projects").status_code == 200


def test_index_csp_nonce_matches_the_tags(engine, static):
    client = api_client(engine, static_dir=static)
    first = client.get("/")
    policy = first.headers["content-security-policy"]
    nonce = nonce_of(policy)
    assert policy == csp(nonce)
    assert first.text.count(f'nonce="{nonce}"') == 2
    assert "__CSP_NONCE__" not in first.text
    assert 'data-theme="dark"' in first.text
    assert first.headers["cache-control"] == "no-store"
    assert nonce_of(client.get("/").headers["content-security-policy"]) != nonce


def test_csp_is_strict():
    assert csp("abc") == (
        "default-src 'self'; script-src 'self' 'nonce-abc'; style-src 'self' 'nonce-abc'; "
        "img-src 'self' data: blob:; media-src 'self' blob:; font-src 'self'; connect-src 'self'; "
        "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    )


def test_client_routes_get_the_index_and_files_are_served(engine, static):
    client = api_client(engine, static_dir=static)
    for path in ("/projects", "/projects/abc", "/clips/c1", "/no/such/page"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert '<div id="root">' in r.text
    js = client.get("/assets/app.js")
    assert js.status_code == 200
    assert js.headers["content-type"].startswith("text/javascript")
    assert js.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert client.get("/favicon.svg").headers["content-type"].startswith("image/svg+xml")
    for path in ("/assets/missing.js", "/assets/"):
        assert client.get(path).status_code == 404, path


def test_traversal_never_reaches_files(engine, static):
    (static.parent / "secret.txt").write_text("private")
    (static / "assets" / "link.js").symlink_to(static.parent / "secret.txt")
    with LiveServer(engine, static_dir=static) as live:
        for path in (
            "/assets/../index.html",
            "/assets/%2e%2e/index.html",
            "/assets/..%2f..%2fsecret.txt",
            "/assets/%2E%2E/%2E%2E/secret.txt",
            "/assets//app.js",
            "/assets/./app.js",
            "/assets/link.js",
            "/assets/app.js%00",
        ):
            status, _, body = live.raw("GET", path)
            assert status == 404, path
            assert b"private" not in body
        assert live.raw("GET", "/assets/app.js")[0] == 200


def test_security_headers_on_every_response(engine, static):
    client = api_client(engine, static_dir=static)
    bare = api_client(engine, static_dir=static, session=False, origin=False)
    responses = {
        "index": client.get("/"),
        "health": client.get("/api/health"),
        "api 404": client.get("/api/nothing-here"),
        "asset": client.get("/assets/app.js"),
        "401": bare.get("/api/projects"),
        "403": bare.post("/api/health"),
        "bad host": client.get("/api/health", headers={"host": "evil.com"}),
    }
    for name, r in responses.items():
        for header, value in COMMON.items():
            assert r.headers.get(header) == value, (name, header)
        if name != "index":
            assert r.headers["content-security-policy"] == BARE_CSP, name
    for name in ("health", "api 404", "401", "403"):
        assert responses[name].headers["cache-control"] == "no-store", name


def test_no_cors_headers(engine):
    client = api_client(engine, origin=False)
    for r in (
        client.get("/api/health", headers={"origin": "http://evil.com"}),
        client.options(
            "/api/health", headers={"origin": "http://evil.com", "access-control-request-method": "GET"}
        ),
    ):
        assert not [h for h in r.headers if h.lower().startswith("access-control-")]


def test_websockets_are_refused(engine):
    client = api_client(engine)
    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/api/events"):
        pass


def test_missing_bundle_page_explains_the_build(engine, tmp_path):
    r = api_client(engine, static_dir=tmp_path / "not-built").get("/")
    assert r.status_code == 503
    assert "ClipForge's window isn't built yet." in r.text
    assert "npm --prefix web ci &amp;&amp; npm --prefix web run build" in r.text
    assert r.headers["content-security-policy"] == BARE_CSP


def test_serve_reports_a_busy_engine_plainly(engine, capsys):
    assert serve(port=free_port()) == 1
    assert BUSY in capsys.readouterr().err


def test_serve_reports_a_taken_port(capsys):
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        port = taken.getsockname()[1]
        assert serve(port=port) == 1
    err = capsys.readouterr().err
    assert f"Port {port} is in use by another app. Close it, or start ClipForge with --port." in err
    # The engine it opened let go of the home again.
    fake_engine().close()


def test_live_server_round_trip(engine):
    with LiveServer(engine) as live:
        r = httpx.get(f"{live.url}/api/health", timeout=5)
        assert r.status_code == 200
        assert r.json()["app"] == "clipforge"
        assert httpx.get(f"{live.url}/api/projects", timeout=5).status_code == 401
