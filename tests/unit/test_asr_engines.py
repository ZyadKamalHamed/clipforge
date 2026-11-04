import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import huggingface_hub
import pytest

from clipforge import children
from clipforge.asr import base, whisper_mlx, worker
from clipforge.asr.base import (
    LANGUAGE_WARNING,
    SETTING_WARNING,
    ASRResult,
    RawWord,
    choose_engine,
    worker_env,
    worker_timeout_s,
)
from clipforge.asr.parakeet import ParakeetEngine, decoding, words_from_tokens
from clipforge.asr.whisper_mlx import WhisperEngine
from clipforge.asr.youtube_captions import transcript_from_captions
from clipforge.errors import ToolError, ToolMissing
from clipforge.ingest.youtube_signals import CapWord
from clipforge.media.audio import wav_seconds
from clipforge.media.ffmpeg import ffmpeg_bin
from tests.helpers import make_wav

PARAKEET_REVISION = "ed2b7e8c15f9aaa0b5772e2efb986255eaef7e15"
HEARD = [{"text": "hi", "start": 0.0, "end": 0.5, "conf": 0.9}]


def seed(engine) -> None:
    """Put the engine's model files where the Hugging Face cache keeps them, so nothing downloads."""
    engine.model_dir().mkdir(parents=True, exist_ok=True)
    for name in engine.files:
        (engine.model_dir() / name).write_text("x")


class FakeWorker:
    """Stands in for subprocess.run: records each call and writes the result file the worker would."""

    def __init__(self, *, returncode=0, stderr="", timeout=False, write=True, on_prepare=None, results=None):
        self.returncode, self.stderr, self.timeout, self.write = returncode, stderr, timeout, write
        self.on_prepare, self.results, self.calls = on_prepare, results, []

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        if "--prepare" in cmd and self.on_prepare:
            self.on_prepare()
        if self.timeout:
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))
        if "--prepare" not in cmd and self.returncode == 0 and self.write:
            results = self.results or [HEARD] * max(cmd.count("--start"), 1)
            data = {"engine_version": "parakeet-mlx 0.5.3, test", "language": None, "results": results}
            Path(cmd[cmd.index("--out") + 1]).write_text(json.dumps(data))
        return subprocess.CompletedProcess(cmd, self.returncode, stdout="", stderr=self.stderr)


def after(cmd: list[str], flag: str) -> str:
    return cmd[cmd.index(flag) + 1]


@pytest.fixture
def ready(tmp_path) -> ParakeetEngine:
    engine = ParakeetEngine(tmp_path / "hf")
    seed(engine)
    return engine


@pytest.fixture
def short_wav(tmp_path) -> Path:
    return make_wav(tmp_path / "talk.wav", [(2, 0.1)])


# Choosing an engine


def test_choose_engine():
    both = {"parakeet": True, "whisper": True}
    parakeet_only = {"parakeet": True, "whisper": False}
    for language in ("en", "en-US", None, "de"):
        assert choose_engine(language, "auto", parakeet_only, False) == ("parakeet", None)
    assert choose_engine("ar", "auto", both, False) == ("whisper", None)
    assert choose_engine("ar", "auto", parakeet_only, True) == ("captions", LANGUAGE_WARNING)
    assert choose_engine("ar", "auto", parakeet_only, False) == ("parakeet", LANGUAGE_WARNING)
    assert LANGUAGE_WARNING == (
        "This video's language isn't one the default speech model knows. "
        "Install the Whisper add-on for better transcripts."
    )


def test_choose_engine_follows_an_available_setting():
    both = {"parakeet": True, "whisper": True}
    assert choose_engine("en", "whisper", both, False) == ("whisper", None)
    assert choose_engine("ar", "parakeet", both, False) == ("parakeet", None)
    assert choose_engine("en", "captions", both, True) == ("captions", None)


