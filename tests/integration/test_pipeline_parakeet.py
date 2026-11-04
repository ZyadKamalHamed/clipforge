"""The whole pipeline on real speech with the real Parakeet model, in Basic mode. Slow, and skipped
without the model: it comes from CLIPFORGE_TEST_HF_HOME, else from the model cache in the real ClipForge
home, where it is only read. The speech is macOS ``say`` reading a short story."""

import os
import shutil
from pathlib import Path

import pytest

from clipforge.app import Engine
from clipforge.asr.parakeet import ParakeetEngine
from clipforge.models import ProjectOptions, Transcript
from clipforge.paths import Paths
from tests.fakes import FakeSpeech, FakeYouTube
from tests.helpers import make_speech_video

pytestmark = pytest.mark.slow

STORY = [
    "Every year I promise myself that this time I will start the big project early and finish it calmly.",
    "Then the first week goes by and I tell myself there is still plenty of time before the deadline.",
    "By the second week the deadline feels far away, so I watch videos and clean my desk instead.",
    "Three days before it is due, the panic finally arrives and I work through the night without a break.",
    "I always hand it in on time, but I am exhausted and I know the work could have been much better.",
    "So this year I am trying something new, twenty minutes on the project every morning before breakfast.",
]


def cached_parakeet() -> ParakeetEngine:
    folder = os.environ.get("CLIPFORGE_TEST_HF_HOME")
    return ParakeetEngine(Path(folder) if folder else Paths(Path.home() / "ClipForge").hf_home)


def test_pipeline_real_parakeet(tmp_path):
    parakeet = cached_parakeet()
    if not parakeet.available() or not parakeet.model_ready() or shutil.which("say") is None:
        pytest.skip("needs parakeet-mlx, its cached model and macOS say")
    video = make_speech_video(tmp_path / "media" / "Deadline story.mp4", " ".join(STORY))
    engine = Engine.open(
        settings_overrides={"min_free_gb": 0.0},
        youtube=FakeYouTube(),
        asr_engines={"parakeet": parakeet, "whisper": FakeSpeech([], name="whisper", ok=False)},
        provider_factory=lambda settings, name: None,
    )
    try:
        (project,), _ = engine.add_sources(str(video), ProjectOptions(render=False))
        engine.start(project.id)
        assert engine.wait(timeout_s=600, projects=[project.id])
        project = engine.project(project.id)
        assert (project.status, project.mode, project.message) == ("ready", "basic", None)
        tr = Transcript.model_validate_json(engine.paths.project(project.id).transcript_json.read_text())
        assert tr.engine == "parakeet" and 4 <= len(tr.sentences) <= 8
        assert "deadline" in " ".join(s.text for s in tr.sentences).lower()
        (clip,) = [c for c in engine.clips(project.id) if c.status == "packaged"]
        # the auto range, and the 10 s more a clip may run to keep a thought whole
        assert clip.packaging["hook_title"] and 15.0 <= clip.end_s - clip.start_s <= 85.0
    finally:
        engine.close()
