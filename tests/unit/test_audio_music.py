"""Music under a clip's speech (SPEC 8.6): a track from your library, looped or cut to the clip, sat 12 dB
under the speech from the first frame and ducked a further 6 dB while someone speaks, faded over the last
300 ms; and the one track a clip's "auto" music picks."""

from pathlib import Path

import numpy as np
import pytest

from clipforge import repo
from clipforge.jobs.stages import clips
from clipforge.models import LibraryItem
from clipforge.render import audio
from clipforge.render.audio import MUSIC_FADE_S, SR, decode_track, loudness, mix_music
from clipforge.render.edl import MUSIC_GONE, MUSIC_NO_MATCH
from clipforge.render.styles import Style
from clipforge.vision.decode import ClipSource
from tests.fakes import fake_engine
from tests.helpers import make_test_audio, make_test_video
from tests.unit.test_audio import edl_for, rms, stereo
from tests.unit.test_clip_jobs import add_clips, project


@pytest.fixture
def engine():
    eng = fake_engine()
    yield eng
    eng.close()


def pink(seconds, seed=1):
    """Steady pink noise at about -20 dBFS RMS, as a stand-in for a music bed."""
    n = round(seconds * SR)
    spec = np.fft.rfft(np.random.default_rng(seed).normal(0.0, 1.0, n))
    f = np.arange(len(spec), dtype=np.float64)
    f[0] = 1.0
    x = np.fft.irfft(spec / np.sqrt(f), n)
    return stereo(x * 0.1 / np.sqrt(np.mean(np.square(x))))


def talking(seconds, quiet_from):
    """A 300 Hz tone as the speech up to ``quiet_from``, then silence."""
    t = np.arange(round(seconds * SR)) / SR
    x = 0.2 * np.sin(2 * np.pi * 300 * t)
    x[round(quiet_from * SR) :] = 0.0
    return stereo(x)


def part(x, a, b):
    return x[round(a * SR) : round(b * SR)]


def test_music_sits_12_db_under_the_speech():
    speech = talking(4.0, quiet_from=2.0)
    level = loudness(speech)
    bed = pink(4.0)
    music = mix_music(speech, bed, SR) - speech
    assert music.shape == speech.shape and music.dtype == np.float32
    # the middle of each part, clear of the duck's attack and release and of the fade at the end
    assert loudness(part(music, 2.8, 3.6)) == pytest.approx(level - 12, abs=1.0)
    assert loudness(part(music, 0.2, 1.8)) == pytest.approx(level - 18, abs=1.5)
    lower = mix_music(speech, bed, SR, under_db=-20.0) - speech
    assert loudness(part(lower, 2.8, 3.6)) == pytest.approx(level - 20, abs=1.0)
    # with no speech at all the bed sits at -26 LUFS
    alone = mix_music(np.zeros_like(speech), bed, SR)
    assert loudness(part(alone, 0.2, 3.6)) == pytest.approx(-26.0, abs=1.0)


def test_music_fades_over_the_last_300_ms():
    assert MUSIC_FADE_S == 0.3
    music = mix_music(np.zeros((3 * SR, 2), np.float32), pink(3.0, seed=4), SR)
    before = rms(part(music, 2.4, 2.65))
    assert rms(part(music, 2.66, 2.7)) == pytest.approx(before, rel=0.25)  # not faded yet
    assert rms(music[-round(0.03 * SR) :]) < 0.15 * before
    assert not music[-1].any()


def test_short_track_loops_without_a_click(tmp_path):
    # 437 Hz over 0.7 s ends partway through a cycle, so a plain loop would jump at each seam
    track = make_test_audio(tmp_path / "loop.wav", seconds=0.7, tone_hz=437)
    bed = decode_track(track, 3 * SR)
    assert bed.shape == (3 * SR, 2) and bed.dtype == np.float32
    assert np.abs(np.diff(bed, axis=0)).max() <= 0.05
    assert rms(part(bed, 2.4, 2.6)) > 0.5 * rms(part(bed, 0.2, 0.4))  # still playing after four loops
    # a long mono MP3 at 44.1 kHz is cut to the clip, the same on both channels
    long = make_test_audio(tmp_path / "long.mp3", seconds=5.0, tone_hz=500)
    cut = decode_track(long, 2 * SR)
    assert cut.shape == (2 * SR, 2) and np.allclose(cut[:, 0], cut[:, 1])
    assert rms(part(cut, 1.5, 1.9)) > 0.05


