"""A voiceover recorded or chosen in the Sound tab (phase 5g Task 4): the take checked and stored, the edit
it becomes, the speech ducked under it and the notes when it is cut or gone."""

import re
from pathlib import Path

import numpy as np
import pytest

from clipforge import repo, voiceover
from clipforge.app import checked_edits
from clipforge.edits import EditError
from clipforge.errors import ClipForgeError
from clipforge.jobs.stages import clips
from clipforge.media.ffmpeg import ffmpeg_bin, probe, run
from clipforge.render.audio import SR, mix_voiceover
from clipforge.render.edl import VO_CUT, VO_GONE, VO_LATE
from tests.fakes import fake_engine
from tests.helpers import make_test_audio, make_test_video, make_wav
from tests.server import api_client
from tests.unit.test_clip_jobs import OneFace, SectionTube, add_clips, project, ready_source
from tests.unit.test_render_job import PACKAGING, band, edl_of, job


@pytest.fixture
def engine():
    eng = fake_engine(youtube=SectionTube())
    eng.face_detector = OneFace()
    yield eng
    eng.close()


def five_second_clip(engine):
    """A project with a 7 s grey source and one packaged clip from 1.0 s to 6.0 s, analysed."""
    pid = project(engine)
    ready_source(engine, pid, seconds=7)
    (cid,) = add_clips(engine, pid, start=1.0, end=6.0)
    repo.update_clip(engine.db(), cid, packaging=PACKAGING.model_dump(mode="json"))
    clips.clip_analyze(engine, job(engine, cid, pid))
    return pid, cid


def takes(engine, pid, cid) -> Path:
    return engine.paths.project(pid).clip_dir(cid) / "voiceover"


def webm(path: Path, seconds: float, hz: int) -> Path:
    """A tone as Chrome's recorder saves one: Opus in WebM."""
    run([ffmpeg_bin(), "-v", "error", "-f", "lavfi", "-i", f"sine=frequency={hz}:duration={seconds}",
         "-c:a", "libopus", str(path)])  # fmt: skip
    return path


def test_voiceover_refuses_unusable_takes(engine, tmp_path):
    pid, cid = five_second_clip(engine)
    empty = tmp_path / "take.webm"
    empty.write_bytes(b"")
    text = tmp_path / "take.m4a"
    text.write_text("not a recording")
    unusable = {
        empty: voiceover.VO_UNREADABLE,
        make_wav(tmp_path / "quiet.wav", [(3.0, 0.0)]): voiceover.VO_SILENT,
        text: voiceover.VO_UNREADABLE,
        make_test_video(tmp_path / "mute.mp4", seconds=1.0, audio=False): voiceover.VO_UNREADABLE,
        make_test_audio(tmp_path / "long.wav", seconds=240.0, tone_hz=300): voiceover.VO_LONG,
    }
    for path, sentence in unusable.items():
        with pytest.raises(ClipForgeError) as caught:
            voiceover.take_recording(engine, cid, path)
        assert caught.value.message == sentence, path.name
        assert not takes(engine, pid, cid).exists(), path.name
    assert (repo.get_clip(engine.db(), cid).edits or {}).get("voiceover") is None
    assert engine.runner.jobs_for(cid) == []


def test_a_take_the_recorder_stopped_at_3_minutes_is_kept(engine, tmp_path):
    _, cid = five_second_clip(engine)
    kept = voiceover.take_recording(engine, cid, webm(tmp_path / "take.webm", 180.2, 300))
    assert kept["duration_s"] == voiceover.VO_MAX_S  # what runs past 3:00 is cut off
    four = make_test_audio(tmp_path / "long.wav", seconds=240.0, tone_hz=300)
    with pytest.raises(ClipForgeError) as refused:
        voiceover.take_recording(engine, cid, four)
    assert (refused.value.code, refused.value.message) == ("too_long", voiceover.VO_LONG)


