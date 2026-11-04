import random
import threading
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from clipforge import repo
from clipforge.app import checked_edits
from clipforge.campaigns import store as campaigns
from clipforge.campaigns.models import Campaign
from clipforge.db import connect, migrate
from clipforge.edits import EditError, render_key
from clipforge.errors import ClipForgeError, ToolError
from clipforge.jobs import pipeline
from clipforge.jobs.stages import clips
from clipforge.library import gameplay, music
from clipforge.media.ffmpeg import ffmpeg_bin, probe, run
from clipforge.models import Analysis, ClipRow, EmojiCue, LibraryItem, Packaging, ProjectOptions
from clipforge.render.edl import EDL, EMPTY, GAMEPLAY_GONE, GAMEPLAY_OFF, MUSIC_GONE, NO_GAMEPLAY_ITEM
from clipforge.render.qa import Check, QAResult, run_qa
from clipforge.render.styles import style_for, style_key
from tests.fakes import FakeProvider, FakeSpeech, brain_handlers, fake_engine, lines
from tests.helpers import make_test_audio, make_test_video
from tests.unit.test_clip_jobs import URL, OneFace, SectionTube, add_clips, project, ready_source
from tests.unit.test_overlays import png

FILES = ["clip.mp4", "cover.jpg", "edl.json", "qa.json", "render.log", "thumb.jpg"]
FIRE = chr(0x1F525)
PACKAGING = Packaging(hook_title="A grey test", emoji_cues=[EmojiCue(sid="S1", word="word", emoji=FIRE)])
UNREADABLE = "ClipForge couldn't read this clip's video. Try again."
BLACK = "The first frame is black, so the clip would open on nothing."
TOO_BIG = "The file is 301 MB, over Instagram's 300 MB limit."


@pytest.fixture
def engine():
    eng = fake_engine(youtube=SectionTube())
    eng.face_detector = OneFace()
    yield eng
    eng.close()


def job(engine, cid, pid):
    """What a clip job's handler is given, for calling one directly."""
    return SimpleNamespace(
        job=SimpleNamespace(ref_id=cid, payload={"project": pid}),
        settings=engine.settings,
        progress=lambda *a, **k: None,
        cancelled=lambda: False,
        beating=nullcontext,
    )


def ready_clip(engine, *, edits=None, **options):
    """A project with a 6 s grey source and one packaged clip from 1.0 s to 4.5 s, analysed."""
    pid = project(engine, **options)
    ready_source(engine, pid)
    (cid,) = add_clips(engine, pid, start=1.0, end=4.5)
    repo.update_clip(engine.db(), cid, packaging=PACKAGING.model_dump(mode="json"), edits=edits)
    clips.clip_analyze(engine, job(engine, cid, pid))
    return pid, cid


def names(folder):
    return sorted(p.name for p in Path(folder).iterdir())


def edl_of(video):
    return EDL.model_validate_json((Path(video).parent / "edl.json").read_text())


def qa_with(check):
    """run_qa as it is, plus ``check``."""

    def patched(path, edl, audio, **kw):
        return QAResult(checks=[*run_qa(path, edl, audio, **kw).checks, check])

    return patched


# Rendering a version


def test_render_job_writes_a_version_and_a_row(engine):
    pid, cid = ready_clip(engine)
    got = clips.clip_render(engine, job(engine, cid, pid))
    folder = engine.paths.project(pid).clip_dir(cid)
    v1 = folder / "v1"
    assert names(folder) == ["analysis.json", "v1"] and names(v1) == FILES
    (row,) = repo.list_renders(engine.db(), cid)
    assert (row.version, row.status, row.path) == (1, "ready", str(v1 / "clip.mp4"))
    assert (row.cover_path, row.thumb_path, row.edl_path) == tuple(
        str(v1 / name) for name in ("cover.jpg", "thumb.jpg", "edl.json")
    )
    qa = QAResult.model_validate_json((v1 / "qa.json").read_text())
    assert qa.passed and QAResult.model_validate(row.qa) == qa
    assert got == {
        "render_id": row.id,
        "version": 1,
        "path": row.path,
        "status": "ready",
        "problems": qa.problems(),
    }
    info = probe(v1 / "clip.mp4")
    assert (info.width, info.height, info.vcodec, info.acodec) == (1080, 1920, "h264", "aac")
    assert row.size_bytes == (v1 / "clip.mp4").stat().st_size
    assert row.duration_s == pytest.approx(info.duration_s, abs=0.01)
    edl = edl_of(row.path)
    assert row.style_key == style_key(edl.style) == style_key(style_for(engine.settings, {}))
    assert 0.0 <= row.cover_s <= edl.duration
    assert (edl.credit or {}).get("text") == "Source: TED"
    assert edl.emoji == [] and "pop" not in [c["kind"] for c in edl.sfx]  # the tests never fetch emoji
    assert "1 emoji was left out, since ClipForge couldn't get its picture" in edl.notes
    assert "VideoToolbox" in (v1 / "render.log").read_text()
    clip = repo.get_clip(engine.db(), cid)
    assert (clip.status, clip.current_render_id, clip.error) == ("rendered", row.id, None)


