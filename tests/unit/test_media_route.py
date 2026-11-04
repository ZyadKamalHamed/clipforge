"""SPEC 17: /media serves the clips' versions, the sources' thumbnails, the gameplay library, the posts'
screenshots and the accounts' pictures from the home, with ranges for the players, and nothing else."""

import pytest

from tests.fakes import fake_engine
from tests.server import LiveServer, api_client

VIDEO = bytes(range(256)) * 40  # 10240 bytes
CLIP = "projects/p1/clips/c1/v1/clip.mp4"
IMMUTABLE = "private, max-age=31536000, immutable"
NO_CACHE = "private, no-cache"


@pytest.fixture
def engine():
    e = fake_engine()
    yield e
    e.close()


def put(engine, rel, data=VIDEO):
    path = engine.paths.home / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_media_serves_allowlisted_files(engine):
    files = {
        "projects/p1/clips/c1/v1/clip.mp4": ("video/mp4", IMMUTABLE),
        "projects/p1/clips/c1/v1/cover.jpg": ("image/jpeg", IMMUTABLE),
        "projects/pa1b2c3d4e5/clips/c0f9e8d7c6b/v12/thumb.jpg": ("image/jpeg", IMMUTABLE),
        "projects/p1/source/thumb.jpg": ("image/jpeg", NO_CACHE),
        "library/gameplay/l0a1b2c3d4e.mp4": ("video/mp4", NO_CACHE),
        "library/gameplay/l0a1b2c3d4e.mov": ("video/quicktime", NO_CACHE),
        "library/gameplay/l0a1b2c3d4e.mkv": ("video/x-matroska", NO_CACHE),
        "library/gameplay/l0a1b2c3d4e.webm": ("video/webm", NO_CACHE),
        "library/gameplay/l0a1b2c3d4e.m4v": ("video/x-m4v", NO_CACHE),
        "library/music/l0a1b2c3d4e.mp3": ("audio/mpeg", NO_CACHE),
        "library/music/l0a1b2c3d4e.m4a": ("audio/mp4", NO_CACHE),
        "library/music/l0a1b2c3d4e.aac": ("audio/aac", NO_CACHE),
        "library/music/l0a1b2c3d4e.wav": ("audio/wav", NO_CACHE),
        "library/music/l0a1b2c3d4e.aif": ("audio/aiff", NO_CACHE),
        "library/music/l0a1b2c3d4e.aiff": ("audio/aiff", NO_CACHE),
        "library/music/l0a1b2c3d4e.flac": ("audio/flac", NO_CACHE),
        "library/music/l0a1b2c3d4e.mp4": ("video/mp4", NO_CACHE),
        "library/music/l0a1b2c3d4e.mov": ("video/quicktime", NO_CACHE),
    }
    client = api_client(engine)
    for n, (rel, (kind, cache)) in enumerate(files.items()):
        data = VIDEO + bytes([n])
        put(engine, rel, data)
        r = client.get("/media/" + rel)
        assert r.status_code == 200, rel
        assert r.content == data, rel
        assert (r.headers["content-type"], r.headers["cache-control"]) == (kind, cache), rel
        assert r.headers["accept-ranges"] == "bytes"
        assert "content-disposition" not in r.headers


def test_media_serves_post_screenshots_and_avatars(engine):
    client = api_client(engine)
    for rel in (
        "logs/posts/p1a2b3c4d5e/01-upload.png",
        "logs/posts/p1/12-needs_you.png",
        "accounts/a1/avatar.png",
    ):
        put(engine, rel, b"png")
        r = client.get("/media/" + rel)
        assert r.status_code == 200, rel
        assert (r.headers["content-type"], r.headers["cache-control"]) == ("image/png", NO_CACHE), rel
    for rel in (
        "logs/posts/p1/01-upload.html",  # the page as it was, which may hold the account's own details
        "logs/posts/p1/steps.jsonl",
        "logs/posts/p1/upload.png",
        "logs/posts/p1/sub/01-upload.png",
        "logs/posts/01-upload.png",
        "accounts/a1/other.png",
        "accounts/a1/avatar.jpg",
        "browser/a1/Cookies",
    ):
        put(engine, rel, b"x")
        r = client.get("/media/" + rel)
        assert (r.status_code, r.text) == (404, "Not found"), rel