def test_no_music_leaves_the_sound_as_it_was(tmp_path, monkeypatch):
    src = ClipSource(make_test_video(tmp_path / "talk.mp4", seconds=3.0))
    edl = edl_for([(0.5, 2.5)])
    heard = []
    monkeypatch.setattr(audio, "finish", lambda pcm, sr, out: heard.append(pcm))
    clips._sound(src, edl, tmp_path / "a.m4a", None)
    base = 0.5 - clips.AUDIO_PAD_S
    speech = audio.clean_speech(
        audio.assemble(audio.decode_audio(src, base, 2.5 + clips.AUDIO_PAD_S), base, edl)
    )
    assert np.array_equal(heard[0], audio.mix_sfx(speech, edl.sfx, fade_s=edl.audio_fade_s))
    track = make_test_audio(tmp_path / "bed.m4a", seconds=2.0, tone_hz=1000)
    with_music = edl.model_copy(
        update={"music": {"item_id": "l1", "title": "Bed", "artist": None, "db": -12.0, "credit": None}}
    )
    clips._sound(src, with_music, tmp_path / "b.m4a", track)
    assert heard[1].shape == heard[0].shape and not np.array_equal(heard[1], heard[0])


def track(engine, name, moods, energy=None, *, file=True):
    path = engine.paths.music / f"{name}.m4a"
    if file:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
    item = LibraryItem(
        id=repo.new_id("l"), kind="music", name=name, path=str(path), tags=moods, energy=energy, credit="Me"
    )
    return repo.add_library_item(engine.db(), item)


def a_clip(engine, mood="hype", energy=4):
    (cid,) = add_clips(engine, project(engine))
    repo.update_clip(engine.db(), cid, packaging={"mood": mood, "energy": energy})
    return repo.get_clip(engine.db(), cid)


def test_auto_music_takes_the_best_match(engine):
    c = a_clip(engine)
    assert clips.music_for(engine.db(), c, Style()) == (None, None)  # music is off by default
    assert clips.music_for(engine.db(), c, Style(music="auto")) == (None, MUSIC_NO_MATCH)
    track(engine, "Low Hum", ["dark"], 1)
    near = track(engine, "Laugh Track", ["funny"], 4)
    assert clips.music_for(engine.db(), c, Style(music="auto")) == (near, None)
    best = track(engine, "Night Drive", ["hype"], 4)
    track(engine, "Anthem", ["hype"], 4, file=False)  # its file has gone, so it can't be picked
    assert clips.music_for(engine.db(), c, Style(music="auto")) == (best, None)
    # a track you named is used whatever its mood, and one that is gone gives none
    dark = repo.list_library(engine.db(), "music")[0]
    assert clips.music_for(engine.db(), c, Style(music=dark.id)) == (dark, None)
    assert clips.music_for(engine.db(), c, Style(music="lgone")) == (None, MUSIC_GONE)
    Path(dark.path).unlink()
    assert clips.music_for(engine.db(), c, Style(music=dark.id)) == (None, MUSIC_GONE)
    assert MUSIC_NO_MATCH == "No track in your music library matches this clip's mood, so it has none"
    assert MUSIC_GONE == "The music track picked for this clip is gone, so this version has none"


def test_music_record_names_the_credit_only_when_the_licence_asks(engine):
    item = track(engine, "Night Drive", ["hype"], 4)
    style = Style(music=item.id, music_db=-18.0)
    assert clips.music_record(item, style) == {
        "item_id": item.id,
        "title": "Night Drive",
        "artist": "Me",
        "db": -18.0,
        "credit": None,
    }
    asks = item.model_copy(update={"credit_required": True})
    assert clips.music_record(asks, style)["credit"] == "Music: Night Drive by Me"
    nameless = asks.model_copy(update={"credit": None})
    assert clips.music_record(nameless, style)["credit"] == "Music: Night Drive"
    assert clips.music_record(None, style) is None