def test_rerender_keeps_the_previous_version_until_done(engine, monkeypatch):
    pid, cid = ready_clip(engine)
    first = clips.clip_render(engine, job(engine, cid, pid))
    folder = engine.paths.project(pid).clip_dir(cid)
    seen = []
    real = clips.make_cover

    def spy(video, edl, cover, thumb):  # the new video is made, the old one still current
        current = repo.get_clip(engine.db(), cid).current_render_id
        seen.append((current, (folder / "v1" / "clip.mp4").exists(), (folder / "v2").exists()))
        seen.append(Path(video).parent.name)
        return real(video, edl, cover, thumb)

    monkeypatch.setattr(clips, "make_cover", spy)
    engine.emoji_fetch = lambda url, max_bytes: png()
    second = clips.clip_render(engine, job(engine, cid, pid))
    assert seen == [(first["render_id"], True, False), ".v2.tmp"]
    assert [r.version for r in repo.list_renders(engine.db(), cid)] == [1, 2]
    assert repo.get_clip(engine.db(), cid).current_render_id == second["render_id"] != first["render_id"]
    assert names(folder / "v1") == FILES  # the previous version stays available
    edl = edl_of(second["path"])
    assert [e["emoji"] for e in edl.emoji] == [FIRE] and Path(edl.emoji[0]["png"]).is_file()
    assert "pop" in [c["kind"] for c in edl.sfx]


def test_killed_render_leaves_no_partial_version(engine, monkeypatch):
    pid, cid = ready_clip(engine)
    folder = engine.paths.project(pid).clip_dir(cid)
    left = folder / ".v1.tmp"  # what a render killed halfway leaves behind
    left.mkdir()
    (left / "clip.mp4").write_bytes(b"half a video")
    (left / "sound.m4a").write_bytes(b"half a sound")
    first = clips.clip_render(engine, job(engine, cid, pid))
    assert names(folder) == ["analysis.json", "v1"] and names(folder / "v1") == FILES

    def dies(edl, src, out, **kw):
        Path(out).write_bytes(b"half a frame")
        raise ToolError("decode_failed", UNREADABLE)

    monkeypatch.setattr(clips, "render_video", dies)
    with pytest.raises(ToolError):
        clips.clip_render(engine, job(engine, cid, pid))
    assert names(folder) == ["analysis.json", "v1"]
    assert [r.version for r in repo.list_renders(engine.db(), cid)] == [1]
    assert repo.get_clip(engine.db(), cid).current_render_id == first["render_id"]


def test_a_version_folder_without_a_row_is_left_alone(engine):
    pid, cid = ready_clip(engine)
    orphan = engine.paths.project(pid).render_dir(cid, 1)  # what a crash right after its rename leaves
    orphan.mkdir(parents=True)
    (orphan / "clip.mp4").write_bytes(b"an orphan")
    got = clips.clip_render(engine, job(engine, cid, pid))
    assert got["version"] == 2 and (orphan / "clip.mp4").read_bytes() == b"an orphan"


def test_the_emoji_pictures_are_fetched_inside_a_heartbeat(engine):
    pid, cid = ready_clip(engine)
    events, inside = [], []

    @contextmanager
    def beating():
        events.append("beating")
        try:
            yield
        finally:
            events.append("beaten")

    def fetch(url, max_bytes):
        inside.append(events.count("beating") - events.count("beaten"))
        return png()

    engine.emoji_fetch = fetch
    handed = job(engine, cid, pid)
    handed.beating = beating
    clips.clip_render(engine, handed)
    assert inside == [1]  # a slow emoji server can't get the render taken for stuck


def test_the_sound_is_made_beside_the_picture(engine, monkeypatch):
    """The sound needs little of the CPU and took a third of a render's time, so it is made while the
    picture renders (SPEC 19), and the log still lists each step in order."""
    pid, cid = ready_clip(engine)
    started = threading.Event()
    picture, sound = clips.render_video, clips._sound

    def renders(*args, **kw):
        started.set()
        return picture(*args, **kw)

    def levels(*args):
        assert started.wait(10), "the sound was made before the picture started"
        return sound(*args)

    monkeypatch.setattr(clips, "render_video", renders)
    monkeypatch.setattr(clips, "_sound", levels)
    got = clips.clip_render(engine, job(engine, cid, pid))
    log = (Path(got["path"]).parent / "render.log").read_text().splitlines()
    steps = next(line for line in log if line.startswith("Steps: "))
    assert [step.split()[0] for step in steps.removeprefix("Steps: ").split(", ")] == [
        "sound",
        "picture",
        "file",
        "cover",
        "QA",
    ]
    assert got["status"] == "ready"