def test_choose_engine_picks_itself_when_the_setting_is_unavailable():
    parakeet_only = {"parakeet": True, "whisper": False}
    assert choose_engine("en", "whisper", parakeet_only, False) == ("parakeet", SETTING_WARNING)
    assert choose_engine("en", "captions", parakeet_only, False) == ("parakeet", SETTING_WARNING)
    captions_warning = f"{SETTING_WARNING} {LANGUAGE_WARNING}"
    assert choose_engine("ar", "whisper", parakeet_only, True) == ("captions", captions_warning)
    engine, warning = choose_engine("ar", "whisper", parakeet_only, False)
    assert engine == "parakeet" and warning == f"{SETTING_WARNING} {LANGUAGE_WARNING}"


# Running the worker


def test_worker_command_and_env(ready, short_wav, monkeypatch):
    fake = FakeWorker()
    monkeypatch.setattr(children, "run", fake)
    result = ready.transcribe(short_wav)
    [(cmd, kwargs)] = fake.calls  # the model is already here, so nothing is fetched first
    assert cmd[:4] == [sys.executable, "-I", "-m", "clipforge.asr.worker"]
    assert after(cmd, "--engine") == "parakeet"
    assert after(cmd, "--model-id") == "mlx-community/parakeet-tdt-0.6b-v3"
    assert after(cmd, "--revision") == PARAKEET_REVISION
    assert after(cmd, "--wav") == str(short_wav.resolve())
    assert "--beam" not in cmd and "--start" not in cmd and "--language" not in cmd
    assert kwargs["env"]["HF_HOME"] == str(ready.hf_home)
    assert kwargs["env"]["HF_HUB_DISABLE_TELEMETRY"] == "1"
    assert kwargs["stdin"] == subprocess.DEVNULL and kwargs["cwd"] != os.getcwd()
    assert result == ASRResult(
        engine="parakeet",
        engine_version="parakeet-mlx 0.5.3, test",
        language=None,
        words=[RawWord(text="hi", start=0.0, end=0.5, conf=0.9)],
    )


def test_worker_timeout_scales_with_audio(ready, tmp_path, monkeypatch):
    fake = FakeWorker()
    monkeypatch.setattr(children, "run", fake)
    ten_minutes = make_wav(tmp_path / "long.wav", [(600, 0)])
    ready.transcribe(ten_minutes)
    assert fake.calls[0][1]["timeout"] == 420
    assert worker_timeout_s(0) == 120
    ready.transcribe(ten_minutes, timeout_s=5)
    assert fake.calls[1][1]["timeout"] == 5


def test_window_language_and_beam_reach_the_worker(ready, short_wav, monkeypatch):
    fake = FakeWorker()
    monkeypatch.setattr(children, "run", fake)
    result = ready.transcribe(short_wav, language="en-US", window=(100, 160), beam=True)
    [(cmd, kwargs)] = fake.calls
    assert (after(cmd, "--start"), after(cmd, "--end")) == ("100.0", "160.0")
    assert "--beam" in cmd and after(cmd, "--language") == "en"
    assert kwargs["timeout"] == 150  # 120 s plus half of the 60 s window
    assert result.language == "en"


def test_transcribe_many_sends_every_window_in_one_call(ready, short_wav, monkeypatch):
    fake = FakeWorker()
    monkeypatch.setattr(children, "run", fake)
    results = ready.transcribe_many(short_wav, [(1.0, 3.0), (10.0, 14.0)], beam=True)
    [(cmd, kwargs)] = fake.calls
    assert cmd.count("--start") == 2 and cmd[cmd.index("--start") :].count("--end") == 2
    assert kwargs["timeout"] == 123
    assert len(results) == 2 and all(r.words[0].text == "hi" for r in results)
    assert ready.transcribe_many(short_wav, []) == [] and len(fake.calls) == 1


def test_worker_timeout_raises_asr_timeout(ready, short_wav, monkeypatch):
    monkeypatch.setattr(children, "run", FakeWorker(timeout=True))
    with pytest.raises(ToolError) as caught:
        ready.transcribe(short_wav)
    assert caught.value.code == "asr_timeout"
    assert str(caught.value) == "Speech to text took too long and was stopped."


