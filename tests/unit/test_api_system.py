"""The settings, library, fonts and doctor API (SPEC 12, 14.7 and 16), and the yt-dlp update behind the
Doctor's Update button. Nothing here runs uv or reaches the internet: the update is given a fake ``run``."""

import dataclasses
import io
import json
import os
import subprocess
import threading
import time
import wave
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pytest

from clipforge import app as app_module
from clipforge import cli, repo, service, ytdlp_update
from clipforge.app import take_home
from clipforge.assets import FONTS_DIR
from clipforge.config import PASSES, model_for
from clipforge.doctor import run_checks
from clipforge.jobs import stages
from clipforge.jobs.runner import JobRunner
from clipforge.library import gameplay, sfx
from clipforge.models import ProjectOptions, Transcript
from clipforge.paths import Paths
from clipforge.server.routes import library, system
from clipforge.ytdlp_update import UpdateResult, update_ytdlp
from tests.fakes import FakeSpeech, fake_engine, lines
from tests.helpers import make_test_audio, make_test_video
from tests.server import api_client
from tests.unit.test_clip_jobs import URL, add_clips, project

CHOICE = "Pick one of the choices."
SWITCH = "That setting is on or off."
EVERY_PASS = "Pick a model for every pass, or one model for all of them."
NOT_READABLE = "That file isn't a video ClipForge can read."
WAIT = "Wait for the downloads to finish, then update."
VERSION = "import yt_dlp.version; print(yt_dlp.version.__version__)"


@pytest.fixture
def engine():
    eng = fake_engine()
    yield eng
    eng.close()


def put(client, **changes):
    return client.put("/api/settings", json=changes)


def problem(r) -> tuple[int, str, str]:
    body = r.json()
    return r.status_code, body["field"], body["message"]


# Settings


def test_settings_get_has_values_and_choices(engine):
    got = api_client(engine).get("/api/settings").json()
    assert got["values"] == engine.settings.model_dump(mode="json")
    values = got["values"]
    assert (values["delete_sources_after_days"], values["asr_language"]) == (14, "auto")
    assert values["create_options"] == ProjectOptions().model_dump(mode="json")
    choices = got["choices"]
    assert choices["llm_provider"] == ["claude_cli", "anthropic", "gemini", "openai_compat", "heuristic"]
    assert choices["caption_preset"] == ["bold_pop", "hormozi", "beast", "pill", "clean", "one_word", "neon"]
    assert choices["theme"] == ["dark", "light", "system"]
    assert choices["create_options.layout"] == [
        "auto",
        "face",
        "stacked",
        "gameplay",
        "stream",
        "fit",
        "motion",
    ]
    assert choices["create_options.caption_preset"] == choices["caption_preset"]  # None is "the default"
    assert {
        "on_llm_limit",
        "asr_engine",
        "download_quality",
        "auto_render",
        "encoder",
        "gameplay_mode",
    } <= set(choices)
    assert not {"accent_color", "sfx", "llm_parallel", "create_options.clip_count"} & set(choices)
    assert got["passes"] == ["discover", "rerank", "package", "review"] == list(PASSES)
    assert got["models"]["claude_cli"] == {
        "discover": "opus",
        "rerank": "opus",
        "package": "sonnet",
        "review": "sonnet",
    }
    assert got["economy_models"]["claude_cli"] == "sonnet"


BAD_SETTINGS = [
    ({"accent_color": "gold"}, "accent_color", "Accent colour must look like #FFD60A."),
    ({"llm_parallel": 0}, "llm_parallel", "AI calls at once must be a whole number from 1 to 10."),
    ({"llm_parallel": 2.5}, "llm_parallel", "AI calls at once must be a whole number from 1 to 10."),
    ({"llm_timeout_s": 5}, "llm_timeout_s", "The AI's time limit must be from 30 to 1800 seconds."),
    ({"theme": "blue"}, "theme", CHOICE),
    ({"caption_preset": None}, "caption_preset", CHOICE),
    ({"sfx": "loud"}, "sfx", SWITCH),
    ({"asr_language": "French"}, "asr_language", "The speech language must be Auto or a code like en or es."),
    (
        {"openai_compat_base_url": "ftp://10.0.0.5/v1"},
        "openai_compat_base_url",
        "The local AI server's address must start with http:// or https://.",
    ),
    ({"own_channels": ["@me"] * 51}, "own_channels", "List up to 50 of your channels, like @yourname."),
    ({"own_channels": ["  "]}, "own_channels", "List up to 50 of your channels, like @yourname."),
    ({"full_download_max_gb": -1}, "full_download_max_gb", "The whole-video limit must be from 0 to 100 GB."),
    ({"min_free_gb": -0.5}, "min_free_gb", "The free space to keep must be 0 GB or more."),
    ({"max_source_hours": 30}, "max_source_hours", "The longest video must be from 0.1 to 24 hours."),
    (
        {"delete_sources_after_days": 400},
        "delete_sources_after_days",
        "Keep source videos for 0 to 365 days (0 keeps them for good).",
    ),
    ({"colour": "red"}, "colour", "ClipForge has no setting called that."),
]


