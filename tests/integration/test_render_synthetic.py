"""The whole pipeline from a local file to rendered clips: ``clipforge run FILE --provider heuristic`` in a
tmp home, with the real Parakeet model and the real renderer. Slow, and skipped without parakeet-mlx, its
cached model and macOS ``say``. The source is a dark frame with a block moving across it, which is not a
face, so the layout fills the frame without a speaker; YuNet looks for faces when its model is in the real
ClipForge home's cache, read in place, and a stand-in that finds none does otherwise. Nothing is fetched."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from clipforge import assets, cli, repo
from clipforge.app import Engine
from clipforge.db import connect
from clipforge.media.ffmpeg import ffmpeg_bin, ffprobe_bin, probe
from clipforge.paths import Paths
from clipforge.render.edl import EDL
from clipforge.render.qa import QAResult
from clipforge.vision.faces import FaceDetector
from tests.fakes import FakeSpeech, FakeYouTube, NoFaces, offline
from tests.helpers import make_block_talk_video
from tests.integration.test_pipeline_parakeet import cached_parakeet

pytestmark = pytest.mark.slow

TALK = [
    "Last winter I decided to fix my sleep, because I was tired every single afternoon at work.",
    "The first thing I tried was going to bed at ten, and that lasted exactly three nights.",
    "Then a friend told me the real problem was my phone, not the time I went to bed.",
    "So I bought a cheap alarm clock and left my phone charging in the kitchen overnight.",
    "The first week was strange, because I kept reaching for a phone that was not there.",
    "By the second week I was reading a few pages of a book and falling asleep fast.",
    "I also stopped drinking coffee after lunch, which was much harder than I expected.",
    "My afternoons changed completely, and I stopped needing a snack just to stay awake.",
    "The biggest surprise was how much more patient I felt with the people around me.",
    "Small problems that used to ruin my whole day suddenly felt easy to handle.",
    "I started waking up before my alarm, which had never happened to me in my life.",
    "Now my mornings are the best part of the day instead of the worst part.",
    "My partner noticed the change before I did, and asked what I had been doing differently.",
    "When I explained the phone rule, they laughed and said it sounded far too simple to work.",
    "A month later their phone left the bedroom too, and now we both sleep much better.",
    "The simple changes are usually the ones we ignore, because they never feel important enough.",
    "If you only change one thing this year, move your phone out of the bedroom.",
    "It costs nothing, it takes one minute, and it might change how every day feels.",
    "Try it for two weeks and pay attention to how you feel in the afternoon.",
    "I promise you will notice the difference long before the two weeks are over.",
]
TOP_THIRD = 640  # the hook's box sits above this
CAPTION_Y = 1150  # where a full-frame layout centres its captions
NEAR_Y = 30  # the line's ink sits a few px below its centre


def cached_faces():
    """YuNet read in place from the real ClipForge home's model cache, else a stand-in that finds no face."""
    home = Paths(Path.home() / "ClipForge")
    if assets.model_ready(home):
        return FaceDetector(home.models / assets.MODELS["yunet"].file)
    return NoFaces()


def streams(video: Path) -> dict[str, dict]:
    """ffprobe's video and audio stream of ``video``, by type."""
    out = subprocess.run(
        [ffprobe_bin(), "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height"]
        + ["-of", "json", str(video)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return {s["codec_type"]: s for s in json.loads(out)["streams"]}


def integrated_lufs(video: Path) -> float:
    """The integrated loudness of ``video``'s sound, as ffmpeg's EBU R128 meter reads it."""
    err = subprocess.run(
        [ffmpeg_bin(), "-hide_banner", "-nostats", "-i", str(video), "-map", "0:a", "-af", "ebur128"]
        + ["-f", "null", "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stderr
    return float(re.findall(r"I:\s+(-?[\d.]+) LUFS", err)[-1])


def frame_at(video: Path, t: float) -> np.ndarray:
    """The 1080x1920 RGB frame showing at ``t`` seconds."""
    out = subprocess.run(
        [ffmpeg_bin(), "-v", "error", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1"]
        + ["-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True,
        capture_output=True,
    ).stdout
    return np.frombuffer(out, np.uint8).reshape(1920, 1080, 3)


def white(pixels: np.ndarray) -> np.ndarray:
    return (pixels >= 200).all(axis=-1)


def caption_coloured(pixels: np.ndarray) -> np.ndarray:
    """White, as the words are, or the yellow of the word being said."""
    r, g, b = (pixels[..., i].astype(int) for i in range(3))
    return white(pixels) | ((r >= 200) & (g >= 160) & (b <= 100))


def check_video(video: Path) -> None:
    folder = video.parent
    qa = QAResult.model_validate_json((folder / "qa.json").read_text())
    assert qa.passed, qa.problems()
    assert next(c for c in qa.checks if c.name == "safe_zone").ok, qa.problems()
    edl = EDL.model_validate_json((folder / "edl.json").read_text())
    got = streams(video)
    assert (got["video"]["codec_name"], got["video"]["width"], got["video"]["height"]) == ("h264", 1080, 1920)
    assert got["audio"]["codec_name"] == "aac"
    assert integrated_lufs(video) == pytest.approx(-14.0, abs=1.5)

    x, y, w, h = (int(v) for v in edl.hook["box"])
    assert y + h / 2 < TOP_THIRD
    box = frame_at(video, 0.0)[y : y + h, x : x + w]
    assert white(box).mean() > 0.5  # the hook's white box, from the first frame

    spoken = [p for p in edl.pages if p.end - p.start >= 0.4]
    assert len(spoken) >= 4
    for page in spoken[:: len(spoken) // 4][:4]:
        band = caption_coloured(frame_at(video, (page.start + page.end) / 2)[900:1500])
        rows = np.nonzero(band)[0] + 900
        assert len(rows) > 300, page.words
        assert abs(rows.mean() - CAPTION_Y) < NEAR_Y, page.words


def test_pipeline_renders_a_synthetic_source(tmp_path, monkeypatch, capsys):
    parakeet = cached_parakeet()
    if not parakeet.available() or not parakeet.model_ready() or shutil.which("say") is None:
        pytest.skip("needs parakeet-mlx, its cached model and macOS say")
    video = make_block_talk_video(tmp_path / "media" / "Sleep talk.mp4", " ".join(TALK))
    assert 80.0 <= probe(video).duration_s <= 110.0

    def opened(*, verbose=False):
        engine = Engine.open(
            settings_overrides={"min_free_gb": 0.0},
            youtube=FakeYouTube(),
            asr_engines={"parakeet": parakeet, "whisper": FakeSpeech([], name="whisper", ok=False)},
            provider_factory=lambda settings, name: None,
            verbose=verbose,
        )
        engine.face_detector = cached_faces()
        engine.emoji_fetch = offline
        return engine

    monkeypatch.setattr(cli, "open_engine", opened)
    code = cli.main(["run", str(video), "--provider", "heuristic"])
    out = capsys.readouterr().out
    assert code == 0 and "Videos:" in out, out

    conn = connect(Paths.from_env().db)
    try:
        (project,) = repo.list_projects(conn)
        clips = repo.list_clips(conn, project.id)
        videos = {c.id: repo.get_render(conn, c.current_render_id) for c in clips if c.current_render_id}
    finally:
        conn.close()
    assert project.mode == "basic"
    assert videos and all(c.status == "rendered" for c in clips if c.passes_gates), out
    for clip in clips:
        if clip.id in videos:
            assert clip.layout in ("fit", "motion"), clip.layout_reason
            assert videos[clip.id].path in out
            check_video(Path(videos[clip.id].path))