def test_a_cancelled_render_stops_at_the_next_frames(engine, monkeypatch):
    pid, cid = ready_clip(engine)
    stopped = []

    def cancels(edl, src, out, *, gameplay=None, progress=None):
        (render,) = [j for j in engine.runner.jobs_for(cid) if j.kind == "clip.render"]
        engine.runner.cancel(render.id)
        try:
            progress(0.5)
        except ClipForgeError as err:
            stopped.append(err.code)
            raise
        raise AssertionError("the render went on")

    monkeypatch.setattr(clips, "render_video", cancels)
    assert pipeline.queue_render(engine.runner, engine.db(), cid)
    assert engine.wait(60, clips=[cid])
    assert stopped == ["cancelled"]
    clip = repo.get_clip(engine.db(), cid)
    assert (clip.status, clip.current_render_id) == ("failed", None) and clip.error
    assert names(engine.paths.project(pid).clip_dir(cid)) == ["analysis.json"]


def test_render_failure_marks_the_clip_with_a_sentence(engine, monkeypatch):
    pid, cid = ready_clip(engine)
    real = clips.render_video

    def broken(edl, src, out, **kw):
        raise ToolError("decode_failed", UNREADABLE)

    monkeypatch.setattr(clips, "render_video", broken)
    assert pipeline.queue_render(engine.runner, engine.db(), cid)
    assert engine.wait(60, clips=[cid])
    clip = repo.get_clip(engine.db(), cid)
    assert (clip.status, clip.error, clip.current_render_id) == ("failed", UNREADABLE, None)
    assert repo.list_renders(engine.db(), cid) == []

    monkeypatch.setattr(clips, "render_video", real)
    monkeypatch.setattr(
        clips, "run_qa", qa_with(Check(name="first_frame", ok=False, hard=True, message=BLACK))
    )
    assert pipeline.queue_render(engine.runner, engine.db(), cid)
    assert engine.wait(60, clips=[cid])
    (row,) = repo.list_renders(engine.db(), cid)
    clip = repo.get_clip(engine.db(), cid)
    assert (row.status, clip.status, clip.error, clip.current_render_id) == (
        "failed",
        "failed",
        BLACK,
        row.id,
    )

    big = Check(name="file_size", ok=False, hard=True, message=TOO_BIG, platform="instagram")
    monkeypatch.setattr(clips, "run_qa", qa_with(big))  # one app's limit blocks only that app
    assert pipeline.queue_render(engine.runner, engine.db(), cid)
    assert engine.wait(60, clips=[cid])
    newest = repo.list_renders(engine.db(), cid)[-1]
    clip = repo.get_clip(engine.db(), cid)
    assert (newest.version, newest.status, clip.status, clip.error) == (2, "ready", "rendered", None)
    assert clip.current_render_id == newest.id and TOO_BIG in QAResult.model_validate(newest.qa).problems()


def test_a_render_asked_for_goes_ahead_of_those_made_on_their_own(engine, monkeypatch):
    pid, cid = ready_clip(engine)
    repo.update_clip(engine.db(), cid, status="failed", error=UNREADABLE)
    seen = []
    real = clips.make_cover

    def spy(*args):
        seen.append(repo.get_clip(engine.db(), cid).status)
        return real(*args)

    monkeypatch.setattr(clips, "make_cover", spy)
    assert pipeline.queue_render(engine.runner, engine.db(), cid, {"preset": "clean"})
    clip = repo.get_clip(engine.db(), cid)
    assert (clip.status, clip.edits, clip.error) == ("queued", {"preset": "clean"}, None)
    assert engine.wait(60, clips=[cid])
    jobs = engine.runner.jobs_for(cid)
    assert [j.kind for j in jobs] == ["clip.source", "clip.analyze", "clip.render"]
    assert {j.priority for j in jobs} == {pipeline.USER_PRIORITY}  # each step keeps its place in the queue
    assert pipeline.USER_PRIORITY > 1000  # those made on their own run at 1000 less their rank
    assert repo.get_clip(engine.db(), cid).status == "rendered" and seen == ["rendering"]
    assert not pipeline.queue_render(engine.runner, engine.db(), "c0000000000")


def test_a_failed_fast_encode_is_tried_again_with_libx264(engine, monkeypatch):
    pid, cid = ready_clip(engine)
    real = clips.render_video
    tried = []

    def flaky(edl, src, out, **kw):
        tried.append(edl.style.encoder)
        if edl.style.encoder == "fast":
            raise ToolError("encode_failed", "The video encoder stopped with an error. Try again.")
        return real(edl, src, out, **kw)

    monkeypatch.setattr(clips, "render_video", flaky)
    got = clips.clip_render(engine, job(engine, cid, pid))
    assert tried == ["fast", "best"] and got["status"] == "ready"
    assert edl_of(got["path"]).style.encoder == "best"
    assert "libx264" in (Path(got["path"]).parent / "render.log").read_text()
    (row,) = repo.list_renders(engine.db(), cid)
    assert row.style_key == style_key(style_for(engine.settings, {}))  # the style asked for