@pytest.mark.parametrize("changes, field, sentence", BAD_SETTINGS, ids=[b[1] for b in BAD_SETTINGS])
def test_settings_put_validates_with_sentences(engine, changes, field, sentence):
    client = api_client(engine)
    before, stored = engine.settings, repo.load_settings(engine.db())
    r = client.put("/api/settings", json=changes)
    assert problem(r) == (422, field, sentence) and r.json()["error"] == "invalid_setting"
    assert engine.settings is before and engine.runner.settings is before
    assert repo.load_settings(engine.db()) == stored  # nothing of a refused change is kept


def test_settings_put_model_override_needs_every_pass(engine):
    client = api_client(engine)
    for models in (
        {"claude_cli": {"discover": "haiku", "rerank": ""}},  # rerank left with no model
        {"gemini": {"*": "  "}},
        {"openai_compat": {"*": "qwen3:14b", "discover": ""}, "anthropic": {"review": ""}},
    ):
        assert problem(put(client, llm_models=models)) == (422, "llm_models", EVERY_PASS), models
    for models, sentence in (
        (
            {"claude_cli": {"discover": "--dangerously-skip-permissions"}},
            "A model's name starts with a letter or number and has only letters, numbers"
            " and . _ : / @ + - in it.",
        ),
        (
            {"claude_cli": {"summary": "haiku"}},
            "Models can be set for discover, rerank, package and review, or * for all.",
        ),
        (
            {"heuristic": {"*": "haiku"}},
            "Models can be set for Claude Code, the Anthropic API, Gemini and a local server.",
        ),
    ):
        assert problem(put(client, llm_models=models)) == (422, "llm_models", sentence), models
    assert engine.settings.llm_models == {}
    r = put(
        client,
        llm_models={"claude_cli": {"*": " sonnet ", "discover": ""}, "gemini": {"discover": "gemini-pro"}},
    )
    assert r.status_code == 200
    s = engine.settings
    assert [model_for(s, "claude_cli", p) for p in PASSES] == ["sonnet"] * 4
    assert (model_for(s, "gemini", "discover"), model_for(s, "gemini", "review")) == (
        "gemini-pro",
        "gemini-flash-latest",
    )


def test_settings_put_swaps_engine_and_runner_settings(engine, monkeypatch):
    parallel = []
    monkeypatch.setattr(app_module, "set_parallel", parallel.append)
    client = api_client(engine)
    before = engine.settings
    r = put(
        client, llm_parallel=5, caption_preset="beast", accent_color="#ff3b30", asr_language=" FR ", sfx=False
    )
    assert r.status_code == 200
    values = r.json()["values"]
    assert (values["llm_parallel"], values["accent_color"], values["asr_language"]) == (5, "#ff3b30", "fr")
    assert engine.settings is engine.runner.settings  # the jobs that start from now on get the new ones
    assert (engine.settings.llm_parallel, engine.settings.caption_preset, engine.settings.sfx) == (
        5,
        "beast",
        False,
    )
    assert (before.llm_parallel, before.caption_preset, before.sfx) == (
        3,
        "bold_pop",
        True,
    )  # never changed in place
    assert parallel == [5]
    stored = repo.load_settings(engine.db())
    assert (stored.llm_parallel, stored.caption_preset, stored.asr_language) == (5, "beast", "fr")
    assert client.get("/api/settings").json()["values"] == values


def test_settings_put_keeps_the_pause_as_it_is(engine):
    """Only Pause posting and Resume change the kill switch: a Posting section save sends the whole posting
    object, and a stale copy of it must never turn posting back on (or off)."""
    client = api_client(engine)
    assert client.post("/api/posting/pause").json() == {"paused": True}
    posting = client.get("/api/settings").json()["values"]["posting"]
    r = put(client, posting={**posting, "paused": False, "tiktok_daily_cap": 3})
    assert r.status_code == 200
    assert r.json()["values"]["posting"]["paused"] is True
    assert (engine.settings.posting.paused, engine.settings.posting.tiktok_daily_cap) == (True, 3)
    assert repo.load_settings(engine.db()).posting.paused is True

    assert client.post("/api/posting/resume").json() == {"paused": False}
    r = put(client, posting={**posting, "paused": True, "instagram_daily_cap": 2})
    assert r.status_code == 200
    assert (engine.settings.posting.paused, engine.settings.posting.instagram_daily_cap) == (False, 2)


def test_job_stages_read_the_settings_their_job_started_with():
    """A running job keeps the settings it started with: every stage reads ``ctx.settings``, which the
    runner hands each job when it starts, never the engine's, which a change in Settings replaces."""
    folder = Path(stages.__file__).parent
    assert [p.name for p in sorted(folder.glob("*.py")) if "engine.settings" in p.read_text()] == []


