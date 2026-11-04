import sys
import wave
from pathlib import Path

import numpy as np
import pytest

from clipforge.errors import ToolError, ToolMissing
from clipforge.media import ffmpeg
from clipforge.media.audio import percentile_ranks, read_wav16k, rms_per_second, wav_seconds, write_wav16k
from clipforge.media.ffmpeg import MediaInfo, ffmpeg_bin, ffprobe_bin, probe, run, to_wav16k
from tests.helpers import make_test_video, make_wav

# Finding the tools


def test_missing_ffmpeg_raises_tool_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(ffmpeg, "HOMEBREW_BIN", tmp_path)
    for find in (ffmpeg_bin, ffprobe_bin):
        with pytest.raises(ToolMissing) as caught:
            find()
        assert "Install ffmpeg with Homebrew: brew install ffmpeg" in str(caught.value)


def test_homebrew_fallback_when_not_on_path(tmp_path, monkeypatch):
    # An app started from the Dock gets a PATH without /opt/homebrew/bin.
    fake = tmp_path / "ffprobe"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(ffmpeg, "HOMEBREW_BIN", tmp_path)
    assert ffprobe_bin() == str(fake)


# Running them


def test_run_returns_the_output():
    proc = run([sys.executable, "-c", "print('hi')"])
    assert proc.returncode == 0 and proc.stdout.strip() == b"hi"


def test_run_feeds_stdin_when_given():
    count = [sys.executable, "-c", "import sys; print(len(sys.stdin.buffer.read()))"]
    assert run(count, stdin=b"abcdef").stdout.strip() == b"6"
    assert run(count).stdout.strip() == b"0"  # no stdin: the tool reads nothing and never waits


def test_run_raises_tool_error_with_stderr(tmp_path):
    with pytest.raises(ToolError) as caught:
        run([ffmpeg_bin(), "-hide_banner", "-nostdin", "-i", str(tmp_path / "missing.mp4")])
    err = caught.value
    assert err.code == "ffmpeg_failed"
    assert "missing.mp4" in err.details and "No such file" in err.details
    assert "ffmpeg" in str(err) and "missing.mp4" not in str(err)  # the plain message stays plain


def test_run_keeps_the_last_20_stderr_lines():
    script = "import sys\nfor i in range(30): print(f'line {i}', file=sys.stderr)\nsys.exit(3)"
    with pytest.raises(ToolError) as caught:
        run([sys.executable, "-c", script])
    lines = caught.value.details.splitlines()
    assert lines[-20:] == [f"line {i}" for i in range(10, 30)]
    assert "line 9" not in lines


def test_run_stops_a_tool_that_takes_too_long():
    with pytest.raises(ToolError) as caught:
        run([sys.executable, "-c", "import time; time.sleep(10)"], timeout_s=0.3)
    assert caught.value.code.endswith("_timeout") and "too long" in str(caught.value)


def test_run_missing_program_raises_tool_missing(tmp_path):
    with pytest.raises(ToolMissing):
        run([str(tmp_path / "ffmpeg"), "-version"])


# Probing


def test_probe_test_video(tmp_path):
    video = make_test_video(tmp_path / "v.mp4")
    info = probe(video)
    assert isinstance(info, MediaInfo)
    assert info.duration_s == pytest.approx(2.0, abs=0.1)
    assert (info.width, info.height) == (640, 360)
    assert info.fps == pytest.approx(30)
    assert info.has_video and info.has_audio
    assert (info.vcodec, info.acodec) == ("h264", "aac")
    assert info.size_bytes == video.stat().st_size


def test_probe_reports_the_displayed_size_of_a_rotated_video(tmp_path):
    # Phones store portrait video as landscape plus a 90 degree turn, and ffmpeg decodes it upright.
    info = probe(make_test_video(tmp_path / "phone.mp4", rotation=90))
    assert (info.width, info.height) == (360, 640)


def test_probe_reads_the_stream_details_qa_checks(tmp_path):
    video = make_test_video(tmp_path / "v.mp4", seconds=2.0)
    info = probe(video)
    assert info.pix_fmt == "yuv420p" and (info.sample_rate, info.channels) == (48000, 1)
    assert info.video_duration_s == pytest.approx(2.0, abs=0.04)
    assert (info.frames, info.nominal_fps) == (60, 30.0)
    muxed = tmp_path / "muxed.mp4"  # muxed as ClipForge muxes, so its first frame runs 1024 samples long
    run([ffmpeg_bin(), "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x240:r=30:d=2", "-f", "lavfi",
         "-i", "sine=sample_rate=48000:duration=2", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
         "-movflags", "+faststart", "-use_editlist", "0", str(muxed)])  # fmt: skip
    stretched = probe(muxed)
    assert (stretched.frames, stretched.nominal_fps) == (60, 30.0) and stretched.fps < 29.9
    assert info.audio_duration_s == pytest.approx(2.0, abs=0.05)
    assert info.transfer is None and not info.hdr
    for transfer, name in [(18, "arib-std-b67"), (16, "smpte2084")]:  # HLG, as an iPhone records, and PQ
        tagged = tmp_path / f"{name}.mp4"
        run([ffmpeg_bin(), "-v", "error", "-i", str(video), "-c", "copy", "-bsf:v",
             f"h264_metadata=transfer_characteristics={transfer}", str(tagged)])  # fmt: skip
        assert probe(tagged).transfer == name and probe(tagged).hdr
    audio = probe(make_wav(tmp_path / "a.wav", [(1.0, 0.5)]))
    assert audio.pix_fmt is None and audio.video_duration_s == 0.0 and not audio.hdr