# Gameplay


def a_track(engine, tmp_path, **fields):
    """A 2 s 1 kHz track in the library, calm, as the test clip's packaging is."""
    made = make_test_audio(tmp_path / "bed.m4a", seconds=2.0, tone_hz=1000)
    notes = {
        "title": "Night Drive",
        "artist": "Me",
        "source_url": None,
        "license": "Mine",
        "license_url": None,
    }
    notes |= {"credit_required": True, "moods": ["calm"], "energy": 3}
    return music.add_music(engine, made, **(notes | fields))


def band(path, hz):
    """How strong ``hz`` is in the file's sound: the peak of its spectrum within 20 Hz of it."""
    pcm = np.frombuffer(
        run(
            [
                ffmpeg_bin(),
                "-v",
                "error",
                "-i",
                str(path),
                "-f",
                "f32le",
                "-ac",
                "1",
                "-ar",
                "48000",
                "pipe:1",
            ]
        ).stdout,
        "<f4",
    )
    spec = np.abs(np.fft.rfft(pcm * np.hanning(len(pcm))))
    freqs = np.fft.rfftfreq(len(pcm), 1 / 48000)
    return float(spec[np.abs(freqs - hz) < 20].max())


def test_render_with_music(engine, tmp_path):
    item = a_track(engine, tmp_path)
    pid, cid = ready_clip(engine)
    plain = clips.clip_render(engine, job(engine, cid, pid))
    assert edl_of(plain["path"]).music is None  # off by default
    assert band(plain["path"], 1000) < 0.01 * band(plain["path"], 300)
    clip = repo.get_clip(engine.db(), cid)
    fields = checked_edits(engine.db(), engine.paths, clip, {"music": item.id, "music_db": -12})
    repo.update_clip(engine.db(), cid, edits=fields["edits"])
    got = clips.clip_render(engine, job(engine, cid, pid))
    edl = edl_of(got["path"])
    assert edl.music == {
        "item_id": item.id,
        "title": "Night Drive",
        "artist": "Me",
        "db": -12.0,
        "credit": "Music: Night Drive by Me",
    }
    # the 300 Hz speech plays all through, so the track sits about 18 dB under it
    ratio = 20 * np.log10(band(got["path"], 1000) / band(got["path"], 300))
    assert -26.0 < ratio < -10.0, ratio
    assert (
        "Music: Night Drive by Me (" + item.id + ") at 12 dB under the speech"
        in (Path(got["path"]).parent / "render.log").read_text()
    )
    # "auto" takes the track that suits the clip's calm mood
    repo.update_clip(engine.db(), cid, edits={"music": "auto"})
    assert edl_of(clips.clip_render(engine, job(engine, cid, pid))["path"]).music["item_id"] == item.id


def test_deleted_track_renders_without_music(engine, tmp_path):
    item = a_track(engine, tmp_path, credit_required=False)
    pid, cid = ready_clip(engine, edits={"music": item.id})
    assert edl_of(clips.clip_render(engine, job(engine, cid, pid))["path"]).music["credit"] is None
    assert repo.delete_library_item(engine.db(), item.id)
    Path(item.path).unlink()
    got = clips.clip_render(engine, job(engine, cid, pid))
    edl = edl_of(got["path"])
    assert edl.music is None and MUSIC_GONE in edl.notes
    assert f"Note: {MUSIC_GONE}" in (Path(got["path"]).parent / "render.log").read_text()
    assert repo.get_clip(engine.db(), cid).status == "rendered"
    assert band(got["path"], 1000) < 0.01 * band(got["path"], 300)


def test_gameplay_layout_uses_a_library_item_and_records_the_use(engine, tmp_path):
    original = make_test_video(tmp_path / "parkour.mp4", seconds=20.0)
    item = gameplay.add_gameplay(engine.db(), engine.paths, original, credit="Me", license="Mine")
    assert Path(item.path).parent == engine.paths.gameplay and original.exists()
    assert item.duration_s == pytest.approx(20.0, abs=0.1)
    pid, cid = ready_clip(engine, edits={"layout": "gameplay"})
    got = clips.clip_render(engine, job(engine, cid, pid))
    edl = edl_of(got["path"])
    assert {s.layout for s in edl.spans} == {"gameplay"} and edl.gameplay["item_id"] == item.id
    start = edl.gameplay["start_s"]
    assert 0.0 <= start <= item.duration_s - edl.duration
    (use,) = repo.gameplay_uses(engine.db(), item.id)
    assert (use.clip_id, use.account_id, use.post_id) == (cid, None, None)
    assert (use.start_s, use.end_s) == pytest.approx((start, start + edl.duration))
    clip = repo.get_clip(engine.db(), cid)
    assert (clip.status, clip.layout) == ("rendered", "gameplay")
    again = clips.clip_render(engine, job(engine, cid, pid))
    assert edl_of(again["path"]).gameplay["start_s"] == start  # a re-render keeps its segment
    assert len(repo.gameplay_uses(engine.db(), item.id)) == 1
    repo.update_clip(engine.db(), cid, edits={"layout": "face"})
    clips.clip_analyze(engine, job(engine, cid, pid))
    clips.clip_render(engine, job(engine, cid, pid))
    assert repo.gameplay_uses(engine.db(), item.id) == []  # its segment is free for other clips again


