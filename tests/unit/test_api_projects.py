"""SPEC 14.1 to 14.3 and 18: the projects API. Links and dropped files become projects at once, a project
reads as the window shows it, with its steps, and retry, rerun and delete do what their buttons say."""

import json
import socket
import threading
import time
from urllib.parse import quote

import pytest

from clipforge import repo
from clipforge.ingest.urls import OTHER_SITE, PLAYLIST, file_from_url
from clipforge.jobs.stages import complete_file
from clipforge.models import RenderRow
from clipforge.server.security import SESSION_COOKIE
from clipforge.server.views import clean_file_name, clean_text
from tests.fakes import FakeProvider, FakeSpeech, FakeYouTube, brain_handlers, fake_engine, lines
from tests.helpers import make_test_video
from tests.server import LiveServer, api_client

URL = "https://www.youtube.com/watch?v=arj7oStGLkU"
VIMEO = "https://vimeo.com/76979871"
LIST = "https://www.youtube.com/playlist?list=PLx0sYbCqOb8TBPRdmBHs5Iftvv9TPboYG"
NOTHING = "Nothing to clip. Give a YouTube or Google Drive link, or the path of a video file."
INTERNAL = "Something went wrong. The log has the details."
NOT_FOUND = {"error": "not_found", "message": "That isn't here.", "field": None}
FIELDS = {
    "id", "title", "channel", "channel_url", "source_url", "source_kind", "duration_s", "language",
    "rights_basis", "campaign_id", "status", "stage", "progress", "message", "error", "error_code",
    "error_details", "options", "created_at", "updated_at", "thumb", "clip_count", "rendered_count",
    "rerun_drops", "source_on_disk", "steps",
}  # fmt: skip
STEPS = [
    ("metadata", "Metadata"),
    ("download", "Download"),
    ("transcribe", "Transcribe"),
    ("signals", "Signals"),
    ("select", "Find moments"),
    ("package", "Package"),
    ("render", "Render"),
]


@pytest.fixture
def engine():
    e = fake_engine()
    yield e
    e.close()


def full_engine(**kw):
    """An engine whose projects run all the way to ready, in Full mode, without rendering on their own."""
    return fake_engine(
        speech=FakeSpeech(lines(36)), provider=FakeProvider(brain_handlers()), auto_render="none", **kw
    )


class FlakyYouTube(FakeYouTube):
    """A fake YouTube that fails with a plain exception until ``broken`` is turned off."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.broken = True

    def fetch_info(self, url: str) -> dict:
        if self.broken:
            raise RuntimeError("the fake YouTube is down")
        return super().fetch_info(url)


class SlowYouTube(FakeYouTube):
    """A fake YouTube whose audio download, once ``slow`` is on, waits for ``gate`` (at most 10 s)."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.slow = False
        self.inside = threading.Event()
        self.gate = threading.Event()

    def download_audio(self, url, dest_dir, *, progress=None):
        if self.slow:
            self.inside.set()
            self.gate.wait(10)
        return super().download_audio(url, dest_dir, progress=progress)


def link(n: int) -> str:
    return f"https://www.youtube.com/watch?v=vid{n:08d}"


def post(client, text: str, **body) -> str:
    r = client.post("/api/projects", json={"text": text, **body})
    assert r.status_code == 200, r.text
    return r.json()["projects"][0]["id"]


def project_of(client, pid: str) -> dict:
    r = client.get(f"/api/projects/{pid}")
    assert r.status_code == 200, r.text
    return r.json()


def job_of(engine, ref: str, kind: str):
    return next(j for j in reversed(engine.runner.jobs_for(ref)) if j.kind == kind)


def set_job(engine, job_id: int, **columns) -> None:
    sets = ", ".join(f"{name} = ?" for name in columns)
    engine.db().execute(f"UPDATE jobs SET {sets} WHERE id = ?", (*columns.values(), job_id))


def wait_for(check, timeout_s: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while not check():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.02)
    return True


def uploads_left(engine) -> list:
    folder = engine.paths.uploads
    return sorted(folder.iterdir()) if folder.is_dir() else []