def test_create_options_are_validated_and_remembered(engine):
    client = api_client(engine)
    r = put(client, create_options={"length": "short", "clip_count": 5, "layout": "gameplay"})
    assert r.status_code == 200
    wanted = ProjectOptions(length="short", clip_count=5, layout="gameplay")
    assert r.json()["values"]["create_options"] == wanted.model_dump(mode="json")
    for options, field, sentence in (
        ({"length": "tiny"}, "create_options.length", CHOICE),
        ({"render": "maybe"}, "create_options.render", SWITCH),
        ({"colour": "red"}, "create_options.colour", "The Create page has no option called that."),
        ("short", "create_options", "The Create page's options must be a set of choices."),
    ):
        assert problem(put(client, create_options=options)) == (422, field, sentence), options
    assert engine.settings.create_options == wanted
    engine.close()
    again = fake_engine()  # the next start of ClipForge
    try:
        assert again.settings.create_options == wanted
    finally:
        again.close()


def test_asr_language_setting_overrides_the_detected_language():
    """YouTube says the video is in English; Settings says French, so the speech engine is asked for
    French and YouTube's English captions are not used to fill its gaps."""
    engine = fake_engine(speech=FakeSpeech(lines(36)), asr_language="fr", auto_render="none")
    try:
        (p,), _ = engine.add_sources(URL, ProjectOptions(render=False))
        engine.start(p.id)
        assert engine.wait(timeout_s=60, projects=[p.id])
        project = engine.project(p.id)
        tr = Transcript.model_validate_json(engine.paths.project(p.id).transcript_json.read_text())
        assert (project.status, project.language, tr.language) == ("ready", "fr", "fr")
        assert tr.engine != "youtube-captions"
    finally:
        engine.close()


# Library


def add_item(client, data: bytes, file_name: str = "run.mp4", **item):
    fields = {"kind": "gameplay", "credit": "Me", "license": "My own footage"} | item
    headers = {
        "X-File-Name": quote(file_name),
        "X-Item": quote(json.dumps(fields)),
        "Content-Type": "application/octet-stream",
    }
    return client.post("/api/library", content=data, headers=headers)


def uploads_left(engine) -> list:
    folder = engine.paths.uploads
    return sorted(folder.iterdir()) if folder.is_dir() else []


def test_library_upload_list_and_delete(engine, tmp_path):
    data = make_test_video(tmp_path / "run.mp4", seconds=2.0).read_bytes()
    client = api_client(engine)
    r = add_item(
        client,
        data,
        "Parkour run (60fps).mp4",
        name="Parkour",
        credit="Zoë Plays",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        notes="Minecraft parkour, no commentary",
    )
    assert r.status_code == 201
    got = r.json()
    assert {k: got[k] for k in ("kind", "name", "credit", "license", "license_url", "notes", "uses")} == {
        "kind": "gameplay",
        "name": "Parkour",
        "credit": "Zoë Plays",
        "license": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "notes": "Minecraft parkour, no commentary",
        "uses": 0,
    }
    assert (got["width"], got["height"]) == (640, 360) and got["duration_s"] == pytest.approx(2.0, abs=0.1)
    assert got["size_bytes"] == len(data)
    item = repo.get_library_item(engine.db(), got["id"])
    kept = Path(item.path)
    assert kept.parent == engine.paths.gameplay and kept.read_bytes() == data
    assert got["url"] == f"/media/library/gameplay/{kept.name}" and client.get(got["url"]).content == data
    assert uploads_left(engine) == []  # moved into the library, not copied
    plain = add_item(client, data, "Subway run.mp4").json()
    assert (
        plain["name"] == "Subway run" and plain["license_url"] is None
    )  # the file's name when none is given
    listed = client.get("/api/library", params={"kind": "gameplay"}).json()["items"]
    assert [i["id"] for i in listed] == [got["id"], plain["id"]]  # oldest first
    assert client.delete(f"/api/library/{got['id']}").status_code == 204
    assert [i["id"] for i in client.get("/api/library").json()["items"]] == [plain["id"]]
    assert not kept.exists()


def test_library_upload_needs_credit_and_licence(engine, tmp_path):
    data = make_test_video(tmp_path / "run.mp4").read_bytes()
    client = api_client(engine)
    for item, field, sentence in (
        ({"credit": "  "}, "credit", "Say who made this video, so clips can credit them."),
        (
            {"license": ""},
            "license",
            "Say which licence lets you use this video, like CC BY 4.0 or your own footage.",
        ),
        (
            {"license_url": "javascript:alert(1)"},
            "license_url",
            "The licence link must start with http:// or https://.",
        ),
        ({"kind": "sfx"}, "kind", "The library takes gameplay videos and music."),
        ({"credit": "x" * 201}, "credit", "Keep the credit to 200 characters."),
    ):
        assert problem(add_item(client, data, **item)) == (422, field, sentence), item
    no_item = client.post("/api/library", content=data, headers={"X-File-Name": "run.mp4"})
    assert problem(no_item)[:2] == (422, "credit")
    garbled = client.post(
        "/api/library", content=data, headers={"X-File-Name": "run.mp4", "X-Item": "{credit"}
    )
    assert problem(garbled) == (422, "item", "ClipForge couldn't read that request.")
    assert uploads_left(engine) == [] and repo.list_library(engine.db()) == []