def test_worker_failure_raises_asr_failed_with_details(ready, short_wav, monkeypatch):
    stderr = "Traceback (most recent call last):\n  ...\nRuntimeError: Metal ran out of memory"
    monkeypatch.setattr(children, "run", FakeWorker(returncode=1, stderr=stderr))
    with pytest.raises(ToolError) as caught:
        ready.transcribe(short_wav)
    assert caught.value.code == "asr_failed" and not caught.value.retryable
    assert str(caught.value) == "Speech to text failed."
    assert "Metal ran out of memory" in caught.value.details


@pytest.mark.parametrize(
    "fake",
    [
        FakeWorker(write=False),  # exit 0 but no result file
        FakeWorker(results=[HEARD, HEARD]),  # two results for one piece of audio
        FakeWorker(results=[[{"text": "hi"}]]),  # a word without times
    ],
)
def test_a_worker_result_that_does_not_add_up_is_a_failure(fake, ready, short_wav, monkeypatch):
    monkeypatch.setattr(children, "run", fake)
    with pytest.raises(ToolError) as caught:
        ready.transcribe(short_wav)
    assert caught.value.code == "asr_failed"


def test_missing_model_exit_code_is_retryable(ready, short_wav, monkeypatch):
    monkeypatch.setattr(children, "run", FakeWorker(returncode=worker.EXIT_MODEL))
    with pytest.raises(ToolError) as caught:
        ready.transcribe(short_wav)
    assert caught.value.code == "asr_model_missing" and caught.value.retryable
    assert str(caught.value) == (
        "The speech model (about 2.5 GB) isn't on this Mac yet and couldn't be downloaded. "
        "Check the internet connection and try again."
    )


def test_a_missing_model_is_fetched_first_with_a_long_timeout(tmp_path, short_wav, monkeypatch):
    engine = ParakeetEngine(tmp_path / "hf")
    fake = FakeWorker(on_prepare=lambda: seed(engine))
    monkeypatch.setattr(children, "run", fake)
    engine.transcribe(short_wav)
    (prepare, prepare_kwargs), (cmd, _) = fake.calls
    assert "--prepare" in prepare and "--wav" not in prepare
    assert after(prepare, "--model-id") == "mlx-community/parakeet-tdt-0.6b-v3"
    assert prepare_kwargs["timeout"] == 3 * 3600  # 2.5 GB in three hours needs under 2 Mbit/s
    assert prepare_kwargs["env"]["HF_HOME"] == str(tmp_path / "hf")
    assert "--prepare" not in cmd


def test_a_fetch_that_leaves_no_model_is_an_error(tmp_path, short_wav, monkeypatch):
    monkeypatch.setattr(children, "run", FakeWorker())
    with pytest.raises(ToolError) as caught:
        ParakeetEngine(tmp_path / "hf").transcribe(short_wav)
    assert caught.value.code == "asr_model_missing" and caught.value.retryable


def test_a_stopped_model_download_leaves_no_partial_file(tmp_path, monkeypatch):
    # huggingface_hub 2.2 never resumes a partial file, and a killed download can't delete its own.
    blobs = tmp_path / "hf" / "hub" / "blobs" / "9a"
    blobs.mkdir(parents=True)
    done = blobs / "4f469c2e"
    done.write_text("a finished file")

    def partly_downloaded():
        (blobs / "9a0bf05b.1a2b3c4d.incomplete").write_text("half a model")

    monkeypatch.setattr(children, "run", FakeWorker(timeout=True, on_prepare=partly_downloaded))
    with pytest.raises(ToolError) as caught:
        ParakeetEngine(tmp_path / "hf").prepare()
    assert caught.value.code == "asr_model_timeout" and caught.value.retryable
    assert str(caught.value) == (
        "Downloading the speech model (about 2.5 GB) took too long and was stopped. "
        "Check the internet connection and try again."
    )
    assert list(blobs.iterdir()) == [done]


def test_model_folder_follows_the_hugging_face_layout(tmp_path):
    engine = ParakeetEngine(tmp_path)
    assert engine.model_dir() == (
        tmp_path / "hub" / "models--mlx-community--parakeet-tdt-0.6b-v3" / "snapshots" / PARAKEET_REVISION
    )
    assert not engine.model_ready()
    seed(engine)
    assert engine.model_ready()
    (engine.model_dir() / "model.safetensors").unlink()
    assert not engine.model_ready()  # half a model is no model