def test_a_campaign_banning_gameplay_wins_over_a_chosen_split(engine, tmp_path):
    original = make_test_video(tmp_path / "parkour.mp4", seconds=20.0)
    item = gameplay.add_gameplay(engine.db(), engine.paths, original, credit="Me", license="Mine")
    pid, cid = ready_clip(engine, edits={"layout": "gameplay"})
    assert repo.get_clip(engine.db(), cid).layout == "gameplay"
    now = time.time()
    banned = Campaign(
        id=repo.new_id("cp"),
        name="Beast clips",
        marketplace="Whop",
        banned=["gameplay", "hook", "sfx"],
        created_at=now,
        updated_at=now,
    )
    repo.update_project(engine.db(), pid, campaign_id=campaigns.add_campaign(engine.db(), banned).id)
    edl = edl_of(clips.clip_render(engine, job(engine, cid, pid))["path"])
    assert edl.gameplay is None and {s.layout for s in edl.spans} != {"gameplay"}
    assert edl.hook is None and edl.sfx == [] and not (edl.style.hook or edl.style.sfx)
    assert repo.gameplay_uses(engine.db(), item.id) == []
    clip = repo.get_clip(engine.db(), cid)
    assert (clip.layout, clip.layout_reason) == ("face", GAMEPLAY_OFF) and NO_GAMEPLAY_ITEM not in edl.notes
    clips.clip_analyze(engine, job(engine, cid, pid))
    saved = Analysis.model_validate_json(engine.paths.project(pid).analysis_json(cid).read_text())
    assert saved.layout != "gameplay"  # the next look at it agrees


def test_a_gameplay_item_deleted_as_the_render_starts_makes_it_try_again(engine, tmp_path, monkeypatch):
    """The item a render picked is deleted from the Library page before the render records its use: the
    render stops there and runs again, when it picks another item or falls back."""
    original = make_test_video(tmp_path / "parkour.mp4", seconds=20.0)
    item = gameplay.add_gameplay(engine.db(), engine.paths, original, credit="Me", license="Mine")
    pid, cid = ready_clip(engine, edits={"layout": "gameplay"})
    pick = gameplay.pick_item

    def picked_then_deleted(conn, clip_id, **kw):
        got = pick(conn, clip_id, **kw)
        repo.delete_library_item(conn, got.id)
        return got

    monkeypatch.setattr(gameplay, "pick_item", picked_then_deleted)
    with pytest.raises(ClipForgeError) as err:
        clips.clip_render(engine, job(engine, cid, pid))
    assert (err.value.code, err.value.retryable) == ("gameplay_gone", True)
    assert str(err.value) == "That gameplay video left the library as this clip started, so it renders again."
    assert repo.gameplay_uses(engine.db(), item.id) == []


def test_a_render_whose_gameplay_left_as_it_started_runs_again(engine, tmp_path, monkeypatch):
    """Its sentence says it renders again, so the job runs once more, and that run picks another item or
    falls back to the speaker."""
    original = make_test_video(tmp_path / "parkour.mp4", seconds=20.0)
    gameplay.add_gameplay(engine.db(), engine.paths, original, credit="Me", license="Mine")
    pid, cid = ready_clip(engine, edits={"layout": "gameplay"})
    pick, picks = gameplay.pick_item, []

    def first_one_deleted(conn, clip_id, **kw):
        got = pick(conn, clip_id, **kw)
        if got is not None and not picks:
            repo.delete_library_item(conn, got.id)
        picks.append(got)
        return got

    monkeypatch.setattr(gameplay, "pick_item", first_one_deleted)
    engine.runner.backoff_base_s = 0.01
    assert pipeline.queue_render(engine.runner, engine.db(), cid)
    assert engine.wait(60, clips=[cid])
    clip = repo.get_clip(engine.db(), cid)
    assert (clip.status, clip.error, clip.layout) == ("rendered", None, "face")
    (render,) = [j for j in engine.runner.jobs_for(cid) if j.kind == "clip.render"]
    assert (render.status, render.attempts) == ("done", 2)