def upload(client, data: bytes, name: str, options: dict | None = None, **headers):
    sent = {"X-File-Name": quote(name), "Content-Type": "application/octet-stream", **headers}
    if options is not None:
        sent["X-Options"] = json.dumps(options)
    return client.post("/api/projects/upload", content=data, headers=sent)


# Adding


def test_post_projects_returns_projects_fast(engine):
    client = api_client(engine)
    t0 = time.monotonic()
    r = client.post("/api/projects", json={"text": URL, "options": {"length": "short"}})
    took = time.monotonic() - t0
    assert r.status_code == 200, r.text
    assert took < 1.0
    body = r.json()
    assert body["rejected"] == []
    (p,) = body["projects"]
    assert set(p) == FIELDS
    assert (p["source_kind"], p["status"], p["options"]["length"]) == ("youtube", "queued", "short")
    assert "arj7oStGLkU" in p["source_url"]
    assert engine.youtube.called("fetch_info") == []  # nothing was fetched while the window waited
    assert [j.kind for j in engine.runner.jobs_for(p["id"])] == ["ingest.metadata"]  # and it is on its way


def test_post_projects_reports_each_rejected_input(engine):
    client = api_client(engine)
    cases = {"just some words about a video": NOTHING, VIMEO: OTHER_SITE, LIST: PLAYLIST}
    for text, message in cases.items():
        r = client.post("/api/projects", json={"text": text})
        assert r.status_code == 200, r.text
        assert r.json() == {"projects": [], "rejected": [{"input": text, "message": message}]}
    assert engine.projects() == []

    body = client.post("/api/projects", json={"text": f"{URL}\n{VIMEO}"}).json()
    assert len(body["projects"]) == 1
    assert body["rejected"] == [{"input": VIMEO, "message": OTHER_SITE}]
    assert client.post("/api/projects", json={"text": URL, "options": {"length": "huge"}}).status_code == 422
    assert client.post("/api/projects", json={}).status_code == 422


def test_ten_links_make_ten_projects(engine):
    client = api_client(engine)
    r = client.post("/api/projects", json={"text": "\n".join(link(n) for n in range(10))})
    made = r.json()["projects"]
    assert len({p["id"] for p in made}) == 10
    assert [p["source_url"].endswith(f"vid{n:08d}") for n, p in enumerate(made)] == [True] * 10
    assert len(client.get("/api/projects").json()["projects"]) == 10

    again = client.post("/api/projects", json={"text": link(0)}).json()["projects"]
    assert [p["id"] for p in again] == [made[0]["id"]]  # the same video gives its project back
    fresh = client.post("/api/projects", json={"text": link(0), "new": True}).json()["projects"]
    assert fresh[0]["id"] not in {p["id"] for p in made}


def test_get_projects_newest_first_and_by_status(engine):
    client = api_client(engine)
    ids = [post(client, link(n)) for n in range(3)]
    engine.update_project(ids[1], status="failed", error="It broke.")
    assert [p["id"] for p in client.get("/api/projects").json()["projects"]] == ids[::-1]
    assert [p["id"] for p in client.get("/api/projects?status=failed").json()["projects"]] == [ids[1]]
    assert [p["id"] for p in client.get("/api/projects?status=failed,queued").json()["projects"]] == ids[::-1]
    assert [p["id"] for p in client.get("/api/projects?limit=2").json()["projects"]] == [ids[2], ids[1]]
    assert client.get("/api/projects?limit=0").status_code == 422


# Reading


