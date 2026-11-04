"""The clip's sound (SPEC 7.10 item 4, 8.5): synthesised effects, the cut joined with crossfades, the speech
cleaned, the effects under it, then two-pass loudnorm to -14 LUFS in AAC."""

import json
import math
import re
import subprocess

import numpy as np
import pytest

from clipforge.library.sfx import KINDS, LENGTH_S, PEAK_S, synth
from clipforge.media.ffmpeg import ffmpeg_bin, ffprobe_bin
from clipforge.render.audio import SR, assemble, clean_speech, decode_audio, finish, loudness, mix_sfx
from clipforge.render.edl import EDL, Span, sfx_layer
from clipforge.render.styles import Style
from clipforge.render.timeline import Segment, Timeline
from clipforge.vision.decode import ClipSource
from tests.helpers import make_test_video, make_wav


def raw(path, pcm):
    """``pcm`` written as raw stereo float, and the ffmpeg input options that read it back."""
    path.write_bytes(np.ascontiguousarray(pcm, dtype="<f4").tobytes())
    return ["-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", str(path)]


def ebur128(inputs):
    """The integrated loudness and true peak ffmpeg's ebur128 filter measures for ``inputs``."""
    proc = subprocess.run(
        [ffmpeg_bin(), "-hide_banner", "-nostdin", "-nostats", *inputs,
         "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True,
        text=True,
        check=True,
    )  # fmt: skip
    summary = proc.stderr.split("Summary:")[-1]
    level = re.search(r"I:\s+(\S+) LUFS", summary)
    peak = re.search(r"Peak:\s+(\S+) dBFS", summary)
    assert level and peak, proc.stderr[-500:]
    return float(level.group(1)), float(peak.group(1))


def stream(path):
    proc = subprocess.run(
        [ffprobe_bin(), "-v", "error", "-select_streams", "a:0", "-show_entries",
         "stream=codec_name,profile,sample_rate,channels:format=duration", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )  # fmt: skip
    data = json.loads(proc.stdout)
    return data["streams"][0], float(data["format"]["duration"])


def edl_for(ranges, fps=30.0, speed=1.0):
    tl = Timeline.from_ranges(ranges, fps, speed)
    return EDL(
        clip_id="c1",
        fps=fps,
        src_width=1920,
        src_height=1080,
        duration=tl.duration,
        segments=tl.segments,
        style=Style(),
    )


def stereo(x):
    return np.stack([x, x], axis=1).astype(np.float32)


def rms(x):
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))


def peak_hz(x):
    n = 8 * len(x)  # zero padded, for a finer reading
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x)), n))
    return float(np.argmax(spec) * SR / n)


# Effects


def test_synth_is_repeatable_and_short():
    sounds = {}
    for kind in KINDS:
        x = synth(kind)
        assert x.dtype == np.float32 and x.ndim == 1
        assert np.array_equal(x, synth(kind))
        assert 0.01 <= len(x) / SR <= 1.5 and len(x) == round(LENGTH_S[kind] * SR)
        assert abs(float(np.abs(x).max()) - 1.0) < 1e-6
        assert abs(x[0]) < 0.01 and abs(x[-1]) < 0.01
        w = max(1, round(0.2 * len(x)))
        loudest = np.argmax(np.convolve(np.square(x, dtype=np.float64), np.ones(w), "same")) / SR
        assert abs(loudest - PEAK_S[kind]) <= 0.15 * LENGTH_S[kind] + 0.005, kind
        sounds[kind] = x
    for a in KINDS:
        for b in KINDS:
            n = min(len(sounds[a]), len(sounds[b]))
            assert a == b or not np.allclose(sounds[a][:n], sounds[b][:n], atol=0.05)
    assert len(synth("pop", 24000)) == round(LENGTH_S["pop"] * 24000)
    with pytest.raises(ValueError):
        synth("boom")


def span(start, end, layout):
    return Span(start=start, end=end, layout=layout)