def test_library_upload_rejects_a_non_video(engine):
    r = add_item(api_client(engine), b"not a video at all " * 200, "game.mp4")
    assert (r.status_code, r.json()["error"], r.json()["message"]) == (422, "not_video", NOT_READABLE)
    assert uploads_left(engine) == [] and repo.list_library(engine.db()) == []
    assert not engine.paths.gameplay.is_dir() or list(engine.paths.gameplay.iterdir()) == []


def test_deleting_an_item_removes_its_file_and_uses(engine, tmp_path):
    original = make_test_video(tmp_path / "run.mp4", seconds=4.0)
    item = gameplay.add_gameplay(engine.db(), engine.paths, original, credit="Me", license="Mine")
    pid = project(engine)
    (cid,) = add_clips(engine, pid)
    repo.update_clip(engine.db(), cid, edits={"layout": "gameplay", "gameplay_item": item.id})
    repo.record_gameplay_use(engine.db(), item.id, 0.0, 3.0, clip_id=cid)
    client = api_client(engine)
    assert [i["uses"] for i in client.get("/api/library").json()["items"]] == [1]
    assert client.delete(f"/api/library/{item.id}").status_code == 204
    assert not Path(item.path).exists() and original.exists()
    assert repo.get_library_item(engine.db(), item.id) is None
    assert repo.gameplay_uses(engine.db(), item.id) == [] and repo.gameplay_use_of(engine.db(), cid) is None
    assert (
        repo.get_clip(engine.db(), cid).edits["gameplay_item"] == item.id
    )  # it falls back at its next render
    gone = client.delete(f"/api/library/{item.id}")
    assert (gone.status_code, gone.json()["message"]) == (404, "That isn't in the library.")


def test_a_clip_counts_once_however_many_versions_and_posts_show_the_gameplay(engine, tmp_path):
    """A clip holds a use, one more for each account's version and one for each post: the Library says
    how many clips, so "Used in 1 clip" for all of them."""
    item = gameplay.add_gameplay(
        engine.db(),
        engine.paths,
        make_test_video(tmp_path / "run.mp4", seconds=4.0),
        credit="Me",
        license="Mine",
    )
    pid = project(engine)
    first, second = add_clips(engine, pid, 2)
    repo.record_gameplay_use(engine.db(), item.id, 0.0, 3.0, clip_id=first)
    repo.record_gameplay_use(engine.db(), item.id, 0.0, 3.0, clip_id=first, account_id="a1")
    repo.record_gameplay_use(engine.db(), item.id, 0.0, 3.0, clip_id=first, account_id="a1", post_id="p1")
    repo.record_gameplay_use(engine.db(), item.id, 0.0, 3.0, clip_id=first, account_id="a2", post_id="p2")
    client = api_client(engine)
    assert [i["uses"] for i in client.get("/api/library").json()["items"]] == [1]
    repo.record_gameplay_use(engine.db(), item.id, 1.0, 4.0, clip_id=second)
    assert [i["uses"] for i in client.get("/api/library").json()["items"]] == [2]


NO_SOUND = "That file has no sound ClipForge can read. Pick an audio file such as MP3, M4A or WAV."
NOT_MUSIC = "That isn't a track in your music library."


def add_track(client, data: bytes, file_name: str = "night drive.m4a", **item):
    fields = {"kind": "music", "title": "Night Drive", "license": "Bought from Artlist for social use"} | item
    headers = {
        "X-File-Name": quote(file_name),
        "X-Item": quote(json.dumps(fields)),
        "Content-Type": "application/octet-stream",
    }
    return client.post("/api/library", content=data, headers=headers)


