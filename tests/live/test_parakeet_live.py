"""The real Parakeet model on real speech. Run on purpose with:
uv run pytest -m live tests/live/test_parakeet_live.py -q

The model comes from CLIPFORGE_TEST_HF_HOME, else from the model cache in the real ClipForge home,
where the app keeps it too (fetched there once, about 2.5 GB, when it isn't there yet). The speech is
CLIPFORGE_TEST_AUDIO, else the TED talk's audio, downloaded for the run."""

import os
from pathlib import Path

import pytest

from clipforge.asr.parakeet import ParakeetEngine
from clipforge.asr.segment import build_transcript
from clipforge.ingest.ytdlp import YtDlpClient
from clipforge.media import ffmpeg
from clipforge.paths import Paths

pytestmark = pytest.mark.live

TED = "https://www.youtube.com/watch?v=arj7oStGLkU"


@pytest.fixture(scope="module")
def engine() -> ParakeetEngine:
    folder = os.environ.get("CLIPFORGE_TEST_HF_HOME")
    return ParakeetEngine(Path(folder) if folder else Paths(Path.home() / "ClipForge").hf_home)


@pytest.fixture(scope="module")
def minute(tmp_path_factory) -> Path:
    """A minute of the talk, from 30 s in, as 16 kHz mono WAV."""
    folder = tmp_path_factory.mktemp("speech")
    source = os.environ.get("CLIPFORGE_TEST_AUDIO") or YtDlpClient().download_audio(TED, folder)
    wav = folder / "minute.wav"
    ffmpeg.run(
        [
            ffmpeg.ffmpeg_bin(),
            *ffmpeg.QUIET,
            "-ss",
            "30",
            "-t",
            "60",
            "-i",
            str(source),
            *ffmpeg.SPEECH_WAV,
            str(wav),
        ]
    )
    return wav


def test_parakeet_transcribes_one_minute(engine, minute):
    result = engine.transcribe(minute, language="en")
    starts = [w.start for w in result.words]
    assert len(result.words) >= 100 and starts == sorted(starts)
    assert any(w.text.endswith((".", "?", "!")) for w in result.words)
    assert "parakeet-mlx 0.5.3" in result.engine_version
    assert len(build_transcript(result, 60.0).sentences) >= 3


def test_parakeet_windows_with_beam_search(engine, minute):
    windows = [(5.0, 20.0), (35.0, 50.0)]
    results = engine.transcribe_many(minute, windows, language="en", beam=True)
    assert len(results) == 2
    for result, (start, end) in zip(results, windows, strict=True):
        assert len(result.words) >= 20
        assert all(start - 0.5 <= w.start and w.end <= end + 0.5 for w in result.words)