def test_probe_audio_only(tmp_path):
    info = probe(make_wav(tmp_path / "a.wav", [(1.5, 0.5)]))
    assert info.has_audio and not info.has_video
    assert (info.width, info.height, info.fps, info.vcodec) == (0, 0, 0.0, None)
    assert info.acodec == "pcm_s16le"
    assert info.duration_s == pytest.approx(1.5, abs=0.01)


def test_probe_unreadable_file(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_text("not a video")
    with pytest.raises(ToolError) as caught:
        probe(bad)
    assert caught.value.code == "unreadable_media"
    assert "can't read" in str(caught.value) and caught.value.details


# Audio for speech to text


def test_to_wav16k_is_mono_16k_s16(tmp_path):
    dst = tmp_path / "work" / "audio16k.wav"
    assert to_wav16k(make_test_video(tmp_path / "v.mp4"), dst) == dst
    with wave.open(str(dst)) as w:
        assert (w.getnchannels(), w.getframerate(), w.getsampwidth()) == (1, 16000, 2)
        assert w.getnframes() / 16000 == pytest.approx(2.0, abs=0.1)
    assert not dst.with_suffix(".tmp.wav").exists()


def test_to_wav16k_failure_leaves_no_file(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_text("not a video")
    dst = tmp_path / "audio16k.wav"
    with pytest.raises(ToolError):
        to_wav16k(bad, dst)
    assert not dst.exists() and not dst.with_suffix(".tmp.wav").exists()


def test_to_wav16k_removes_a_partial_file(tmp_path, monkeypatch):
    def dies_halfway(args, **kwargs):  # ffmpeg killed or out of disk after it began writing
        Path(args[-1]).write_bytes(b"RIFF")
        raise ToolError("ffmpeg_failed", "ffmpeg hit an error")

    monkeypatch.setattr(ffmpeg, "run", dies_halfway)
    dst = tmp_path / "audio16k.wav"
    with pytest.raises(ToolError):
        to_wav16k(tmp_path / "v.mp4", dst)
    assert not dst.exists() and not dst.with_suffix(".tmp.wav").exists()


def test_read_wav16k(tmp_path):
    samples = read_wav16k(make_wav(tmp_path / "a.wav", [(0.5, 0.0), (0.5, 0.5)]))
    assert samples.dtype == np.float32 and samples.shape == (16000,)
    assert np.abs(samples[:8000]).max() == 0
    assert np.abs(samples[8000:]).max() == pytest.approx(0.5, abs=0.01)


def test_read_wav16k_rejects_other_formats(tmp_path):
    path = tmp_path / "stereo.wav"
    with wave.open(str(path), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(44100)
        out.writeframes(b"\0" * 400)
    with pytest.raises(ValueError, match="16 kHz mono"):
        read_wav16k(path)


def test_read_wav16k_reads_a_stretch(tmp_path):
    path = make_wav(tmp_path / "a.wav", [(1, 0.0), (1, 0.5)])
    samples = read_wav16k(path, start=0.5, end=1.5)
    assert samples.shape == (16000,)
    assert np.abs(samples[:8000]).max() == 0
    assert np.abs(samples[8000:]).max() == pytest.approx(0.5, abs=0.01)
    assert read_wav16k(path, start=1.5, end=9.0).shape == (8000,)  # stops where the file does
    assert read_wav16k(path, start=5.0).shape == (0,)


def test_write_wav16k_round_trip(tmp_path):
    samples = np.array([0.0, 0.5, -0.5, 1.5, -2.0], dtype=np.float32)
    path = write_wav16k(tmp_path / "out" / "b.wav", samples)
    assert read_wav16k(path) == pytest.approx([0.0, 0.5, -0.5, 32767 / 32768, -1.0], abs=1e-4)  # clipped
    assert wav_seconds(path) == pytest.approx(5 / 16000)
    assert wav_seconds(make_wav(tmp_path / "c.wav", [(2.5, 0.1)])) == pytest.approx(2.5)


def test_rms_per_second_loud_and_silent(tmp_path):
    rms = rms_per_second(read_wav16k(make_wav(tmp_path / "a.wav", [(1, 0.0), (1, 0.5)])))
    assert rms.shape == (2,)
    assert rms[0] < 0.001
    assert rms[1] == pytest.approx(0.354, abs=0.01)


def test_rms_per_second_counts_a_started_second():
    rms = rms_per_second(np.full(16000 * 2 + 4000, 0.25, dtype=np.float32))
    assert rms == pytest.approx([0.25, 0.25, 0.25])  # the short last second is not watered down
    assert rms_per_second(np.zeros(0, dtype=np.float32)).shape == (0,)


def test_rms_per_second_over_many_seconds_matches_one_by_one():
    rng = np.random.default_rng(7)
    samples = rng.uniform(-1, 1, 10 * 1500 + 3).astype(np.float32)
    rms = rms_per_second(samples, sr=10)
    seconds = [samples[i * 10 : (i + 1) * 10].astype(np.float64) for i in range(1501)]
    assert rms == pytest.approx([np.sqrt(np.mean(s**2)) for s in seconds], rel=1e-5)


def test_percentile_ranks():
    assert percentile_ranks([1, 2, 2, 3]) == pytest.approx([0, 0.5, 0.5, 1])
    assert percentile_ranks([3, 1, 2]) == pytest.approx([1, 0, 0.5])
    assert percentile_ranks([7, 7, 7]) == pytest.approx([0.5, 0.5, 0.5])
    assert percentile_ranks([5]) == pytest.approx([0.5])
    assert percentile_ranks([]).shape == (0,)