def test_media_range_206(engine):
    put(engine, CLIP)
    r = api_client(engine).get("/media/" + CLIP, headers={"range": "bytes=100-199"})
    assert r.status_code == 206
    assert r.content == VIDEO[100:200]
    assert r.headers["content-range"] == f"bytes 100-199/{len(VIDEO)}"
    assert r.headers["content-length"] == "100"


def test_media_suffix_range(engine):
    put(engine, CLIP)
    r = api_client(engine).get("/media/" + CLIP, headers={"range": "bytes=-50"})
    assert r.status_code == 206
    assert r.content == VIDEO[-50:]
    assert r.headers["content-range"] == f"bytes {len(VIDEO) - 50}-{len(VIDEO) - 1}/{len(VIDEO)}"


def test_media_unsatisfiable_range_416(engine):
    put(engine, CLIP)
    r = api_client(engine).get("/media/" + CLIP, headers={"range": f"bytes={len(VIDEO)}-"})
    assert r.status_code == 416
    assert r.headers["content-range"] == f"bytes */{len(VIDEO)}"


def test_media_head(engine):
    put(engine, CLIP)
    r = api_client(engine).head("/media/" + CLIP)
    assert r.status_code == 200
    assert r.content == b""
    assert (r.headers["content-length"], r.headers["content-type"]) == (str(len(VIDEO)), "video/mp4")


def test_media_rejects_traversal(engine, tmp_path):
    put(engine, CLIP)
    (tmp_path / "secret.mp4").write_bytes(b"private")
    up = "../" * 6
    with LiveServer(engine) as live:
        for path in (
            f"/media/projects/p1/clips/c1/v1/{up}secret.mp4",
            "/media/projects/p1/clips/c1/v1/" + "%2e%2e/" * 6 + "secret.mp4",
            "/media/projects/p1/clips/c1/v1/" + "..%2f" * 6 + "secret.mp4",
            "/media/projects/p1/clips/c1/v1/%2E%2E/v1/clip.mp4",
            "/media//projects/p1/clips/c1/v1/clip.mp4",
            "/media/projects//p1/clips/c1/v1/clip.mp4",
            "/media/projects/p1/clips/c1/./v1/clip.mp4",
            "/media/" + str(engine.paths.home / CLIP),
            "/media/%2F" + str(tmp_path / "secret.mp4").lstrip("/"),
            "/media/projects/p1/clips/c1/v1/clip.mp4%00",
            "/media/projects/p1/clips/c1/v1/clip.mp4%00.jpg",
        ):
            status, _, body = live.raw("GET", path)
            assert status == 404, path
            assert body == b"Not found", path
        status, _, body = live.raw("GET", "/media/projects%5Cp1%5Cclips%5Cc1%5Cv1%5Cclip.mp4")
        assert (status, body) == (404, b"Not found")  # a backslash is never a separator
        status, _, body = live.raw("GET", "/media/projects/p1/clips/c1/v1\\clip.mp4")
        assert status in (400, 404) and VIDEO[:50] not in body  # the server may refuse it first
        assert live.raw("GET", "/media/" + CLIP)[0] == 200


def test_media_refuses_symlinks(engine, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "clip.mp4").write_bytes(b"private")
    linked = engine.paths.home / CLIP
    linked.parent.mkdir(parents=True)
    linked.symlink_to(outside / "clip.mp4")
    (linked.parent / "cover.jpg").symlink_to(engine.paths.db)  # inside the home is no better
    (linked.parent.parent / "v2").symlink_to(outside, target_is_directory=True)
    put(engine, "projects/p2/clips/c1/v1/clip.mp4", b"real")
    (engine.paths.projects / "p3").symlink_to(engine.paths.projects / "p2", target_is_directory=True)
    client = api_client(engine)
    for rel in (
        CLIP,
        "projects/p1/clips/c1/v1/cover.jpg",
        "projects/p1/clips/c1/v2/clip.mp4",
        "projects/p3/clips/c1/v1/clip.mp4",
    ):
        r = client.get("/media/" + rel)
        assert (r.status_code, r.text) == (404, "Not found"), rel
    assert client.get("/media/projects/p2/clips/c1/v1/clip.mp4").content == b"real"