def test_gameplay_layout_without_a_library_item_falls_back_to_the_speaker(engine, tmp_path):
    pid, cid = ready_clip(engine, edits={"layout": "gameplay"})
    got = clips.clip_render(engine, job(engine, cid, pid))
    assert {s.layout for s in edl_of(got["path"]).spans} == {"face"}
    clip = repo.get_clip(engine.db(), cid)
    assert (clip.status, clip.layout) == ("rendered", "face") and "gameplay" in clip.layout_reason

    original = make_test_video(tmp_path / "parkour.mp4", seconds=20.0)
    item = gameplay.add_gameplay(engine.db(), engine.paths, original, credit="Me", license="Mine")
    clips.clip_analyze(engine, job(engine, cid, pid))
    assert repo.get_clip(engine.db(), cid).layout == "gameplay"
    Path(item.path).unlink()  # gone between the analysis and the render
    got = clips.clip_render(engine, job(engine, cid, pid))
    assert {s.layout for s in edl_of(got["path"]).spans} == {"face"}
    clip = repo.get_clip(engine.db(), cid)
    assert (clip.layout, clip.layout_reason) == ("face", NO_GAMEPLAY_ITEM)


def library(tmp_path):
    """A database with a project, six clips c1 to c6 and no library items."""
    conn = connect(tmp_path / "test.db")
    migrate(conn)
    p = repo.create_project(
        conn,
        source_kind="youtube",
        source_url=URL,
        video_id="x",
        options=ProjectOptions(),
        focus_s=None,
        prompt=None,
    )
    repo.replace_ideas(
        conn, p.id, [ClipRow(id=f"c{i}", project_id=p.id, start_s=0.0, end_s=30.0) for i in range(1, 7)]
    )
    return conn


def game(conn, tmp_path, name, seconds):
    path = tmp_path / f"{name}.mp4"
    path.write_bytes(b"video")
    item = LibraryItem(id=f"l{name}", kind="gameplay", name=name, path=str(path), duration_s=seconds)
    return repo.add_library_item(conn, item)


def test_pick_segment_prefers_unused_segments(tmp_path):
    conn = library(tmp_path)
    long = game(conn, tmp_path, "long", 120.0)
    repo.record_gameplay_use(conn, long.id, 0.0, 30.0, clip_id="c1")
    repo.record_gameplay_use(conn, long.id, 70.0, 100.0, clip_id="c2")
    starts = {gameplay.pick_segment(conn, long, 20.0, rng=random.Random(seed)) for seed in range(40)}
    assert all(30.0 <= s <= 50.0 for s in starts) and len(starts) > 1  # only 30 to 50 overlaps no use

    short = game(conn, tmp_path, "short", 60.0)
    for a, b, cid in ((0.0, 30.0, "c3"), (0.0, 30.0, "c4"), (30.0, 60.0, "c5")):
        repo.record_gameplay_use(conn, short.id, a, b, clip_id=cid)
    assert gameplay.pick_segment(conn, short, 30.0, rng=random.Random(1)) == 30.0  # the half used less
    conn.execute("DELETE FROM clips WHERE id IN ('c3', 'c4')")  # their uses no longer count
    assert gameplay.pick_segment(conn, short, 30.0, rng=random.Random(1)) == 0.0
    conn.execute("DELETE FROM clips WHERE id = 'c5'")
    repo.record_gameplay_use(conn, short.id, 0.0, 30.0, clip_id=None, post_id="t1")  # a post's use counts
    assert gameplay.pick_segment(conn, short, 30.0, rng=random.Random(1)) == 30.0

    tiny = LibraryItem(id="ltiny", kind="gameplay", name="tiny", path="tiny.mp4", duration_s=10.0)
    assert gameplay.pick_segment(conn, tiny, 30.0, rng=random.Random(1)) == 0.0  # shorter than the clip
    unknown = tiny.model_copy(update={"duration_s": None})
    assert gameplay.pick_segment(conn, unknown, 30.0, rng=random.Random(1)) == 0.0


def test_pick_item_prefers_the_least_used_gameplay(tmp_path):
    conn = library(tmp_path)
    a, b = game(conn, tmp_path, "a", 60.0), game(conn, tmp_path, "b", 60.0)
    repo.record_gameplay_use(conn, a.id, 0.0, 10.0, clip_id="c1")
    assert gameplay.pick_item(conn, "c2", rng=random.Random(1)).id == b.id
    assert gameplay.pick_item(conn, "c1", rng=random.Random(1)).id == a.id  # a clip keeps its own gameplay
    Path(a.path).unlink()
    assert gameplay.pick_item(conn, "c1", rng=random.Random(1)).id == b.id  # its file is gone
    Path(b.path).unlink()
    assert gameplay.pick_item(conn, "c1", rng=random.Random(1)) is None