def test_project_json_shape_and_steps():
    engine = full_engine()
    try:
        client = api_client(engine)
        pid = post(client, URL)
        before = project_of(client, pid)
        assert set(before) == FIELDS
        assert [(s["key"], s["label"], s["status"], s["progress"]) for s in before["steps"]] == [
            (key, label, "todo", 0) for key, label in STEPS
        ]
        assert (before["thumb"], before["source_on_disk"], before["clip_count"]) == (None, False, 0)

        assert engine.wait(60, projects=[pid])
        p = project_of(client, pid)
        assert set(p) == FIELDS
        assert (p["status"], p["channel"], p["rights_basis"], p["error"]) == (
            "ready",
            "TED",
            "not_cleared",
            None,
        )
        assert p["title"].startswith("Inside the Mind of a Master Procrastinator")
        assert [(s["status"], s["progress"]) for s in p["steps"]] == [("done", 1)] * 6 + [("todo", 0)]
        clips = engine.clips(pid)
        assert p["clip_count"] == len(clips) >= 2 and p["rendered_count"] == p["rerun_drops"] == 0
        assert p["thumb"] is None  # tests never fetch from the web, so the miss is remembered

        a, b = clips[:2]
        engine.update_clip(a.id, status="rendered")
        engine.update_clip(b.id, status="rendering")
        render = project_of(client, pid)["steps"][-1]
        assert (render["status"], render["progress"]) == ("running", 0.5)
        engine.update_clip(b.id, status="failed")
        render = project_of(client, pid)["steps"][-1]
        assert (render["status"], render["progress"]) == ("done", 0.5)
        engine.update_clip(b.id, discarded_at=time.time())  # a discarded clip no longer counts
        p = project_of(client, pid)
        assert (p["steps"][-1]["status"], p["steps"][-1]["progress"]) == ("done", 1)
        assert (p["clip_count"], p["rendered_count"], p["rerun_drops"]) == (len(clips) - 1, 1, 1)
        engine.update_clip(a.id, status="failed")
        assert project_of(client, pid)["steps"][-1]["status"] == "failed"
    finally:
        engine.close()


def test_steps_follow_the_jobs(engine):
    client = api_client(engine)  # no workers run here: the jobs only move as the test moves them
    pid = post(client, URL)

    def step(key):
        return next(s for s in project_of(client, pid)["steps"] if s["key"] == key)

    meta = job_of(engine, pid, "ingest.metadata")
    set_job(engine, meta.id, status="running", progress=0.5)
    assert (step("metadata")["status"], step("metadata")["progress"]) == ("running", 0.5)
    set_job(engine, meta.id, status="queued", progress=0, not_before=time.time() + 600)
    assert step("metadata")["status"] == "waiting"
    set_job(engine, meta.id, status="done", progress=1, not_before=0)
    engine.runner.enqueue("ingest.audio", pid)
    engine.runner.enqueue("ingest.captions", pid)
    engine.runner.enqueue("ingest.extras", pid)
    set_job(engine, job_of(engine, pid, "ingest.audio").id, status="running", progress=0.5)
    set_job(engine, job_of(engine, pid, "ingest.captions").id, status="failed")  # optional: its turn is over
    download = step("download")
    assert download["status"] == "running"
    assert download["progress"] == pytest.approx((0.10 * 0.5 + 0.01) / 0.13, abs=1e-3)
    set_job(engine, job_of(engine, pid, "ingest.audio").id, status="failed", error="It broke.")
    assert step("download")["status"] == "failed"
    assert step("transcribe")["status"] == "todo"


def test_project_json_cleans_untrusted_text(engine):
    client = api_client(engine)
    pid = post(client, URL)
    nasty = (
        "Big"
        + chr(27)
        + "[31m news"
        + chr(0x202E)
        + "gnp.exe"
        + chr(7)
        + chr(0x85)
        + "end"
        + chr(0x2066)
        + "x"
    )
    clean = "Big [31m newsgnp.exe  endx"  # control characters became spaces, the direction marks are gone
    engine.update_project(
        pid, title=nasty, channel=nasty, message=nasty, error=nasty, channel_url="javascript:alert(1)"
    )
    p = project_of(client, pid)
    assert [p["title"], p["channel"], p["message"], p["error"]] == [clean] * 4
    assert p["channel_url"] is None
    engine.update_project(pid, channel_url="https://www.youtube.com/@TED")
    assert project_of(client, pid)["channel_url"] == "https://www.youtube.com/@TED"
    assert clean_text(None) is None
    assert clean_text("tab" + chr(9) + "new" + chr(10) + "line" + chr(0x9F)) == "tab new line "


# Changing


def test_patch_rights_basis_and_bad_value(engine):
    client = api_client(engine)
    pid = post(client, URL)
    for basis in ("own", "cc", "permission", "clipping_programme", "not_cleared", "permission"):
        r = client.patch(f"/api/projects/{pid}", json={"rights_basis": basis})
        assert r.status_code == 200, r.text
        assert r.json()["rights_basis"] == basis
    assert engine.project(pid).rights_basis == "permission"
    r = client.patch(f"/api/projects/{pid}", json={"rights_basis": "stolen"})
    assert r.status_code == 422
    assert (r.json()["error"], r.json()["field"]) == ("invalid", "rights_basis")
    assert engine.project(pid).rights_basis == "permission"