def test_sfx_cues_mark_layout_changes_emoji_and_the_payoff():
    spans = [
        span(0.0, 2.0, "face"),
        span(2.0, 3.0, "stacked"),
        span(3.0, 6.5, "face"),
        span(6.5, 9.0, "motion"),  # the same full frame as a face: no hard change
        span(9.0, 12.0, "stacked"),
    ]
    emoji = [{"emoji": "x", "start": t, "end": t + 1.2} for t in (4.0, 7.0, 10.5)]
    cues = sfx_layer(spans, emoji, 10.7, Style(), 12.0)
    assert [(c["kind"], c["on"]) for c in cues] == [
        ("whoosh", 2.0),
        ("pop", 4.0),
        ("whoosh", 9.0),
        ("hit", 10.7),
    ]
    for c in cues:
        assert c["at"] == pytest.approx(c["on"] - PEAK_S[c["kind"]])
    assert [c["at"] for c in cues] == sorted(c["at"] for c in cues)
    assert sfx_layer(spans, emoji, 10.7, Style(sfx=False), 12.0) == []
    assert sfx_layer(spans[:1], [], None, Style(), 12.0) == []


# The cut


def test_assemble_matches_the_timeline_length():
    rng = np.random.default_rng(1)
    base = 10.0
    pcm = rng.uniform(-0.5, 0.5, (8 * SR, 2)).astype(np.float32)
    ranges = [(10.5, 11.7), (12.0, 13.33), (14.1, 16.0)]
    for fps in (30.0, 29.97):
        edl = edl_for(ranges, fps)
        out = assemble(pcm, base, edl)
        assert out.shape == (round(edl.duration * SR), 2) and out.dtype == np.float32
        for k, seg in enumerate(edl.segments):
            o0 = round(seg.out_start * SR)
            last = k + 1 == len(edl.segments)
            o1 = len(out) if last else round(edl.segments[k + 1].out_start * SR)
            a = round((seg.src_start - base) * SR)
            lo, hi = o0 + 400, o1 - (7300 if last else 400)  # clear of the fades and the crossfades
            assert np.array_equal(out[lo:hi], pcm[a + lo - o0 : a + hi - o0])
    t = base + np.arange(8 * SR) / SR
    hz = np.where(t < 11.8, 440.0, np.where(t < 13.9, 660.0, 880.0))
    tone = stereo(0.5 * np.sin(2 * np.pi * np.cumsum(hz) / SR))
    edl = edl_for(ranges, 30.0, 1.1)
    out = assemble(tone, base, edl)
    assert out.shape == (round(edl.duration * SR), 2)
    for k, (seg, want) in enumerate(zip(edl.segments, (440, 660, 880), strict=True)):
        o0 = round(seg.out_start * SR)
        o1 = len(out) - round(0.2 * SR) if k == 2 else round(edl.segments[k + 1].out_start * SR)
        assert abs(peak_hz(out[o0 + 2400 : o1 - 2400, 0]) - want) < 5  # sped up with the pitch kept
        for a in (o0 + round(0.03 * SR), o1 - round(0.09 * SR)):  # and each stretch in its place
            assert abs(peak_hz(out[a : a + round(0.06 * SR), 0]) - want) < 10, (k, a)


def test_assemble_crossfades_leave_no_click():
    t = np.arange(4 * SR) / SR
    pcm = stereo(0.5 * np.sin(2 * np.pi * 480 * t))  # 100 samples a cycle
    # each join goes from a crest straight to a trough
    edl = edl_for([(25 / SR, 25 / SR + 0.5), (1.0 + 75 / SR, 1.5 + 75 / SR), (2.0 + 25 / SR, 2.6 + 25 / SR)])
    out = assemble(pcm, 0.0, edl)
    step = 2 * np.pi * 480 / SR * 0.5  # the largest step the tone itself takes
    assert float(np.abs(np.diff(out[:, 0])).max()) < 2.2 * step
    assert not out[0].any() and not out[-1].any()
    assert rms(out[-round(0.05 * SR) :, 0]) < 0.5 * rms(out[-round(0.4 * SR) : -round(0.2 * SR), 0])