def test_a_clip_holds_one_unposted_gameplay_use(tmp_path):
    conn = library(tmp_path)
    a, b = game(conn, tmp_path, "a", 60.0), game(conn, tmp_path, "b", 60.0)
    repo.record_gameplay_use(conn, a.id, 0.0, 10.0, clip_id="c1")
    repo.record_gameplay_use(conn, b.id, 5.0, 15.0, clip_id="c1")
    repo.record_gameplay_use(conn, b.id, 20.0, 30.0, clip_id="c1", account_id="a1", post_id="t1")
    assert repo.gameplay_uses(conn, a.id) == []
    assert [(u.start_s, u.post_id) for u in repo.gameplay_uses(conn, b.id)] == [(5.0, None), (20.0, "t1")]
    repo.clear_gameplay_uses(conn, "c1")
    assert [u.post_id for u in repo.gameplay_uses(conn, b.id)] == ["t1"]
    assert repo.gameplay_use_of(conn, "c1") is None  # what a post showed is the post's, not the clip's
    assert gameplay.pick_item(conn, "c1", rng=random.Random(1)).id == a.id
    start = gameplay.segment_for(conn, "c1", b, 10.0, rng=random.Random(1))
    assert start <= 10.0 or start >= 30.0  # not the posted segment again


# Making the clips again


def test_rerun_replaces_unedited_rendered_clips():
    eng = fake_engine(speech=FakeSpeech(lines(36)), provider=FakeProvider(brain_handlers()))
    try:
        (p,), _ = eng.add_sources(URL, ProjectOptions(render=False))
        eng.start(p.id)
        assert eng.wait(60, projects=[p.id])
        plain, edited = [c.id for c in eng.clips(p.id)][:2]
        pp = eng.paths.project(p.id)
        for cid in (plain, edited):
            pp.render_dir(cid, 1).mkdir(parents=True)
            (pp.render_dir(cid, 1) / "clip.mp4").write_bytes(b"video")
            repo.update_clip(eng.db(), cid, status="rendered")
        repo.update_clip(eng.db(), edited, edits={"preset": "clean"})
        assert eng.rerun_selection(p.id)
        assert eng.wait(60, projects=[p.id])
        now = {c.id: c for c in eng.clips(p.id)}
        assert plain not in now and not pp.clip_dir(plain).exists()
        assert now[edited].status == "rendered" and (pp.render_dir(edited, 1) / "clip.mp4").exists()
        assert len(now) == 3  # the kept clip and the two new ones
    finally:
        eng.close()


# The clip's own edits


def test_save_edits_checks_the_patch_and_moves_the_range(engine):
    _, cid = ready_clip(engine)
    clip = engine.save_edits(cid, {"emoji": False}, (1.0, 4.3))  # the words are 1/3 s apart
    assert (clip.start_s, clip.end_s) == pytest.approx((1.0, 12.8 / 3))
    assert clip.edits == {"emoji": False, "orig_range": [1.0, 4.5]}
    with pytest.raises(EditError) as err:
        engine.save_edits(cid, {"accent": "red"})
    assert (err.value.field, err.value.message) == ("accent", "The accent colour must look like #FFD60A.")
    assert repo.get_clip(engine.db(), cid).edits == {"emoji": False, "orig_range": [1.0, 4.5]}
    clip = engine.save_edits(cid, {"emoji": None}, None)
    assert (clip.start_s, clip.end_s, clip.sentence_ids, clip.edits) == (1.0, 4.5, ["S1"], None)
    with pytest.raises(ClipForgeError) as err:
        engine.save_edits("nope", {})
    assert err.value.code == "not_found"


def test_everything_struck_fails_with_the_empty_sentence(engine):
    pid, cid = ready_clip(engine)
    first = clips.clip_render(engine, job(engine, cid, pid))
    assert engine.render_clip(cid, {"struck": list(range(18))})
    assert engine.wait(60, clips=[cid])
    clip = repo.get_clip(engine.db(), cid)
    assert (clip.status, clip.error, clip.current_render_id) == ("failed", EMPTY, first["render_id"])
    assert [r.version for r in repo.list_renders(engine.db(), cid)] == [1]
    assert clip.edits == {"struck": list(range(18))}
    assert EMPTY == "Nothing is left of this clip once its cuts are made, so bring back some of its words."


def test_packaging_edits_reach_the_render(engine):
    edits = {
        "hook_title": "A new hook",
        "accent_word": "new",
        "fact_cards": [{"sid": "S1", "text": "A card"}],
        "hook_whole_clip": True,  # so the card has room under the hook
    }
    pid, cid = ready_clip(engine, edits=edits)
    edl = edl_of(clips.clip_render(engine, job(engine, cid, pid))["path"])
    assert (edl.hook["title"], edl.hook["accent_word"]) == ("A new hook", "new")
    assert [c["text"] for c in edl.cards] == ["A card"]
    assert Packaging.model_validate(repo.get_clip(engine.db(), cid).packaging) == PACKAGING  # kept as made