def test_patch_options_validates(engine):
    client = api_client(engine)
    pid = post(client, URL, options={"length": "short", "clip_count": 4})
    r = client.patch(
        f"/api/projects/{pid}", json={"options": {"length": "long", "prompt": "  the funny bits  "}}
    )
    assert r.status_code == 200, r.text
    options = r.json()["options"]
    assert (options["length"], options["clip_count"], options["prompt"]) == ("long", 4, "the funny bits")
    assert engine.project(pid).prompt == "the funny bits"  # the project's own prompt follows
    for bad, field in (({"length": "huge"}, "options.length"), ({"colour": "red"}, "options.colour")):
        r = client.patch(f"/api/projects/{pid}", json={"options": bad})
        assert r.status_code == 422, bad
        assert r.json()["field"] == field
    assert engine.project(pid).options.length == "long"


def test_retry_requeues_a_failed_project():
    youtube = FlakyYouTube()
    engine = full_engine(youtube=youtube)
    try:
        client = api_client(engine)
        pid = post(client, URL)
        assert engine.wait(30, projects=[pid])
        p = project_of(client, pid)
        assert (p["status"], p["error_code"], p["error"]) == ("failed", "internal", INTERNAL)
        assert p["error_details"] == "RuntimeError: the fake YouTube is down"
        assert [s["status"] for s in p["steps"][:2]] == ["failed", "todo"]

        youtube.broken = False
        engine.runner.stop()  # so the answer shows the project as queued, not already picked up
        r = client.post(f"/api/projects/{pid}/retry")
        assert r.status_code == 200, r.text
        p = r.json()
        assert (p["status"], p["error"], p["error_code"], p["error_details"]) == ("queued", None, None, None)
        assert engine.wait(60, projects=[pid])
        assert engine.project(pid).status == "ready"
    finally:
        engine.close()


def test_retry_wakes_a_waiting_project(engine):
    client = api_client(engine)
    pid = post(client, URL)
    meta = job_of(engine, pid, "ingest.metadata")
    set_job(engine, meta.id, not_before=time.time() + 3600)
    engine.update_project(pid, status="waiting", message="Waiting for the usage limit to lift.")
    r = client.post(f"/api/projects/{pid}/retry")
    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["message"]) == ("queued", None)
    assert engine.runner.get(meta.id).not_before == 0


def test_retry_refuses_a_ready_project(engine):
    client = api_client(engine)
    pid = post(client, URL)
    stuck = {"error": "not_stuck", "message": "This project isn't stuck.", "field": None}
    r = client.post(f"/api/projects/{pid}/retry")  # queued: on its way, not stuck
    assert (r.status_code, r.json()) == (409, stuck)
    engine.update_project(pid, status="ready")
    r = client.post(f"/api/projects/{pid}/retry")
    assert (r.status_code, r.json()) == (409, stuck)
    assert engine.project(pid).status == "ready"


def test_rerun_with_a_prompt():
    engine = full_engine()
    try:
        client = api_client(engine)
        pid = post(client, URL)
        r = client.post(f"/api/projects/{pid}/rerun", json={})
        assert r.status_code == 409
        assert r.json()["error"] == "nothing_to_rerun"  # nothing is selected yet

        assert engine.wait(60, projects=[pid])
        engine.runner.stop()
        r = client.post(f"/api/projects/{pid}/rerun", json={"prompt": "  the deadline  "})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "queued"
        project = engine.project(pid)
        assert project.prompt == project.options.prompt == "the deadline"
        assert engine.wait(60, projects=[pid])
        selects = [j for j in engine.runner.jobs_for(pid) if j.kind == "brain.select"]
        assert len(selects) == 2 and selects[-1].payload == {"rerun": True}
        assert engine.project(pid).status == "ready"

        assert client.post(f"/api/projects/{pid}/rerun", json={"prompt": None}).status_code == 200
        assert engine.project(pid).options.prompt is None  # a rerun can clear the prompt too
        assert engine.wait(60, projects=[pid])
    finally:
        engine.close()