def test_music_lists_its_uses_and_deletes(engine, tmp_path):
    data = make_test_audio(tmp_path / "track.m4a", seconds=2.0).read_bytes()
    client = api_client(engine)
    r = add_track(
        client,
        data,
        artist="Kai Lune",
        source_url="https://artlist.io/song/1",
        credit_required=True,
        moods=["hype", "dark"],
        energy=4,
    )
    assert r.status_code == 201
    got = r.json()
    wanted = ("kind", "name", "credit", "source_url", "license", "credit_required", "moods", "energy", "uses")
    assert {k: got[k] for k in wanted} == {
        "kind": "music",
        "name": "Night Drive",
        "credit": "Kai Lune",
        "source_url": "https://artlist.io/song/1",
        "license": "Bought from Artlist for social use",
        "credit_required": True,
        "moods": ["hype", "dark"],
        "energy": 4,
        "uses": 0,
    }
    assert got["duration_s"] == pytest.approx(2.0, abs=0.1) and got["size_bytes"] == len(data)
    kept = Path(repo.get_library_item(engine.db(), got["id"]).path)
    assert kept.parent == engine.paths.music and got["url"] == f"/media/library/music/{kept.name}"
    served = client.get(got["url"])
    assert served.content == data and served.headers["content-type"] == "audio/mp4"
    assert uploads_left(engine) == []
    other = add_track(client, data, "aurora.m4a", title=None).json()
    listed = client.get("/api/library", params={"kind": "music"}).json()["items"]
    assert [i["name"] for i in listed] == ["aurora", "Night Drive"]  # A to Z

    pid = project(engine)
    (cid,) = add_clips(engine, pid)
    repo.update_clip(engine.db(), cid, edits={"music": got["id"]})
    listed = client.get("/api/library", params={"kind": "music"}).json()["items"]
    assert {i["name"]: i["uses"] for i in listed} == {"aurora": 0, "Night Drive": 1}
    patched = client.patch(f"/api/library/{got['id']}", json={"title": "Night Drive (slow)", "energy": 2})
    assert patched.status_code == 200
    assert (patched.json()["name"], patched.json()["energy"], patched.json()["moods"]) == (
        "Night Drive (slow)",
        2,
        ["hype", "dark"],
    )
    for body, field, sentence in (
        ({"license": ""}, "license", "Say what the licence lets you do."),
        ({"moods": "hype"}, "moods", "ClipForge couldn't read that request."),
        ({"credit": "Kai"}, "item", "ClipForge couldn't read that request."),
    ):
        assert problem(client.patch(f"/api/library/{got['id']}", json=body)) == (422, field, sentence), body
    video = add_item(client, make_test_video(tmp_path / "run.mp4").read_bytes()).json()
    assert problem(client.patch(f"/api/library/{video['id']}", json={"energy": 2})) == (404, None, NOT_MUSIC)

    assert client.delete(f"/api/library/{got['id']}").status_code == 204
    assert not kept.exists() and repo.get_library_item(engine.db(), got["id"]) is None
    assert repo.get_clip(engine.db(), cid).edits["music"] == got["id"]  # its render says the track is gone
    assert [i["id"] for i in client.get("/api/library", params={"kind": "music"}).json()["items"]] == [
        other["id"]
    ]


def test_music_upload_refuses_files_without_sound(engine, tmp_path):
    client = api_client(engine)
    silent = make_test_video(tmp_path / "silent.mp4", audio=False).read_bytes()
    track = make_test_audio(tmp_path / "track.m4a", seconds=2.0).read_bytes()
    for data, name, item, wanted in (
        (b"a tone, honest " * 200, "notes.txt", {}, (415, "file", NO_SOUND)),
        (silent, "silent.mp4", {}, (422, "file", NO_SOUND)),
        (b"not music at all " * 200, "track.mp3", {}, (422, "file", NO_SOUND)),
        (track, "track.m4a", {"license": "  "}, (422, "license", "Say what the licence lets you do.")),
        (track, "track.m4a", {"energy": 0}, (422, "energy", "Energy must be a whole number from 1 to 5.")),
        (track, "track.m4a", {"credit": "Kai"}, (422, "item", "ClipForge couldn't read that request.")),
    ):
        assert problem(add_track(client, data, name, **item)) == wanted, name
    assert uploads_left(engine) == [] and repo.list_library(engine.db()) == []
    assert not engine.paths.music.is_dir() or list(engine.paths.music.iterdir()) == []


def test_sfx_wav_is_served(engine, monkeypatch):
    library.wav_of.cache_clear()
    made, real = [], sfx.synth
    monkeypatch.setattr(sfx, "synth", lambda kind, *a, **k: made.append(kind) or real(kind, *a, **k))
    client = api_client(engine)
    sounds = client.get("/api/library/sfx").json()["sounds"]
    assert [s["kind"] for s in sounds] == list(sfx.KINDS)
    assert sounds[0] == {
        "kind": "whoosh",
        "length_s": sfx.LENGTH_S["whoosh"],
        "url": "/api/library/sfx/whoosh.wav",
    }
    r = client.get("/api/library/sfx/whoosh.wav")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(r.content)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 48000)
        pcm = np.frombuffer(w.readframes(w.getnframes()), "<i2")
    assert len(pcm) == len(real("whoosh"))
    assert 0.45 < np.abs(pcm).max() / 32767 < 0.55  # a preview at about -6 dBFS, never at full scale
    assert client.get("/api/library/sfx/whoosh.wav").content == r.content
    assert made == ["whoosh"]  # made once, then kept in memory
    for wrong in ("boom.wav", "whoosh.mp3", "whoosh"):
        assert client.get(f"/api/library/sfx/{wrong}").status_code == 404, wrong