def test_project_defaults_apply_under_clip_edits(engine):
    pid, cid = ready_clip(engine, layout="fit", caption_preset="clean")
    edl = edl_of(clips.clip_render(engine, job(engine, cid, pid))["path"])
    assert ({s.layout for s in edl.spans}, edl.style.preset) == ({"fit"}, "clean")
    repo.update_clip(engine.db(), cid, edits={"layout": "auto", "preset": "beast"})
    clips.clip_analyze(engine, job(engine, cid, pid))
    edl = edl_of(clips.clip_render(engine, job(engine, cid, pid))["path"])
    assert ({s.layout for s in edl.spans}, edl.style.preset) == ({"face"}, "beast")


def test_deleted_gameplay_item_falls_back(engine, tmp_path):
    a, b = (
        gameplay.add_gameplay(
            engine.db(),
            engine.paths,
            make_test_video(tmp_path / f"{n}.mp4", seconds=8.0),
            credit="Me",
            license="Mine",
        )
        for n in ("a", "b")
    )
    repo.record_gameplay_use(engine.db(), b.id, 0.0, 4.0, clip_id=None, post_id="t1")  # b is the more used
    pid, cid = ready_clip(engine, edits={"layout": "gameplay", "gameplay_item": b.id})
    edl = edl_of(clips.clip_render(engine, job(engine, cid, pid))["path"])
    assert edl.gameplay["item_id"] == b.id and GAMEPLAY_GONE not in edl.notes
    Path(b.path).unlink()
    edl = edl_of(clips.clip_render(engine, job(engine, cid, pid))["path"])
    assert edl.gameplay["item_id"] == a.id and GAMEPLAY_GONE in edl.notes
    assert GAMEPLAY_GONE == "The gameplay video picked for this clip is gone, so another one is used"
    Path(a.path).unlink()
    edl = edl_of(clips.clip_render(engine, job(engine, cid, pid))["path"])
    assert {s.layout for s in edl.spans} == {"face"}
    assert repo.get_clip(engine.db(), cid).layout_reason == NO_GAMEPLAY_ITEM


def test_pick_item_prefers_the_one_asked_for(tmp_path):
    conn = library(tmp_path)
    a, b = game(conn, tmp_path, "a", 60.0), game(conn, tmp_path, "b", 60.0)
    repo.record_gameplay_use(conn, a.id, 0.0, 10.0, clip_id="c1")
    assert gameplay.pick_item(conn, "c2", rng=random.Random(1), wanted=a.id).id == a.id
    assert gameplay.pick_item(conn, "c1", rng=random.Random(1), wanted=b.id).id == b.id  # over its own
    assert gameplay.pick_item(conn, "c2", rng=random.Random(1), wanted="gone").id == b.id
    Path(a.path).unlink()
    assert gameplay.pick_item(conn, "c2", rng=random.Random(1), wanted=a.id).id == b.id


def test_shuffle_picks_a_new_segment(engine, tmp_path):
    original = make_test_video(tmp_path / "parkour.mp4", seconds=20.0)
    item = gameplay.add_gameplay(engine.db(), engine.paths, original, credit="Me", license="Mine")
    pid, cid = ready_clip(engine, edits={"layout": "gameplay"})
    clips.clip_render(engine, job(engine, cid, pid))
    before = repo.gameplay_use_of(engine.db(), cid)
    assert before.item_id == item.id
    clip = engine.shuffle_gameplay(cid)
    assert (clip.edits, clip.status) == ({"layout": "gameplay", "gameplay_seed": 1}, "queued")
    assert repo.gameplay_use_of(engine.db(), cid) is None  # so the render picks a segment afresh
    assert engine.wait(60, clips=[cid])
    after = repo.gameplay_use_of(engine.db(), cid)
    assert after.item_id == item.id and after.start_s != before.start_s
    assert repo.get_clip(engine.db(), cid).status == "rendered"
    assert engine.shuffle_gameplay(cid).edits["gameplay_seed"] == 2
    with pytest.raises(ClipForgeError) as err:
        engine.shuffle_gameplay("nope")
    assert err.value.code == "not_found"


def test_render_records_its_edits_key(engine):
    pid, cid = ready_clip(engine, edits={"tiktok_caption": "Hi"}, caption_preset="beast")
    got = clips.clip_render(engine, job(engine, cid, pid))
    row = repo.get_render(engine.db(), got["render_id"])
    clip = repo.get_clip(engine.db(), cid)
    assert row.edits_key == render_key(clip, {"preset": "beast", "tiktok_caption": "Hi"})
    assert row.edits_key != render_key(clip, {"tiktok_caption": "Hi"})  # the project's default is in it
    assert edl_of(row.path).style.preset == "beast"