def test_delete_project_cancels_jobs_and_removes_files():
    youtube = SlowYouTube()
    engine = full_engine(youtube=youtube)
    try:
        client = api_client(engine)
        done = post(client, URL)
        assert engine.wait(60, projects=[done])
        clips = engine.clips(done)
        rid = repo.new_id("r")
        repo.add_render(
            engine.db(), RenderRow(id=rid, clip_id=clips[0].id, version=1, style_key="k", path="x")
        )
        sub = engine.events.subscribe()

        r = client.delete(f"/api/projects/{done}")
        assert r.status_code == 204 and r.content == b""
        assert engine.project(done) is None and engine.clips(done) == []
        assert repo.get_clip(engine.db(), clips[0].id) is None and repo.get_render(engine.db(), rid) is None
        assert not engine.paths.project(done).root.exists()
        assert engine.runner.jobs_for(done) == [] and engine.runner.jobs_for(clips[0].id) == []
        events = [e.data for e in iter(lambda: sub.get(0), None) if e.topic == "project"]
        assert {"id": done, "status": "deleted"} in events

        youtube.slow = True
        busy = post(client, URL, new=True)
        engine.runner.start()
        assert youtube.inside.wait(10)  # its audio is downloading
        threading.Timer(0.3, youtube.gate.set).start()
        t0 = time.monotonic()
        r = client.delete(f"/api/projects/{busy}")
        assert r.status_code == 204
        assert time.monotonic() - t0 < 9  # it waited for the download to let go, not the whole 10 s
        assert engine.project(busy) is None and engine.runner.jobs_for(busy) == []
        assert not engine.paths.project(busy).root.exists()
        assert client.get(f"/api/projects/{busy}").status_code == 404
    finally:
        engine.close()


# Uploads


def test_upload_makes_a_file_project(engine, tmp_path):
    data = make_test_video(tmp_path / "talk.mp4").read_bytes()
    client = api_client(engine)
    r = upload(client, data, "My talk (final).mp4", {"length": "short"})
    assert r.status_code == 201, r.text
    p = r.json()
    assert set(p) == FIELDS
    assert (p["source_kind"], p["title"], p["options"]["length"]) == ("file", "My talk (final)", "short")
    src = file_from_url(engine.project(p["id"]).source_url)
    assert src.name == "My talk (final).mp4" and src.read_bytes() == data
    assert src.parent.parent == engine.paths.uploads.resolve()
    assert [j.kind for j in engine.runner.jobs_for(p["id"])] == ["ingest.metadata"]


def test_upload_needs_a_length(engine):
    def chunks():
        yield b"x" * 1000

    r = api_client(engine).post("/api/projects/upload", content=chunks(), headers={"X-File-Name": "a.mp4"})
    assert r.status_code == 411
    assert r.json()["message"] == "ClipForge needs the file's size before it starts. Try dropping it again."
    assert (uploads_left(engine), engine.projects()) == ([], [])


def test_upload_refuses_other_file_types(engine):
    client = api_client(engine)
    message = "That file type isn't a video ClipForge can use. Use MP4, MOV, MKV, WEBM or M4V."
    for name in ("notes.txt", "video", "clip.mp4.exe", ""):
        r = upload(client, b"x" * 100, name)
        assert (r.status_code, r.json()["message"]) == (415, message), name
    assert upload(client, b"x" * 100, "clip.MP4").status_code == 201  # any case will do
    assert len(engine.projects()) == 1


def test_upload_refuses_when_space_is_short():
    engine = fake_engine(min_free_gb=500.0)
    try:
        r = upload(api_client(engine), b"x" * 10_240, "talk.mp4")
        assert r.status_code == 507
        assert r.json()["message"] == (
            "That file needs 10 KB, which would leave less than 500 GB free. Free up some space first."
        )
        assert (uploads_left(engine), engine.projects()) == ([], [])
    finally:
        engine.close()