def test_a_file_too_big_is_told_its_size_not_its_length(engine, tmp_path, monkeypatch):
    """A short take saved as a huge file (uncompressed, say) is over the size cap, not 3 minutes."""
    _, cid = five_second_clip(engine)
    monkeypatch.setattr(voiceover, "VO_MAX_BYTES", 1000)
    wav = make_test_audio(tmp_path / "take.wav", 2.0, 440)
    with pytest.raises(ClipForgeError) as refused:
        voiceover.take_recording(engine, cid, wav)
    assert (refused.value.code, refused.value.message) == ("too_big", voiceover.VO_BIG)
    sent = api_client(engine).post(
        f"/api/clips/{cid}/voiceover", params={"name": "take.wav"}, content=wav.read_bytes()
    )
    assert (sent.status_code, sent.json()["message"], sent.json()["field"]) == (413, voiceover.VO_BIG, "file")
    assert voiceover.VO_BIG == "That file is bigger than 50 MB. Pick a smaller one, such as an M4A or MP3."


def test_take_becomes_the_edit(engine, tmp_path):
    pid, cid = five_second_clip(engine)
    got = voiceover.take_recording(engine, cid, webm(tmp_path / "take.webm", 2.0, 440))
    assert re.fullmatch(r"[0-9a-f]{10}\.wav", got["file"])
    assert got["duration_s"] == pytest.approx(2.0, abs=0.05)
    assert repo.get_clip(engine.db(), cid).edits["voiceover"] == got["file"]
    assert engine.runner.jobs_for(cid)  # a render is queued
    stored = takes(engine, pid, cid) / got["file"]
    info = probe(stored)
    assert (info.acodec, info.sample_rate, info.channels) == ("pcm_s16le", 48000, 2)
    again = voiceover.take_recording(engine, cid, make_test_audio(tmp_path / "b.m4a", 1.0, 600))
    assert sorted(p.name for p in takes(engine, pid, cid).iterdir()) == [again["file"]]  # one take kept


def test_voiceover_edit_is_checked(engine):
    pid, cid = five_second_clip(engine)
    clip = repo.get_clip(engine.db(), cid)
    for patch in ({"voiceover": "0123456789.wav"}, {"voiceover": "../edl.json"}, {"voiceover_at": -1.0},
                  {"voiceover_at": 9.0}, {"voiceover_at": float("nan")}):  # fmt: skip
        with pytest.raises(EditError) as caught:
            checked_edits(engine.db(), engine.paths, clip, patch)
        assert caught.value.field == next(iter(patch)), patch
    assert (
        checked_edits(engine.db(), engine.paths, clip, {"voiceover_at": 2.5})["edits"]["voiceover_at"] == 2.5
    )


def amplitude(x: np.ndarray, hz: float, a: float, b: float) -> float:
    """How strong ``hz`` is between ``a`` and ``b`` seconds on the left channel."""
    part = x[round(a * SR) : round(b * SR), 0].astype(np.float64)
    t = np.arange(round(a * SR), round(a * SR) + len(part)) / SR
    return 2 * float(
        np.hypot(np.mean(part * np.sin(2 * np.pi * hz * t)), np.mean(part * np.cos(2 * np.pi * hz * t)))
    )


def tone(seconds: float, hz: float, level: float) -> np.ndarray:
    t = np.arange(round(seconds * SR)) / SR
    x = level * np.sin(2 * np.pi * hz * t)
    return np.stack([x, x], axis=1).astype(np.float32)


def test_speech_ducks_under_the_voiceover():
    speech = tone(3.0, 220, 0.1)
    mixed = mix_voiceover(speech, tone(1.0, 1000, 0.05), 1.0)
    assert mixed.shape == speech.shape
    alone = amplitude(speech, 220, 0.2, 0.9)
    assert abs(20 * np.log10(amplitude(mixed, 220, 0.2, 0.9) / alone)) < 0.5
    assert 20 * np.log10(amplitude(mixed, 220, 1.2, 1.8) / alone) <= -8.0
    assert amplitude(mixed, 1000, 1.2, 1.8) > 0.01  # the take plays over it
    assert amplitude(mixed, 1000, 0.2, 0.9) < 1e-4