def test_worker_env_keeps_secrets_and_outside_settings_out(tmp_path, monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "GITHUB_PERSONAL_ACCESS_TOKEN", "AWS_SECRET_ACCESS_KEY", "HF_TOKEN"):
        monkeypatch.setenv(name, "hunter2")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("HF_HUB_CACHE", "/somewhere/else")
    monkeypatch.setenv("LANG", "en_AU.UTF-8")
    env = worker_env(tmp_path / "hf")
    assert "hunter2" not in env.values()
    assert env["HF_HOME"] == str(tmp_path / "hf") and env["HF_HUB_CACHE"] == str(tmp_path / "hf" / "hub")
    assert "HF_HUB_OFFLINE" not in env and env["LANG"] == "en_AU.UTF-8"
    assert env["PATH"].split(os.pathsep)[0] == str(Path(ffmpeg_bin()).parent)  # the worker's ffmpeg


def test_parakeet_is_available():
    assert ParakeetEngine(Path("/nowhere")).available()


def test_whisper_available_follows_the_import(tmp_path, short_wav, monkeypatch):
    monkeypatch.setattr(whisper_mlx, "find_spec", lambda name: None)
    engine = WhisperEngine(tmp_path / "hf")
    assert not engine.available()
    with pytest.raises(ToolMissing) as caught:
        engine.transcribe(short_wav)
    assert caught.value.code == "whisper_missing"
    assert str(caught.value) == "The Whisper add-on isn't installed. Install it with: uv sync --extra whisper"
    monkeypatch.setattr(whisper_mlx, "find_spec", lambda name: object() if name == "mlx_whisper" else None)
    assert engine.available()


def test_whisper_command(tmp_path, short_wav, monkeypatch):
    monkeypatch.setattr(whisper_mlx, "find_spec", lambda name: object())
    engine = WhisperEngine(tmp_path / "hf")
    seed(engine)
    fake = FakeWorker()
    monkeypatch.setattr(children, "run", fake)
    assert engine.transcribe(short_wav, language="ar").engine == "whisper"
    [(cmd, _)] = fake.calls
    assert after(cmd, "--engine") == "whisper" and after(cmd, "--language") == "ar"
    assert after(cmd, "--model-id") == "mlx-community/whisper-large-v3-turbo"
    assert after(cmd, "--revision") == "a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb"
    assert engine.files == ("config.json", "weights.safetensors")


# Inside the worker


def test_words_from_tokens_merges_subwords():
    words = words_from_tokens(
        [
            ("Hi", 0.0, 0.1, 0.99),  # the first token starts a word with or without a space
            (" Hel", 0.2, 0.3, 0.9),
            ("lo", 0.3, 0.4, 0.6),
            (" world", 0.5, 0.9, None),
            (" ", 1.0, 1.0, 0.95),  # a lone space belongs to the token after it
            ("again", 1.0, 1.3, 0.8),
            (" ", 1.5, 1.5, 0.9),  # and a trailing one makes no word at all
        ]
    )
    assert words == [
        RawWord(text="Hi", start=0.0, end=0.1, conf=0.99),
        RawWord(text="Hello", start=0.2, end=0.4, conf=0.6),
        RawWord(text="world", start=0.5, end=0.9, conf=None),
        RawWord(text="again", start=1.0, end=1.3, conf=0.8),
    ]
    assert words_from_tokens([]) == []


def test_decoding_uses_a_beam_of_five():
    import parakeet_mlx

    assert decoding(True).decoding == parakeet_mlx.Beam(beam_size=5)
    assert isinstance(decoding(False).decoding, parakeet_mlx.Greedy)


def test_parakeet_transcribe_takes_the_chunk_settings():
    import inspect

    import parakeet_mlx

    params = inspect.signature(parakeet_mlx.BaseParakeet.transcribe).parameters
    assert {"decoding_config", "chunk_duration", "overlap_duration"} <= set(params)