def test_fonts_are_listed_and_served_off_an_allowlist(engine):
    client = api_client(engine)
    fonts = client.get("/api/fonts").json()["fonts"]
    assert [f["file"] for f in fonts] == sorted(p.name for p in FONTS_DIR.glob("*.ttf"))
    black = next(f for f in fonts if f["file"] == "Montserrat-Black.ttf")
    assert black == {
        "file": "Montserrat-Black.ttf",
        "family": "Montserrat",
        "style": "Black",
        "licence": "OFL-Montserrat.txt",
        "url": "/api/fonts/Montserrat-Black.ttf",
        "licence_url": "/api/fonts/OFL-Montserrat.txt",
    }
    bebas = next(f for f in fonts if f["file"] == "BebasNeue-Regular.ttf")
    lucky = next(f for f in fonts if f["file"] == "LuckiestGuy-Regular.ttf")
    assert (bebas["family"], bebas["licence"]) == ("Bebas Neue", "OFL-BebasNeue.txt")
    assert (lucky["family"], lucky["licence"]) == ("Luckiest Guy", "LICENSE-LuckiestGuy.txt")
    r = client.get(black["url"])
    assert (r.status_code, r.headers["content-type"]) == (200, "font/ttf")
    assert r.content == (FONTS_DIR / "Montserrat-Black.ttf").read_bytes()
    licence = client.get(black["licence_url"])
    assert licence.status_code == 200 and licence.headers["content-type"].startswith("text/plain")
    assert "OPEN FONT LICENSE" in licence.text.upper()
    for wrong in ("Missing-Black.ttf", "..%2Fassets.py", "%2E%2E%2F%2E%2E%2Fconfig.py", "fonts", ".DS_Store"):
        assert client.get(f"/api/fonts/{wrong}").status_code == 404, wrong


def test_doctor_lists_checks(engine, monkeypatch):
    asked = []

    def checks(paths, settings):
        asked.append((paths, settings))
        return run_checks(paths, settings, which=lambda name: None, user_home=paths.home)  # nothing installed

    monkeypatch.setattr(system, "run_checks", checks)
    got = api_client(engine).get("/api/doctor").json()["checks"]
    assert asked == [(engine.paths, engine.settings)]
    by_name = {c["name"]: c for c in got}
    assert {"python", "ffmpeg", "yt-dlp", "speech model", "disk"} <= set(by_name)
    assert set(got[0]) == {"name", "ok", "detail", "fix", "required"}
    ffmpeg = by_name["ffmpeg"]
    assert (ffmpeg["ok"], ffmpeg["required"]) == (False, True) and ffmpeg["fix"]


# The yt-dlp update


def checkout(tmp_path: Path, name: str = "clipforge") -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / "pyproject.toml").write_text(f'[project]\nname = "{name}"\n')
    (root / "uv.lock").write_text("version = 1\n")
    return root


class FakeRun:
    """``subprocess.run`` for the update: the checkout's Python prints each of ``versions`` in turn, and
    each uv step exits 0 (the lock step rewriting uv.lock) or as ``outcomes`` says: a code or an
    exception to raise."""

    def __init__(self, versions, outcomes=None):
        self.versions, self.outcomes, self.calls = list(versions), outcomes or {}, []

    def __call__(self, cmd, **kw):
        self.calls.append((list(cmd), kw))
        if "-c" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=f"{self.versions.pop(0)}\n", stderr="")
        outcome = self.outcomes.get(cmd[1], 0)
        if isinstance(outcome, BaseException):
            raise outcome
        if cmd[1] == "lock":
            (Path(kw["cwd"]) / "uv.lock").write_text("version = 1\n# upgraded\n")
        return subprocess.CompletedProcess(
            cmd, outcome, stdout="", stderr="error: no network" if outcome else ""
        )


def test_update_ytdlp_runs_lock_then_sync(tmp_path):
    root = checkout(tmp_path)
    python = root / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.touch()
    run = FakeRun(["2026.10.10", "2026.10.20"])
    got = update_ytdlp(run=run, uv="/opt/uv", root=root)
    assert got == UpdateResult(
        True, "2026.10.10", "2026.10.20", "yt-dlp is now 2026.10.20. Restart ClipForge to use it."
    )
    assert [cmd for cmd, _ in run.calls] == [
        [str(python), "-I", "-c", VERSION],
        ["/opt/uv", "lock", "--upgrade-package", "yt-dlp"],
        ["/opt/uv", "sync", "--frozen", "--inexact"],
        [str(python), "-I", "-c", VERSION],
    ]
    assert all(kw["cwd"] == root and kw["timeout"] == 300 for cmd, kw in run.calls if cmd[0] == "/opt/uv")
    same = update_ytdlp(run=FakeRun(["2026.10.20", "2026.10.20"]), uv="/opt/uv", root=root)
    assert (same.ok, same.message) == (True, "yt-dlp 2026.10.20 is already the newest.")