def test_media_refuses_files_off_the_list(engine):
    client = api_client(engine)
    for rel in (
        "clipforge.db",
        "projects/p1/clips/c1/v1/edl.json",
        "projects/p1/clips/c1/v1/qa.json",
        "projects/p1/clips/c1/v1/render.log",
        "projects/p1/clips/c1/analysis.json",
        "projects/p1/clips/c1/.v2.tmp/clip.mp4",
        "projects/p1/clips/c1/v0/clip.mp4",
        "projects/p1/source/info.json",
        "projects/p1/source/video.mp4",
        "projects/p1/work/audio16k.wav",
        "library/gameplay/l1.mp4.tmp",
        "library/gameplay/l1.MP4",
        "library/gameplay/l1.avi",
        "library/gameplay/sub/l1.mp4",
        "library/gameplay/l1.mp3",
        "library/music/l1.mkv",
        "library/music/l1.mp3.tmp",
        "logs/clipforge.log",
    ):
        if not (engine.paths.home / rel).exists():
            put(engine, rel)
        r = client.get("/media/" + rel)
        assert (r.status_code, r.text) == (404, "Not found"), rel
    for rel in ("projects/p1/clips/c1/v1", "projects/p1/clips/c1/v1/clip.mp4"):  # a folder; a missing file
        assert client.get("/media/" + rel).status_code == 404, rel


def test_media_download_names_are_clean(engine):
    put(engine, CLIP)
    client = api_client(engine)

    def disposition(name):
        return client.get("/media/" + CLIP, params={"download": name}).headers["content-disposition"]

    assert disposition("The best bit") == 'attachment; filename="The best bit.mp4"'
    assert disposition('Wait: "what?" / no*') == 'attachment; filename="Wait what no.mp4"'
    assert disposition("../../etc/passwd") == 'attachment; filename="etcpasswd.mp4"'
    assert disposition("clip.MP4") == 'attachment; filename="clip.mp4"'
    assert disposition("A_b-c.d") == 'attachment; filename="A_b-c.d.mp4"'
    assert disposition("x" * 200) == f'attachment; filename="{"x" * 76}.mp4"'
    assert disposition(chr(0x202E) + "gpj.exe") == 'attachment; filename="gpj.exe.mp4"'
    assert disposition("???") == 'attachment; filename="clip.mp4"'
    assert disposition("") == 'attachment; filename="clip.mp4"'
    assert disposition("caf" + chr(0xE9) + " clip") == (
        "attachment; filename=\"caf_ clip.mp4\"; filename*=UTF-8''caf%C3%A9%20clip.mp4"
    )
    thumb = put(engine, "projects/p1/source/thumb.jpg")
    r = client.get("/media/projects/p1/source/thumb.jpg", params={"download": "Cover"})
    assert r.headers["content-disposition"] == 'attachment; filename="Cover.jpg"'
    assert r.content == thumb.read_bytes()
    assert "content-disposition" not in client.get("/media/" + CLIP).headers


def test_media_old_version_still_plays_after_a_rerender(engine):
    put(engine, CLIP, b"first")
    client = api_client(engine)
    assert client.get("/media/" + CLIP).content == b"first"
    put(engine, "projects/p1/clips/c1/v2/clip.mp4", b"second")  # the new version is a folder of its own
    for rel, data in ((CLIP, b"first"), ("projects/p1/clips/c1/v2/clip.mp4", b"second")):
        r = client.get("/media/" + rel)
        assert (r.status_code, r.content, r.headers["cache-control"]) == (200, data, IMMUTABLE)


def test_media_needs_the_session_cookie(engine):
    put(engine, CLIP)
    r = api_client(engine, session=False).get("/media/" + CLIP)
    assert r.status_code == 401
    assert r.json()["error"] == "session"
    assert VIDEO[:50] not in r.content
    assert api_client(engine).get("/media/" + CLIP).status_code == 200