def test_assemble_joins_different_speeds_without_a_click():
    t = np.arange(4 * SR) / SR
    pcm = stereo(0.5 * np.sin(2 * np.pi * 480 * t))
    segments = [
        Segment(src_start=25 / SR, src_end=25 / SR + 0.5, out_start=0.0),
        Segment(src_start=1.0 + 75 / SR, src_end=1.55 + 75 / SR, out_start=0.5, speed=1.1),
    ]
    edl = EDL(
        clip_id="c1",
        fps=30.0,
        src_width=1920,
        src_height=1080,
        duration=1.0,
        segments=segments,
        style=Style(),
    )
    out = assemble(pcm, 0.0, edl)
    assert out.shape == (SR, 2)
    assert float(np.abs(np.diff(out[:, 0])).max()) < 2.2 * (2 * np.pi * 480 / SR * 0.5)


def test_decode_audio_reads_the_range_and_pads_with_silence(tmp_path):
    wav = make_wav(tmp_path / "a.wav", [(1.0, 0.0), (1.0, 0.5), (2.0, 0.0)], sr=SR)
    x = decode_audio(ClipSource(wav), 0.5, 2.5)
    assert x.shape == (2 * SR, 2) and x.dtype == np.float32
    assert np.array_equal(x[:, 0], x[:, 1])
    assert rms(x[: round(0.45 * SR)]) < 1e-3 and rms(x[round(1.55 * SR) :]) < 1e-3
    assert rms(x[round(0.55 * SR) : round(1.45 * SR)]) > 0.2
    assert np.array_equal(decode_audio(ClipSource(wav, offset_s=10.0), 10.5, 12.5), x)
    past = decode_audio(ClipSource(wav), 3.5, 4.5)  # the file ends at 4 s
    assert past.shape == (SR, 2) and not past[round(0.5 * SR) + 10 :].any()
    early = decode_audio(ClipSource(wav, offset_s=10.0), 9.0, 11.5)  # from 1 s before the file starts
    assert early.shape == (round(2.5 * SR), 2) and not early[:SR].any()
    assert (
        rms(early[SR + 100 : 2 * SR - 2400]) < 1e-3 and rms(early[round(2.05 * SR) : round(2.45 * SR)]) > 0.2
    )
    mute = make_test_video(tmp_path / "v.mp4", seconds=1.0, audio=False)
    assert np.array_equal(decode_audio(ClipSource(mute), 0.0, 1.0), np.zeros((SR, 2), np.float32))


# Levels


def test_loudness_matches_ffmpeg_ebur128(tmp_path):
    rng = np.random.default_rng(4)
    t = np.arange(6 * SR) / SR
    left = (
        0.1 * np.sin(2 * np.pi * 100 * t)
        + 0.05 * rng.standard_normal(t.size)
        + 0.1 * np.sin(2 * np.pi * 30 * t)
    )
    right = 0.08 * np.sin(2 * np.pi * 3000 * t) + 0.02 * rng.standard_normal(t.size)
    x = (
        np.stack([left, right], axis=1) * np.where(t < 2.0, 0.01, 1.0)[:, None]
    )  # a quiet start the gate drops
    x = x.astype(np.float32)
    level, _ = ebur128(raw(tmp_path / "x.f32", x))
    assert abs(loudness(x) - level) < 0.2
    assert abs(loudness(x, block_s=0.1) - level) < 0.3
    assert loudness(x[2 * SR :]) > loudness(x[: 2 * SR]) + 30
    assert loudness(np.zeros((SR, 2), np.float32)) == -math.inf