def test_long_voiceover_is_cut_at_the_end(engine, tmp_path):
    pid, cid = five_second_clip(engine)
    voiceover.take_recording(engine, cid, make_test_audio(tmp_path / "long.wav", 6.0, 1000))
    engine.save_edits(cid, {"voiceover_at": 3.0})
    got = clips.clip_render(engine, job(engine, cid, pid))
    edl = edl_of(got["path"])
    assert edl.voiceover["at"] == 3.0 and edl.voiceover["duration_s"] == pytest.approx(6.0, abs=0.05)
    assert VO_CUT in edl.notes
    assert f"Note: {VO_CUT}" in (Path(got["path"]).parent / "render.log").read_text()
    info = probe(got["path"])
    assert info.audio_duration_s == pytest.approx(info.video_duration_s, abs=0.05)
    assert band(got["path"], 1000) > 0.1 * band(got["path"], 300)  # the take is in the mix


def test_a_take_starting_after_the_cut_clip_ends_is_left_out(engine, tmp_path):
    """Cuts made the clip shorter than where the take starts: nothing of it would play, so it is left out
    with a note, and it doesn't count as an original edit."""
    pid, cid = five_second_clip(engine)
    voiceover.take_recording(engine, cid, make_test_audio(tmp_path / "take.wav", 2.0, 1000))
    clip = engine.save_edits(cid, {"voiceover_at": 4.0})
    pp = engine.paths.project(pid)
    assert clips.voiceover_for(pp, clip, 3.0) == (None, VO_LATE)
    assert clips.voiceover_for(pp, clip, 4.3) == (None, VO_LATE)  # under half a second of it would play
    take, note = clips.voiceover_for(pp, clip, 5.0)
    assert take is not None and take["at"] == 4.0 and note == VO_CUT
    assert VO_LATE == "The voiceover starts after the clip ends, so this version has none"


def test_missing_take_renders_without_it(engine, tmp_path):
    pid, cid = five_second_clip(engine)
    got = voiceover.take_recording(engine, cid, make_test_audio(tmp_path / "take.wav", 2.0, 1000))
    (takes(engine, pid, cid) / got["file"]).unlink()
    out = clips.clip_render(engine, job(engine, cid, pid))
    edl = edl_of(out["path"])
    assert edl.voiceover is None and VO_GONE in edl.notes
    assert repo.get_clip(engine.db(), cid).status == "rendered"
    assert band(out["path"], 1000) < 0.01 * band(out["path"], 300)


def test_voiceover_routes(engine, tmp_path):
    pid, cid = five_second_clip(engine)
    client = api_client(engine)
    wav = make_test_audio(tmp_path / "take.wav", 2.0, 440).read_bytes()
    refused = client.post(f"/api/clips/{cid}/voiceover", params={"name": "take.txt"}, content=b"hello")
    assert refused.status_code == 415 and refused.json()["field"] == "file"
    silent = make_wav(tmp_path / "quiet.wav", [(2.0, 0.0)]).read_bytes()
    refused = client.post(f"/api/clips/{cid}/voiceover", params={"name": "quiet.wav"}, content=silent)
    assert refused.status_code == 422
    assert (refused.json()["message"], refused.json()["field"]) == (voiceover.VO_SILENT, "file")
    assert client.post("/api/clips/nope/voiceover", params={"name": "a.wav"}, content=wav).status_code == 404
    saved = client.post(f"/api/clips/{cid}/voiceover", params={"name": "take.wav"}, content=wav)
    assert saved.status_code == 202
    file = saved.json()["edits"]["voiceover"]
    assert list(engine.paths.uploads.iterdir()) == []  # the upload's folder is gone
    played = client.get(f"/api/clips/{cid}/voiceover/{file}")
    assert played.status_code == 200 and played.headers["content-type"] == "audio/wav"
    assert client.get(f"/api/clips/{cid}/voiceover/0123456789.wav").status_code == 404
    assert client.get(f"/api/clips/{cid}/voiceover/..%2Fanalysis.json").status_code == 404
    removed = client.delete(f"/api/clips/{cid}/voiceover")
    assert removed.status_code == 202 and removed.json()["edits"].get("voiceover") is None
    assert not takes(engine, pid, cid).exists()
