import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from clipforge import repo
from clipforge.errors import WaitUntil
from clipforge.jobs import pipeline
from clipforge.jobs.stages import clips
from clipforge.media.ffmpeg import ffmpeg_bin, run
from clipforge.models import Analysis, ClipRow, FaceBox, ProjectOptions, Sentence, Signals, Transcript, Word
from clipforge.vision.analyze import ANALYSIS_VERSION, analyze_clip
from clipforge.vision.decode import ClipSource
from clipforge.watch import store
from clipforge.watch.models import ClipChannel
from tests.fakes import FakeYouTube, fake_engine
from tests.posting import add_account
from tests.unit.test_pipeline_advance import meta, selected, settle

MARKS = [(0.45, 0.37), (0.52, 0.37), (0.485, 0.42), (0.455, 0.46), (0.515, 0.46)]


class OneFace:
    def detect(self, frame, t):
        return [FaceBox(t=t, x=0.4, y=0.3, w=0.15, h=0.2, score=0.9, marks=MARKS)]


def grey_video(path, seconds=6):
    run(
        [
            ffmpeg_bin(),
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=gray:size=1280x720:rate=30:d={seconds}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=300:duration={seconds}",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ]
    )
    return path


def talk(seconds=6.0, wps=3.0):
    n = int(seconds * wps)
    words = [Word(i=i, text="word", start=i / wps, end=(i + 0.8) / wps) for i in range(n)]
    text = " ".join(w.text for w in words)
    sent = Sentence(id="S1", start=0.0, end=words[-1].end, w0=0, w1=n - 1, text=text)
    tr = Transcript(
        engine="test", engine_version="1", language="en", duration=seconds, words=words, sentences=[sent]
    )
    sig = Signals(
        has_heatmap=False,
        has_comments=False,
        has_chat=False,
        has_events=False,
        rms=[0.1] * int(seconds),
        heat_p80=None,
        max_bucket_weight=0.0,
        sentences={},
    )
    return tr, sig


def analyse(src, a, b, **kw):
    tr, sig = talk()
    opts = {"gameplay_mode": "auto", "has_gameplay": False, **kw}
    return analyze_clip(src, a, b, detector=OneFace(), transcript=tr, signals=sig, **opts)


def test_analyze_clip_on_a_synthetic_source(tmp_path):
    src = ClipSource(grey_video(tmp_path / "video.mp4"))
    got = analyse(src, 2.0, 4.0)
    assert got.version == ANALYSIS_VERSION == 2
    assert (got.start_s, got.end_s) == pytest.approx((1.0, 5.0), abs=0.1)
    assert (got.width, got.height) == (1280, 720)
    assert [s.kind for s in got.shots] == ["single"] and got.cuts == []
    assert len(got.tracks) == 1 and got.turns == []
    assert (got.layout, got.layout_reason) == ("face", "One speaker: the speaker fills the frame")
    assert 0.0 <= got.quality <= 10.0
    assert len(got.motion) == len(got.tracks[0].times)
    edge = analyse(src, 0.4, 5.8)
    assert (edge.start_s, edge.end_s) == pytest.approx((0.0, 6.0), abs=0.1)
    assert edge.key != got.key
    assert analyse(src, 2.0, 4.0, forced="stacked").layout == "stacked"


URL = "https://www.youtube.com/watch?v=arj7oStGLkU"


class SectionTube(FakeYouTube):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.ranges, self.events, self.cancelled = [], [], None

    def download_sections(self, url, dest_dir, ranges, quality, *, cancelled=None):
        self.ranges += list(ranges)
        self.events.append("download")
        self.cancelled = cancelled
        out = []
        for a, b in ranges:
            out.append(Path(dest_dir) / f"video.{a}-{b}.mp4")
            out[-1].write_bytes(b"section")
        return out


class Counting(OneFace):
    def __init__(self):
        self.calls = 0

    def detect(self, frame, t):
        self.calls += 1
        return super().detect(frame, t)


@pytest.fixture
def engine():
    eng = fake_engine(youtube=SectionTube())
    yield eng
    eng.close()


def project(engine, **options):
    (p,), _ = engine.add_sources(URL, ProjectOptions(**options))
    return p.id


def add_clips(engine, pid, n=1, *, start=30.0, end=60.0, failing=()):
    rows = [
        ClipRow(
            id=f"c{i}",
            project_id=pid,
            rank=i,
            start_s=start,
            end_s=end,
            passes_gates=i not in failing,
            status="packaged",
        )
        for i in range(1, n + 1)
    ]
    repo.replace_ideas(engine.db(), pid, rows)
    return [r.id for r in rows]


def ctx(engine, cid, pid, events=None):
    """What a clip job's handler is given; ``events`` hears its heartbeat start and stop."""
    job = SimpleNamespace(ref_id=cid, payload={"project": pid})
    heard = [] if events is None else events

    @contextmanager
    def beating():
        heard.append("beating")
        try:
            yield
        finally:
            heard.append("beaten")

    return SimpleNamespace(
        job=job,
        settings=engine.settings,
        progress=lambda *a, **k: None,
        cancelled=lambda: False,
        beating=beating,
    )


def ready_source(engine, pid, seconds=6):
    pp = engine.paths.project(pid)
    pp.source.mkdir(parents=True, exist_ok=True)
    pp.work.mkdir(parents=True, exist_ok=True)
    grey_video(pp.source / "video.mp4", seconds)
    tr, sig = talk(float(seconds))
    pp.transcript_json.write_text(tr.model_dump_json())
    pp.signals_json.write_text(sig.model_dump_json())
    pp.meta_json.write_text(meta(duration_s=float(seconds)).model_dump_json())
    return pp


def test_clip_source_uses_the_whole_video(engine):
    pid = project(engine)
    (cid,) = add_clips(engine, pid)
    pp = engine.paths.project(pid)
    pp.source.mkdir(parents=True, exist_ok=True)
    (pp.source / "video.mp4").write_bytes(b"video")
    assert clips.clip_source(engine, ctx(engine, cid, pid)) == {
        "path": str(pp.source / "video.mp4"),
        "offset_s": 0.0,
    }
    assert engine.youtube.ranges == []


def test_clip_source_downloads_a_padded_section_without_the_video(engine):
    pid = project(engine)
    (cid,) = add_clips(engine, pid, start=30.0, end=60.0)
    got = clips.clip_source(engine, ctx(engine, cid, pid))
    assert engine.youtube.ranges == [(25.0, 65.0)]
    assert got["offset_s"] == 25.0 and Path(got["path"]).parent == engine.paths.project(pid).sections


def test_clip_source_downloads_inside_a_heartbeat_and_hands_on_the_cancel(engine):
    pid = project(engine)
    (cid,) = add_clips(engine, pid)
    job = ctx(engine, cid, pid, engine.youtube.events)
    clips.clip_source(engine, job)
    assert engine.youtube.events == ["beating", "download", "beaten"]
    assert engine.youtube.cancelled is job.cancelled


def test_clip_source_reuses_a_covering_section(engine):
    pid = project(engine)
    (cid,) = add_clips(engine, pid, start=30.0, end=60.0)
    sections = engine.paths.project(pid).sections
    sections.mkdir(parents=True)
    (sections / "video.28.0-50.0.mp4").write_bytes(b"too short")
    (sections / "video.20.0-70.0.mp4").write_bytes(b"section")
    (sections / "video.10.0-90.0.mp4.part").write_bytes(b"unfinished")
    got = clips.clip_source(engine, ctx(engine, cid, pid))
    assert got == {"path": str(sections / "video.20.0-70.0.mp4"), "offset_s": 20.0}
    assert engine.youtube.ranges == []


def downloading(engine, pid, progress=0.0, spent_s=None):
    """The project's whole video download: queued, or running for ``spent_s`` with ``progress`` done."""
    job = engine.runner.enqueue("ingest.video", pid)
    if spent_s is not None:
        engine.db().execute(
            "UPDATE jobs SET status = 'running', started_at = ?, progress = ? WHERE id = ?",
            (time.time() - spent_s, progress, job),
        )


def test_clip_source_waits_for_the_video_download(engine):
    """A whole video download due within SOON_S at its pace so far is waited for, and once one of the
    project's clips has rendered, so is one further off."""
    pid = project(engine)
    (cid,) = add_clips(engine, pid)
    downloading(engine, pid, 0.9, spent_s=60.0)  # about 7 s to go
    with pytest.raises(WaitUntil) as err:
        clips.clip_source(engine, ctx(engine, cid, pid))
    assert err.value.until > time.time() and err.value.message == clips.WAITING
    repo.update_clip(engine.db(), cid, current_render_id="r1", status="rendered")
    engine.db().execute("UPDATE jobs SET progress = 0.1 WHERE kind = 'ingest.video'")
    with pytest.raises(WaitUntil) as err:
        clips.clip_source(engine, ctx(engine, cid, pid))
    assert err.value.message == clips.WAITING and engine.youtube.ranges == []


def test_the_first_clip_does_not_wait_long_for_the_video(engine):
    """SPEC 19's first clip with the whole video downloading: a 57 minute 1080p60 stream's 2.1 GB took
    410 s against 273 s from its link to its clips, so while the video is over SOON_S away, or not
    started, the best clip downloads its own section. The others wait for it as in section mode, and
    once a clip has rendered, for the video."""
    pid = project(engine)
    add_clips(engine, pid, 3)
    for i, cid in ((1, "c2"), (2, "c3")):
        repo.update_clip(engine.db(), cid, start_s=30.0 + 100 * i, end_s=60.0 + 100 * i)
    pipeline.render_queue(engine, pid)
    downloading(engine, pid)
    clips.clip_source(engine, ctx(engine, "c1", pid))
    engine.db().execute(
        "UPDATE jobs SET status = 'running', started_at = ?, progress = 0.5 WHERE kind = 'ingest.video'",
        (time.time() - 60.0,),
    )  # about 60 s to go
    with pytest.raises(WaitUntil) as err:
        clips.clip_source(engine, ctx(engine, "c2", pid))
    assert err.value.message == clips.BEST_FIRST
    (source,) = engine.runner.jobs_for("c1")
    engine.db().execute(
        "UPDATE jobs SET status = 'failed', finished_at = ? WHERE id = ?", (time.time(), source.id)
    )
    clips.clip_source(engine, ctx(engine, "c2", pid))
    repo.update_clip(engine.db(), "c2", current_render_id="r1", status="rendered")
    with pytest.raises(WaitUntil) as err:
        clips.clip_source(engine, ctx(engine, "c3", pid))
    assert err.value.message == clips.WAITING
    assert engine.youtube.ranges == [(25.0, 65.0), (125.0, 165.0)]


def test_the_best_clip_downloads_its_section_first(engine):
    """SPEC 19's first clip in section mode: sections downloading beside a render made it over twice as
    slow, so until one of the project's clips has rendered, a section waits while a better clip is on its
    way. A better clip that failed holds nothing up."""
    pid = project(engine)
    add_clips(engine, pid, 3)
    for i, cid in ((1, "c2"), (2, "c3")):
        repo.update_clip(engine.db(), cid, start_s=30.0 + 100 * i, end_s=60.0 + 100 * i)
    pipeline.render_queue(engine, pid)
    clips.clip_source(engine, ctx(engine, "c1", pid))
    for cid in ("c2", "c3"):
        with pytest.raises(WaitUntil) as err:
            clips.clip_source(engine, ctx(engine, cid, pid))
        assert err.value.until > time.time() and err.value.message == clips.BEST_FIRST
    assert engine.youtube.ranges == [(25.0, 65.0)]
    (source,) = engine.runner.jobs_for("c1")
    engine.db().execute(
        "UPDATE jobs SET status = 'failed', finished_at = ? WHERE id = ?", (time.time(), source.id)
    )
    clips.clip_source(engine, ctx(engine, "c2", pid))
    with pytest.raises(WaitUntil):
        clips.clip_source(engine, ctx(engine, "c3", pid))
    repo.update_clip(engine.db(), "c2", current_render_id="r1", status="rendered")
    clips.clip_source(engine, ctx(engine, "c3", pid))
    assert engine.youtube.ranges == [(25.0, 65.0), (125.0, 165.0), (225.0, 265.0)]


def test_clip_analyze_job_writes_analysis_and_layout(engine):
    pid = project(engine)
    ready_source(engine, pid)
    (cid,) = add_clips(engine, pid, start=2.0, end=4.0)
    engine.face_detector = OneFace()
    clips.clip_analyze(engine, ctx(engine, cid, pid))
    got = Analysis.model_validate_json(engine.paths.project(pid).analysis_json(cid).read_text())
    assert got.layout == "face" and [s.kind for s in got.shots] == ["single"]
    clip = repo.get_clip(engine.db(), cid)
    assert (clip.layout, clip.layout_reason) == ("face", "One speaker: the speaker fills the frame")


def test_clip_analyze_is_cached_by_key(engine):
    pid = project(engine)
    ready_source(engine, pid)
    (cid,) = add_clips(engine, pid, start=2.0, end=4.0)
    engine.face_detector = det = Counting()
    clips.clip_analyze(engine, ctx(engine, cid, pid))
    first = det.calls
    assert first > 0
    assert clips.clip_analyze(engine, ctx(engine, cid, pid)) == {"skipped": True}
    assert det.calls == first
    path = engine.paths.project(pid).analysis_json(cid)
    path.write_text(
        Analysis.model_validate_json(path.read_text()).model_copy(update={"key": "old"}).model_dump_json()
    )
    clips.clip_analyze(engine, ctx(engine, cid, pid))
    assert det.calls > first


def test_render_queue_follows_auto_render_and_gates(engine):
    pid = project(engine)
    add_clips(engine, pid, 12, failing=(3,))
    engine.settings = engine.settings.model_copy(update={"auto_render": "top5"})
    assert pipeline.render_queue(engine, pid) == ["c1", "c2", "c4", "c5", "c6"]
    (job,) = engine.runner.jobs_for("c4")
    assert (job.kind, job.priority, job.payload) == ("clip.source", 996, {"project": pid})
    assert repo.get_clip(engine.db(), "c4").status == "queued"
    assert repo.get_clip(engine.db(), "c7").status == "packaged"
    engine.settings = engine.settings.model_copy(update={"auto_render": "all"})
    assert len(pipeline.render_queue(engine, pid)) == 11
    engine.settings = engine.settings.model_copy(update={"auto_render": "none"})
    assert pipeline.render_queue(engine, pid) == []


def test_a_watched_projects_renders_stay_behind(engine):
    store.add_channel(engine.db(), ClipChannel(id="cc1", name="Podcasts", created_at=0, updated_at=0))
    pid = project(engine)
    engine.update_project(pid, clip_channel_id="cc1")
    add_clips(engine, pid, 2)
    pipeline.render_queue(engine, pid)
    (job,) = engine.runner.jobs_for("c2")
    assert job.priority == 998 + pipeline.WATCHED_PRIORITY


@pytest.mark.parametrize(
    ("auto_render", "render", "per_day", "expected"),
    [
        ("none", True, None, ["c1", "c2", "c4", "c5", "c6", "c7"]),  # 3 TikTok accounts at 2 a day
        ("top5", True, None, ["c1", "c2", "c4", "c5", "c6", "c7"]),
        ("none", False, None, ["c1", "c2", "c4", "c5", "c6", "c7"]),
        ("none", True, 4, ["c1", "c2", "c4", "c5"]),  # Drafts a day, when lower
        ("top10", True, 4, ["c1", "c2", "c4", "c5", "c6", "c7", "c8", "c9", "c10", "c11"]),
    ],
)
def test_a_watched_project_renders_what_its_accounts_can_take(engine, auto_render, render, per_day, expected):
    accounts = [add_account(engine, "tiktok") for _ in range(3)]
    channel = ClipChannel(
        id="cc1",
        name="Podcasts",
        account_ids=[a.id for a in accounts],
        clips_per_day=per_day,
        created_at=0,
        updated_at=0,
    )
    store.add_channel(engine.db(), channel)
    pid = project(engine, render=render)
    engine.update_project(pid, clip_channel_id="cc1")
    add_clips(engine, pid, 12, failing=(3,))
    engine.settings = engine.settings.model_copy(update={"auto_render": auto_render})
    assert pipeline.render_queue(engine, pid) == expected


def test_no_render_project_queues_no_clip_jobs(engine):
    pid = project(engine, render=False)
    add_clips(engine, pid, 3)
    assert pipeline.render_queue(engine, pid) == []
    assert all(engine.runner.jobs_for(c) == [] for c in ("c1", "c2", "c3"))


def test_failed_clip_job_marks_only_its_clip(engine):
    pid = project(engine)
    add_clips(engine, pid, 2)
    pipeline.render_queue(engine, pid)
    before = engine.project(pid).status
    (job,) = engine.runner.jobs_for("c1")
    engine.db().execute(
        "UPDATE jobs SET status = 'failed', error = 'boom', finished_at = ? WHERE id = ?",
        (time.time(), job.id),
    )
    pipeline.on_finished(engine, engine.runner.get(job.id))
    c1 = repo.get_clip(engine.db(), "c1")
    assert (c1.status, c1.error) == ("failed", "boom")
    assert repo.get_clip(engine.db(), "c2").status == "queued"
    assert engine.project(pid).status == before


def test_engine_wait_follows_clip_jobs(engine):
    pid = project(engine)
    ready_source(engine, pid)
    add_clips(engine, pid, 1, start=2.0, end=4.0)
    engine.face_detector = OneFace()
    pipeline.render_queue(engine, pid)
    assert engine.wait(60, projects=[pid])
    assert [(j.kind, j.status) for j in engine.runner.jobs_for("c1")] == [
        ("clip.source", "done"),
        ("clip.analyze", "done"),
        ("clip.render", "done"),
    ]
    assert engine.paths.project(pid).analysis_json("c1").exists()
    assert (engine.paths.project(pid).render_dir("c1", 1) / "clip.mp4").exists()


def test_ready_project_queues_its_top_clips():
    eng = fake_engine()
    try:
        pid = selected(eng)
        add_clips(eng, pid, 2)
        settle(eng, pid, "brain.package")
        assert eng.project(pid).status == "ready"
        assert [(j.kind, j.status) for j in eng.runner.jobs_for("c1")] == [("clip.source", "queued")]
    finally:
        eng.close()