def test_sfx_sit_6_db_under_speech():
    rng = np.random.default_rng(2)
    speech = rng.normal(0.0, 0.02, (14 * SR, 2)).astype(np.float32)
    speech[5 * SR : 10 * SR] *= 10 ** (10 / 20)
    speech[10 * SR :] = rng.normal(0.0, 1e-4, (4 * SR, 2))  # -80 dBFS: as good as silence
    cues = [
        {"kind": "whoosh", "at": 1.5},
        {"kind": "pop", "at": 3.0},
        {"kind": "hit", "at": 7.5},
        {"kind": "pop", "at": 12.5},  # nothing but near silence around it
    ]
    mixed = mix_sfx(speech, cues, SR)
    assert mixed.shape == speech.shape and mixed.dtype == np.float32
    added = mixed - speech
    for cue, (a, b) in zip(cues[:3], [(0, 5), (0, 5), (5, 10)], strict=True):
        i, n = round(cue["at"] * SR), round(LENGTH_S[cue["kind"]] * SR)
        fx = added[i : i + n]
        assert np.allclose(fx[:, 0], fx[:, 1], rtol=0, atol=1e-6)  # the same on both, but for rounding
        assert np.corrcoef(fx[:, 0], synth(cue["kind"]))[0, 1] > 0.999
        under = loudness(speech[a * SR : b * SR]) - loudness(fx, block_s=min(0.1, n / SR))
        assert 6.0 <= under <= 12.0, (cue, under)
        assert not added[i - round(0.2 * SR) : i].any()
    assert not added[10 * SR :].any()
    assert np.array_equal(mix_sfx(speech, [], SR), speech)


def test_effects_fade_with_the_clip():
    rng = np.random.default_rng(6)
    speech = rng.normal(0.0, 0.05, (3 * SR, 2)).astype(np.float32)
    hit = mix_sfx(speech, [{"kind": "hit", "at": 2.9}], SR, fade_s=0.15) - speech
    assert not hit[-1].any()
    assert rms(hit[-round(0.02 * SR) :]) < 0.3 * rms(hit[round(2.9 * SR) : round(2.92 * SR)])


def test_clean_speech_cuts_rumble_and_levels_the_speech():
    t = np.arange(6 * SR) / SR
    x = stereo(0.1 * np.sin(2 * np.pi * 30 * t) + 0.1 * np.sin(2 * np.pi * 1000 * t))
    y = clean_speech(x, SR)
    assert y.shape == x.shape and y.dtype == np.float32

    def at(a, hz):
        mid = a[SR : 5 * SR, 0]
        spec = np.abs(np.fft.rfft(mid * np.hanning(len(mid))))
        return spec[round(hz * len(mid) / SR)]

    assert 20 * math.log10((at(x, 30) / at(x, 1000)) / (at(y, 30) / at(y, 1000))) >= 12
    rng = np.random.default_rng(7)
    voice = rng.standard_normal(t.size) * (0.5 + 0.5 * np.sin(2 * np.pi * 4 * t) ** 2)
    quiet, loud = clean_speech(stereo(0.003 * voice), SR), clean_speech(stereo(0.2 * voice), SR)
    assert abs(loudness(quiet) - loudness(loud)) < 1.0
    silence = np.zeros((SR, 2), np.float32)
    assert np.array_equal(clean_speech(silence, SR), silence)


def test_clean_speech_tames_loud_bursts():
    rng = np.random.default_rng(5)
    t = np.arange(16 * SR) / SR
    burst = ((t % 4.0) > 1.0) & ((t % 4.0) < 1.3)  # 14 dB louder for 0.3 s in every 4 s
    voice = rng.standard_normal(t.size) * 0.03 * (0.5 + 0.5 * np.sin(2 * np.pi * 4 * t) ** 2)
    x = stereo(voice * np.where(burst, 10 ** (14 / 20), 1.0))
    y = clean_speech(x, SR)

    def contrast(a):
        return 20 * math.log10(rms(a[burst, 0]) / rms(a[~burst, 0]))

    assert contrast(x) - contrast(y) >= 2.5