class FakeParakeet:
    """A loaded model that hears two words half a second into whatever it is given."""

    def __init__(self):
        self.calls = []

    def transcribe(self, path, **kwargs):
        self.calls.append((Path(path), wav_seconds(path), kwargs))
        tokens = [(" hello", 0.5, 0.9, 0.95), (" there.", 1.0, 1.4, 0.9)]
        return SimpleNamespace(
            tokens=[SimpleNamespace(text=t, start=s, end=e, confidence=c) for t, s, e, c in tokens]
        )


@pytest.fixture
def fake_parakeet(tmp_path, monkeypatch):
    import parakeet_mlx

    model, loaded = FakeParakeet(), []
    monkeypatch.setattr(parakeet_mlx, "from_pretrained", lambda path, **kwargs: loaded.append(path) or model)
    monkeypatch.setattr(worker, "fetch_model", lambda model_id, revision, files: tmp_path / "model")
    model.loaded = loaded
    return model


def worker_args(wav: Path, out: Path, *extra: str) -> list[str]:
    return [
        "--engine", "parakeet", "--model-id", "mlx-community/parakeet-tdt-0.6b-v3",
        "--revision", PARAKEET_REVISION, "--file", "config.json", "--file", "model.safetensors",
        "--wav", str(wav), "--out", str(out), *extra,
    ]  # fmt: skip


def heard(out: Path) -> list[list[tuple[str, float, float]]]:
    return [
        [(w["text"], w["start"], w["end"]) for w in words] for words in json.loads(out.read_text())["results"]
    ]


def test_worker_cuts_windows_and_moves_times_back(fake_parakeet, tmp_path):
    import parakeet_mlx

    wav, out = make_wav(tmp_path / "talk.wav", [(20, 0.1)]), tmp_path / "out.json"
    windows = ["--start", "5.0", "--end", "8.0", "--start", "12.0", "--end", "12.05"]
    assert worker.main(worker_args(wav, out, "--beam", *windows)) == 0
    first, second = heard(out)
    assert [w[0] for w in first] == ["hello", "there."]
    assert [t for w in first for t in w[1:]] == pytest.approx([5.5, 5.9, 6.0, 6.4])
    assert second == []  # 50 ms is too short to hear, so the model never sees it
    assert fake_parakeet.loaded == [str(tmp_path / "model")]
    [(path, seconds, kwargs)] = fake_parakeet.calls
    assert path != wav and seconds == pytest.approx(3.0)
    assert kwargs["chunk_duration"] == 60.0 and kwargs["overlap_duration"] == 10.0
    assert kwargs["decoding_config"].decoding == parakeet_mlx.Beam(beam_size=5)
    data = json.loads(out.read_text())
    assert (
        data["engine_version"]
        == f"parakeet-mlx 0.5.3, mlx-community/parakeet-tdt-0.6b-v3@{PARAKEET_REVISION[:7]}"
    )


def test_worker_loads_the_model_once_for_every_window(fake_parakeet, tmp_path):
    wav, out = make_wav(tmp_path / "talk.wav", [(20, 0.1)]), tmp_path / "out.json"
    windows = ["--start", "1.0", "--end", "3.0", "--start", "10.0", "--end", "12.0"]
    assert worker.main(worker_args(wav, out, *windows)) == 0
    assert len(fake_parakeet.calls) == 2 and fake_parakeet.loaded == [str(tmp_path / "model")]


def test_worker_hears_the_whole_file_without_windows(fake_parakeet, tmp_path):
    import parakeet_mlx

    wav, out = make_wav(tmp_path / "talk.wav", [(3, 0.1)]), tmp_path / "out.json"
    assert worker.main(worker_args(wav, out)) == 0
    assert heard(out) == [[("hello", 0.5, 0.9), ("there.", 1.0, 1.4)]]
    [(path, _, kwargs)] = fake_parakeet.calls
    assert path == wav and isinstance(kwargs["decoding_config"].decoding, parakeet_mlx.Greedy)


def test_worker_skips_audio_too_short_to_hear(fake_parakeet, tmp_path):
    wav, out = make_wav(tmp_path / "blip.wav", [(0.05, 0.1)]), tmp_path / "out.json"
    assert worker.main(worker_args(wav, out)) == 0
    assert heard(out) == [[]] and fake_parakeet.calls == [] and fake_parakeet.loaded == []


