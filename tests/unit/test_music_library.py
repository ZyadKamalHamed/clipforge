"""Your music library (SPEC 8.6 and 12): tracks added with who made them, what the licence lets you do,
whether it asks for credit, and the moods and energy suggestions match a clip with. Each is copied into
``library/music/``; a file with no sound ClipForge can read is refused and nothing of it is kept."""

from pathlib import Path

import pytest

from clipforge import repo
from clipforge.errors import ClipForgeError
from clipforge.library import music
from tests.fakes import fake_engine
from tests.helpers import make_test_audio, make_test_video

NO_SOUND = "That file has no sound ClipForge can read. Pick an audio file such as MP3, M4A or WAV."
NO_LICENCE = "Say what the licence lets you do."
ARTLIST = "Bought from Artlist for social use"


@pytest.fixture
def engine():
    eng = fake_engine()
    yield eng
    eng.close()


def add(engine, path, **fields):
    given = {
        "title": "Night Drive",
        "artist": "Kai Lune",
        "source_url": None,
        "license": ARTLIST,
        "license_url": None,
        "credit_required": False,
        "moods": ["hype"],
        "energy": 4,
    } | fields
    return music.add_music(engine, path, **given)


def kept_files(engine) -> list[Path]:
    folder = engine.paths.music
    return sorted(folder.iterdir()) if folder.is_dir() else []


def test_add_music_keeps_a_copy_and_its_notes(engine, tmp_path):
    src = make_test_audio(tmp_path / "in" / "aurora.m4a", seconds=3.0)
    item = add(
        engine,
        src,
        source_url="https://artlist.io/song/123",
        credit_required=True,
        moods=["hype", "dark", "hype"],
    )
    assert (item.kind, item.name, item.credit, item.license, item.source_url) == (
        "music",
        "Night Drive",
        "Kai Lune",
        ARTLIST,
        "https://artlist.io/song/123",
    )
    assert (item.credit_required, item.energy, item.tags) == (True, 4, ["hype", "dark"])
    assert item.duration_s == pytest.approx(3.0, abs=0.1)
    kept = Path(item.path)
    assert kept.parent == engine.paths.music and kept.name == f"{item.id}.m4a"
    assert kept.read_bytes() == src.read_bytes() and src.exists()  # a copy: the original stays
    assert repo.get_library_item(engine.db(), item.id) == item
    named = add(engine, src, title=None, artist=None, moods=[], energy=None)
    assert (named.name, named.credit, named.tags, named.energy) == (
        "aurora",
        None,
        [],
        None,
    )  # the file's name
    talk = add(engine, make_test_video(tmp_path / "talk.MP4", seconds=2.0), title="bass from a video")
    assert Path(talk.path).name == f"{talk.id}.mp4"  # a video file with sound is music too
    assert [i.name for i in music.music_items(engine.db())] == ["aurora", "bass from a video", "Night Drive"]
    assert music.users_of(engine.db(), item.id) == 0


def test_add_music_refuses_files_without_sound(engine, tmp_path):
    silent = make_test_video(tmp_path / "silent.mp4", audio=False)
    text = tmp_path / "track.mp3"
    text.write_text("not music at all\n" * 200)
    notes = tmp_path / "notes.txt"
    notes.write_text("a tone, honest\n")
    for src in (silent, text, notes):
        with pytest.raises(music.MusicRefused) as err:
            add(engine, src)
        assert (err.value.field, err.value.message) == ("file", NO_SOUND), src.name
    blip = make_test_audio(tmp_path / "blip.wav", seconds=0.5)
    with pytest.raises(music.MusicRefused) as err:
        add(engine, blip)
    assert (err.value.field, err.value.message) == ("file", music.TOO_SHORT)
    assert music.music_items(engine.db()) == [] and kept_files(engine) == []


def test_add_music_needs_a_licence(engine, tmp_path):
    src = make_test_audio(tmp_path / "beat.m4a", seconds=2.0)
    for licence in ("", "   ", None):
        with pytest.raises(music.MusicRefused) as err:
            add(engine, src, license=licence)
        assert (err.value.field, err.value.message) == ("license", NO_LICENCE), repr(licence)
    assert music.music_items(engine.db()) == [] and kept_files(engine) == []
    item = add(engine, src, license="  Own recording  ")
    assert item.license == "Own recording"
    for change, field, sentence in (
        ({"license": " "}, "license", NO_LICENCE),
        ({"title": " "}, "title", music.NO_TITLE),
        ({"energy": 6}, "energy", music.ENERGY_WRONG),
        ({"energy": True}, "energy", music.ENERGY_WRONG),
        ({"moods": ["sad"]}, "moods", music.MOOD_WRONG),
        (
            {"source_url": "ftp://example.com/x"},
            "source_url",
            "The link to where it's from must start with http:// or https://.",
        ),
        ({"artist": "x" * 201}, "artist", "Keep the artist to 200 characters."),
    ):
        with pytest.raises(music.MusicRefused) as err:
            music.update_music(engine, item.id, **change)
        assert (err.value.field, err.value.message) == (field, sentence), change
    changed = music.update_music(
        engine, item.id, title=" Slow beat ", energy=2, moods=["calm"], credit_required=True, artist=""
    )
    assert (changed.name, changed.energy, changed.tags, changed.credit_required, changed.credit) == (
        "Slow beat",
        2,
        ["calm"],
        True,
        None,
    )
    assert changed == repo.get_library_item(engine.db(), item.id) and changed.license == "Own recording"
    with pytest.raises(ClipForgeError) as err:
        music.update_music(engine, "lnothere00", energy=1)
    assert err.value.message == music.NOT_MUSIC