def test_cut_off_upload_leaves_nothing(engine):
    r = upload(api_client(engine), b"x" * 10, "short.mp4", **{"Content-Length": "100000"})
    assert r.status_code == 400
    assert (uploads_left(engine), engine.projects()) == ([], [])

    with LiveServer(engine) as server:
        sock = socket.create_connection(("127.0.0.1", server.port), timeout=10)
        head = (
            f"POST /api/projects/upload HTTP/1.1\r\nHost: 127.0.0.1:{server.port}\r\n"
            f"Origin: {server.url}\r\nCookie: {SESSION_COOKIE}={server.app.state.session}\r\n"
            "X-File-Name: cut.mp4\r\nContent-Length: 50000000\r\n\r\n"
        )
        try:
            sock.sendall(head.encode() + b"x" * 65_536)
            assert wait_for(lambda: uploads_left(engine) != [])  # the upload has begun
        finally:
            sock.close()  # and the window goes away halfway through
        assert wait_for(lambda: uploads_left(engine) == [])
    assert engine.projects() == []


def test_upload_names_are_cleaned(engine, tmp_path):
    assert clean_file_name("../../etc/passwd.mp4") == "passwd.mp4"
    assert clean_file_name("C:" + chr(92) + "Users" + chr(92) + "me" + chr(92) + "clip.mov") == "clip.mov"
    assert clean_file_name("a" + chr(27) + "b:c" + chr(0x202E) + ".mp4") == "abc.mp4"
    assert clean_file_name("x" * 300 + ".mp4") == "x" * 116 + ".mp4"
    assert clean_file_name(".mp4") == "video.mp4"
    assert clean_file_name(" . .webm") == "video.webm"
    assert clean_file_name("") == "video"
    assert clean_file_name("my   clip.mkv") == "my   clip.mkv"

    data = make_test_video(tmp_path / "talk.mp4").read_bytes()
    r = upload(api_client(engine), data, "../../evil:name.mp4")
    assert r.status_code == 201, r.text
    src = file_from_url(engine.project(r.json()["id"]).source_url)
    assert src.name == "evilname.mp4" and src.parent.parent == engine.paths.uploads.resolve()
    assert r.json()["title"] == "evilname"


def test_metadata_removes_the_upload_after_cloning(engine, tmp_path):
    data = make_test_video(tmp_path / "talk.mp4").read_bytes()
    client = api_client(engine)
    pid = upload(client, data, "talk.mp4").json()["id"]
    src = file_from_url(engine.project(pid).source_url)
    assert engine.wait(60, projects=[pid])  # with no speech in it, it stops after the download
    pp = engine.paths.project(pid)
    assert not src.exists() and not src.parent.exists()
    assert complete_file(pp.source, "video").read_bytes() == data
    assert pp.thumb.is_file()  # a frame of the file became its thumbnail
    p = project_of(client, pid)
    assert (p["thumb"], p["source_on_disk"]) == (f"/media/projects/{pid}/source/thumb.jpg", True)

    engine.runner.enqueue("ingest.metadata", pid, rerun=True)  # run again, it uses its own copy
    assert engine.wait(60, projects=[pid])
    assert job_of(engine, pid, "ingest.metadata").status == "done"


# Failures


def test_failure_sentence_has_no_job_kind():
    engine = full_engine(youtube=FlakyYouTube())
    try:
        client = api_client(engine)
        pid = post(client, URL)
        assert engine.wait(30, projects=[pid])
        job = job_of(engine, pid, "ingest.metadata")
        assert (job.status, job.error_code, job.error) == ("failed", "internal", INTERNAL)
        assert job.error_details == "RuntimeError: the fake YouTube is down"
        p = project_of(client, pid)
        assert (p["error"], p["error_details"]) == (INTERNAL, "RuntimeError: the fake YouTube is down")
        assert "ingest" not in p["error"] and "metadata" not in p["error"] and p["message"] is None
    finally:
        engine.close()


def test_unknown_project_is_a_json_404(engine):
    client = api_client(engine)
    calls = [
        ("GET", "/api/projects/nope", None),
        ("PATCH", "/api/projects/nope", {"rights_basis": "own"}),
        ("POST", "/api/projects/nope/retry", None),
        ("POST", "/api/projects/nope/rerun", {}),
        ("DELETE", "/api/projects/nope", None),
    ]
    for method, path, body in calls:
        r = client.request(method, path, json=body)
        assert (r.status_code, r.json()) == (404, NOT_FOUND), (method, path)