@pytest.mark.parametrize(
    "outcomes, sentence",
    [
        ({"lock": 1}, "ClipForge couldn't update yt-dlp. Check your internet connection, then try again."),
        ({"sync": 2}, "ClipForge couldn't update yt-dlp. Check your internet connection, then try again."),
        (
            {"sync": subprocess.TimeoutExpired("uv", 300)},
            "Updating yt-dlp took over 5 minutes, so ClipForge stopped it. Try again later.",
        ),
    ],
    ids=["lock fails", "sync fails", "sync too slow"],
)
def test_update_ytdlp_failures_leave_the_lock_file_as_it_was(tmp_path, outcomes, sentence):
    root = checkout(tmp_path)
    before = (root / "uv.lock").read_bytes()
    got = update_ytdlp(run=FakeRun(["2026.10.10"], outcomes), uv="/opt/uv", root=root)
    assert (got.ok, got.before, got.after, got.message) == (False, "2026.10.10", "2026.10.10", sentence)
    assert (root / "uv.lock").read_bytes() == before  # still the lock file of what is installed
    if not isinstance(next(iter(outcomes.values())), BaseException):
        assert "no network" in got.details


def test_update_ytdlp_runs_one_at_a_time(tmp_path):
    root = checkout(tmp_path)
    inside, go = threading.Event(), threading.Event()

    def slow(cmd, **kw):
        if "-c" not in cmd:
            inside.set()
            go.wait(5)
        return subprocess.CompletedProcess(cmd, 0, stdout="2026.10.10\n", stderr="")

    first = threading.Thread(target=update_ytdlp, kwargs={"run": slow, "uv": "/opt/uv", "root": root})
    first.start()
    try:
        assert inside.wait(5)
        second = update_ytdlp(run=FakeRun([]), uv="/opt/uv", root=root)
    finally:
        go.set()
        first.join(5)
    assert (second.ok, second.message) == (False, "yt-dlp is already updating. Wait for that to finish.")


def test_update_ytdlp_outside_a_checkout_says_how(tmp_path, monkeypatch):
    run = FakeRun([])
    got = update_ytdlp(run=run, uv="/opt/uv", root=tmp_path)  # no pyproject.toml or uv.lock here
    assert not got.ok and run.calls == []
    assert got.message == (
        "ClipForge isn't running from its own folder, so update yt-dlp there: run"
        " uv lock --upgrade-package yt-dlp, then uv sync --frozen."
    )
    assert (
        update_ytdlp(run=run, uv="/opt/uv", root=checkout(tmp_path, "something-else")).message == got.message
    )
    assert (
        ytdlp_update.checkout() == Path(ytdlp_update.__file__).resolve().parents[1]
    )  # this one is a checkout
    monkeypatch.setattr(ytdlp_update, "find_uv", lambda: None)
    no_uv = update_ytdlp(run=run, root=checkout(tmp_path))
    assert (no_uv.ok, no_uv.message) == (
        False,
        "Updating yt-dlp needs uv. Install it from docs.astral.sh/uv, then try again.",
    )
    assert run.calls == []


def test_update_ytdlp_refuses_while_downloading(engine):
    client = api_client(engine)
    done = UpdateResult(
        True, "2026.10.10", "2026.10.20", "yt-dlp is now 2026.10.20. Restart ClipForge to use it."
    )
    calls = []
    client.app.state.update_ytdlp = lambda: calls.append(engine.runner.held("io")) or done
    pid = project(engine)
    engine.start(pid)
    (job,) = [j for j in engine.runner.jobs_for(pid) if engine.runner.handlers[j.kind][0] == "io"]
    engine.db().execute("UPDATE jobs SET status = 'running' WHERE id = ?", (job.id,))
    r = client.post("/api/doctor/update-ytdlp")
    assert (r.status_code, r.json()["error"], r.json()["message"]) == (409, "downloading", WAIT)
    assert calls == [] and not engine.runner.held("io")
    engine.db().execute("UPDATE jobs SET status = 'queued' WHERE id = ?", (job.id,))  # waiting is fine
    r = client.post("/api/doctor/update-ytdlp")
    assert r.status_code == 200 and r.json() == dataclasses.asdict(done)
    assert calls == [True] and not engine.runner.held("io")  # no download started while it ran


def test_holding_a_lane_keeps_its_jobs_from_starting(engine):
    started = threading.Event()
    runner = JobRunner(
        engine.paths.db,
        {"t.fetch": ("io", lambda ctx: started.set())},
        paths=engine.paths,
        settings=engine.settings,
        poll_s=0.02,
    )
    runner.start()
    try:
        with runner.hold("io"):
            assert runner.held("io")
            runner.enqueue("t.fetch", "r1")
            time.sleep(0.4)
            assert not started.is_set() and runner.running("io") == []
        assert started.wait(5)
    finally:
        runner.stop()
        runner.close()