def test_worker_exit_code_when_the_model_cannot_be_had(monkeypatch):
    def unreachable(*args, **kwargs):
        raise OSError("no internet")

    monkeypatch.setattr(huggingface_hub, "snapshot_download", unreachable)
    args = ["--engine", "parakeet", "--model-id", "m/x", "--revision", "abc", "--file", "config.json"]
    assert worker.main([*args, "--prepare"]) == worker.EXIT_MODEL


def test_fetch_model_reads_the_cache_before_the_network(tmp_path, monkeypatch):
    folder = tmp_path / "snapshot"
    folder.mkdir()
    (folder / "config.json").write_text("{}")
    calls = []

    def cached(repo_id, **kwargs):
        calls.append((repo_id, kwargs))
        return str(folder)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", cached)
    assert worker.fetch_model("m/x", "abc", ["config.json"]) == folder
    [(repo_id, kwargs)] = calls
    assert repo_id == "m/x" and kwargs["local_files_only"] is True
    assert kwargs["revision"] == "abc" and kwargs["allow_patterns"] == ["config.json"]


def test_fetch_model_downloads_what_the_cache_lacks(tmp_path, monkeypatch):
    folder, calls = tmp_path / "snapshot", []

    def download(repo_id, **kwargs):
        calls.append(kwargs.get("local_files_only", False))
        if kwargs.get("local_files_only"):
            raise huggingface_hub.errors.LocalEntryNotFoundError("not cached")
        folder.mkdir()
        (folder / "config.json").write_text("{}")
        return str(folder)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", download)
    assert worker.fetch_model("m/x", "abc", ["config.json"]) == folder
    assert calls == [True, False]


def test_fetch_model_refuses_an_incomplete_download(tmp_path, monkeypatch):
    (tmp_path / "snapshot").mkdir()
    monkeypatch.setattr(
        huggingface_hub, "snapshot_download", lambda repo_id, **kwargs: str(tmp_path / "snapshot")
    )
    with pytest.raises(worker.ModelUnavailable):
        worker.fetch_model("m/x", "abc", ["config.json"])


def test_whisper_worker_reads_words_with_their_times(tmp_path, monkeypatch):
    seen = []

    def transcribe(path, **kwargs):
        seen.append((path, kwargs))
        words = [
            {"word": " marhaba", "start": 0.1, "end": 0.5, "probability": 0.8},
            {"word": " ", "start": 0.5, "end": 0.5, "probability": 0.1},
        ]
        return {"language": "ar", "segments": [{"words": words}, {"text": "no words here"}]}

    monkeypatch.setitem(sys.modules, "mlx_whisper", SimpleNamespace(transcribe=transcribe))
    pieces = [(tmp_path / "piece.wav", 10.0), (None, 30.0)]
    results, language = whisper_mlx.run(tmp_path / "model", pieces, language="ar", beam=True)
    [[word], []] = results
    assert (word.text, word.start, word.end, word.conf) == ("marhaba", pytest.approx(10.1), 10.5, 0.8)
    assert language == "ar"
    [(path, kwargs)] = seen
    assert path == str(tmp_path / "piece.wav")
    assert kwargs == {
        "path_or_hf_repo": str(tmp_path / "model"),
        "word_timestamps": True,
        "condition_on_previous_text": False,
        "language": "ar",
    }


# YouTube's captions as a transcript


def test_transcript_from_captions():
    words = [CapWord(text="so", start=1.0, end=1.4), CapWord(text="this", start=1.4, end=1.9)]
    result = transcript_from_captions(words, "en-orig")
    assert result == ASRResult(
        engine="youtube-captions",
        engine_version="en-orig",
        language="en",
        words=[RawWord(text="so", start=1.0, end=1.4), RawWord(text="this", start=1.4, end=1.9)],
    )


def test_base_language():
    assert [base.base_language(c) for c in ("en-US", "pt_BR", "EN", "zh-Hans", None, "")] == [
        "en",
        "pt",
        "en",
        "zh",
        None,
        None,
    ]