def test_clean_speech_softens_harsh_sibilance():
    rng = np.random.default_rng(8)
    t = np.arange(8 * SR) / SR
    voiced = sum(np.sin(2 * np.pi * 200 * k * t) / k for k in range(1, 12)) * 0.05
    hiss = np.fft.rfft(rng.standard_normal(t.size))
    f = np.fft.rfftfreq(t.size, 1 / SR)
    hiss[(f < 5000) | (f > 10000)] = 0
    hiss = np.fft.irfft(hiss, t.size)
    sib = ((t % 2.0) > 0.5) & ((t % 2.0) < 0.7)
    x = stereo(voiced + np.where(sib, 0.2 * hiss / rms(hiss), 0.0))
    y = clean_speech(x, SR)

    def hf_share(a):
        spec = np.abs(np.fft.rfft(a[:, 0] * sib)) ** 2
        return 10 * math.log10(spec[f > 5000].sum() / spec[f < 4000].sum())

    assert hf_share(x) - hf_share(y) >= 8.0  # a de-esser at 0.4 manages 4.9 dB here, at 0.7 13 dB


def test_finish_reaches_minus_14_lufs(tmp_path):
    t = np.arange(10 * SR) / SR
    pcm = stereo(0.03 * np.sin(2 * np.pi * 440 * t) + 0.02 * np.sin(2 * np.pi * 1000 * t))
    out = tmp_path / "out" / "a.m4a"
    out.parent.mkdir()
    report = finish(pcm, SR, out)
    level, peak = ebur128(["-i", str(out)])
    assert abs(level - -14.0) <= 1.0
    assert report.silent is False and -35 < report.input_lufs < -25
    assert abs(report.output_lufs - level) <= 0.5 and abs(report.true_peak - peak) <= 0.5
    info, duration = stream(out)
    assert info == {"codec_name": "aac", "profile": "LC", "sample_rate": "48000", "channels": 2}
    assert abs(duration - 10.0) < 0.1
    assert [p.name for p in out.parent.iterdir()] == ["a.m4a"]


def test_finish_keeps_silence_silent(tmp_path):
    rng = np.random.default_rng(3)
    for name, pcm in [
        ("zeros", np.zeros((5 * SR, 2), np.float32)),
        ("hiss", (rng.standard_normal((5 * SR, 2)) * 10 ** (-85 / 20)).astype(np.float32)),
    ]:
        out = tmp_path / f"{name}.m4a"
        report = finish(pcm, SR, out)
        assert report.silent is True and report.input_lufs == -99.0
        level, peak = ebur128(["-i", str(out)])
        assert level <= -70 and peak <= -60
        assert report.output_lufs == -99.0 and report.true_peak <= -60


def test_finish_caps_the_peak_of_loud_audio(tmp_path):
    t = np.arange(10 * SR) / SR
    bed = 0.01 * np.sin(2 * np.pi * 300 * t)
    burst = np.zeros_like(t)
    for k in range(10):  # ten 10 ms bursts of a clipped full scale tone
        i = int((k + 0.5) * SR)
        burst[i : i + 480] = np.clip(3 * np.sin(2 * np.pi * 1000 * t[:480]), -1, 1)
    out = tmp_path / "loud.m4a"
    report = finish(stereo(bed + burst), SR, out)
    level, peak = ebur128(["-i", str(out)])
    assert peak <= -1.4 and report.true_peak <= -1.4
    assert -16.0 <= level <= -13.0 and report.input_lufs > -15


def test_finish_keeps_the_timestamps_continuous(tmp_path):
    # 3.003 s is not a whole number of loudnorm's 100 ms blocks: its look-ahead used to leave a gap in the
    # timestamps 3 s before the end, so the last 3 s played up to 100 ms behind the picture
    n = round(3.003 * SR)
    out = tmp_path / "a.m4a"
    finish(stereo(0.1 * np.sin(2 * np.pi * 440 * np.arange(n) / SR)), SR, out)
    proc = subprocess.run(
        [ffprobe_bin(), "-v", "error", "-select_streams", "a:0", "-show_entries", "packet=pts,duration",
         "-of", "csv=p=0", str(out)],
        capture_output=True,
        text=True,
        check=True,
    )  # fmt: skip
    packets = [tuple(int(v) for v in line.split(",")[:2]) for line in proc.stdout.split()]
    assert len(packets) > 100
    assert {d for _, d in packets[:-1]} == {1024}  # one AAC frame each, none stretched over a gap
    assert stream(out)[1] == pytest.approx(n / SR, abs=0.001)