def test_cli_update_ytdlp_prints_the_result(monkeypatch, capsys):
    results = [
        UpdateResult(
            True, "2026.10.10", "2026.10.20", "yt-dlp is now 2026.10.20. Restart ClipForge to use it."
        ),
        UpdateResult(
            False,
            "2026.10.10",
            "2026.10.10",
            "ClipForge couldn't update yt-dlp. Check your internet connection, then try again.",
            details="error: no network",
        ),
    ]
    monkeypatch.setattr(cli, "update_ytdlp", lambda: results.pop(0))
    assert cli.main(["update-ytdlp"]) == 0
    assert capsys.readouterr().out == "yt-dlp is now 2026.10.20. Restart ClipForge to use it.\n"
    assert cli.main(["update-ytdlp"]) == 1
    assert capsys.readouterr().err == (
        "ClipForge couldn't update yt-dlp. Check your internet connection, then try again.\n"
        "  error: no network\n"
    )
    paths = Paths.from_env()
    paths.ensure()
    fd = take_home(paths)  # ClipForge is open
    try:
        assert cli.main(["update-ytdlp"]) == 1
        assert capsys.readouterr().err == (
            "ClipForge is open, so update yt-dlp from its Doctor page, or quit ClipForge and try again.\n"
        )
    finally:
        os.close(fd)


# Storage and jobs


def test_storage_routes(engine):
    from tests.unit.test_storage import FREED, settled

    settled(engine)
    client = api_client(engine)
    got = client.get("/api/storage").json()
    assert set(got["usage"]) == {
        "free_bytes",
        "total_bytes",
        "sources",
        "work",
        "renders",
        "library",
        "assets",
        "logs",
        "uploads",
    }
    assert got["cleanable"] == {"projects": 1, "bytes": FREED}
    assert got["delete_sources_after_days"] == engine.settings.delete_sources_after_days
    assert client.post("/api/storage/clean").json() == {"freed_bytes": FREED, "projects": 1}
    assert client.get("/api/storage").json()["cleanable"] == {"projects": 0, "bytes": 0}


LABELS = {
    "ingest.metadata": "Reading the video's details",
    "ingest.audio": "Downloading the audio",
    "ingest.captions": "Getting YouTube's captions",
    "ingest.extras": "Getting comments and chapters",
    "ingest.video": "Downloading the video",
    "asr.transcribe": "Transcribing",
    "signals.compute": "Measuring the video's signals",
    "asis.make": "Making the clip as it is",
    "brain.select": "Finding the best moments",
    "brain.package": "Writing titles and captions",
    "clip.source": "Getting the clip's video",
    "clip.analyze": "Framing the clip",
    "clip.render": "Rendering",
    "clip.variant": "Rendering an account's version",
    "clip.review": "Re-checking the edited clip",
    "post.browser": "Posting in Chrome",
    "post.api": "Posting through Instagram",
    "account.login": "Log in in the Chrome window.",
    "account.selftest": "Checking the account's page",
    "watch.check": "Looking for new uploads",
    "library.starter": "Downloading starter gameplay",
}


def test_jobs_route_labels_in_plain_words(engine):
    from clipforge.server import views

    assert views.JOB_LABELS == LABELS
    assert set(engine.runner.handlers) == set(LABELS)
    pid = project(engine)
    engine.runner.enqueue("ingest.metadata", pid)
    (cid,) = add_clips(engine, pid)
    engine.runner.enqueue("clip.render", cid, {"project": pid})
    client = api_client(engine)
    jobs = {j["kind"]: j for j in client.get("/api/jobs", params={"active": 1}).json()["jobs"]}
    meta, render = jobs["ingest.metadata"], jobs["clip.render"]
    assert set(meta) == {
        "id",
        "kind",
        "label",
        "ref_id",
        "project",
        "project_title",
        "clip_rank",
        "status",
        "progress",
        "message",
        "started_at",
    }
    assert (meta["label"], meta["project"], meta["clip_rank"], meta["status"]) == (
        "Reading the video's details",
        pid,
        None,
        "queued",
    )
    assert (render["label"], render["project"], render["ref_id"], render["clip_rank"]) == (
        "Rendering",
        pid,
        cid,
        1,
    )
    with engine.db() as conn:
        conn.execute("UPDATE jobs SET status = 'failed' WHERE kind = 'clip.render'")
    active = {j["kind"] for j in client.get("/api/jobs", params={"active": 1}).json()["jobs"]}
    assert "clip.render" not in active and "ingest.metadata" in active
    assert "clip.render" in {j["kind"] for j in client.get("/api/jobs").json()["jobs"]}


# The background service


def test_service_routes(engine, monkeypatch):
    calls = []

    def run(args):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(service, "RUN", run)
    client = api_client(engine)
    got = client.get("/api/service").json()
    assert got == {"installed": False, "running": False, "pid": None, "sentence": service.OFF}
    got = client.post("/api/service/install").json()
    assert got["installed"] and got["message"] == service.INSTALLED
    assert [c[1] for c in calls] == ["bootout", "bootstrap", "print"]
    assert service.plist_path(service.AGENTS).is_file()
    got = client.post("/api/service/uninstall").json()
    assert not got["installed"] and got["message"] == service.REMOVED


def test_service_route_says_why_it_failed(engine, monkeypatch):
    def missing(args):
        raise FileNotFoundError(args[0])

    monkeypatch.setattr(service, "RUN", missing)
    response = api_client(engine).post("/api/service/install")
    assert response.status_code == 409
    assert response.json()["message"] == service.NO_LAUNCHCTL
