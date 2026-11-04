"""The clip's sound (SPEC 7.10 item 4, 8.5): the kept stretches of the source audio joined with 15 ms
crossfades, the speech cleaned (80 Hz high-pass, gentle 3:1 compression, de-ess), a voiceover cleaned the
same way over it with the speech ducked under it when the clip has one, the effects mixed under that, the
music track under all of it (SPEC 8.6) when the clip has one, then two-pass loudnorm to -14 LUFS
integrated, -1.5 dBTP, LRA 11, in AAC-LC at 128 kbps, 48 kHz stereo.
Audio goes to ffmpeg and back as raw float through pipes, so no step leaves files behind."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel

from clipforge.errors import ToolError
from clipforge.library.sfx import Kind, synth
from clipforge.media.ffmpeg import QUIET, ffmpeg_bin, probe, run
from clipforge.render.edl import EDL, FADE_S
from clipforge.render.timeline import Segment
from clipforge.vision.decode import ClipSource

SR = 48000
XFADE_S = 0.015  # each join's crossfade, centred on the join
FADE_IN_S = 0.005  # so the clip's first sample starts from silence
SILENT_LUFS = -70.0  # quieter than this is silence: nothing to level, nothing to put effects under
FLOOR_DB = -99.0  # how silence is written in the report
PRE_LUFS = -20.0  # the speech is brought here before the compressor, so it acts the same on any source
COMPRESS = "acompressor=threshold=-18dB:ratio=3:attack=15:release=150"
DEESS = "deesser=i=0.7"  # ffmpeg's default strength of 0 does nothing
SFX_UNDER_DB = 8.0  # each effect this far under the speech around it (SPEC 8.5: at least 6)
SFX_BLOCK_S = 0.1  # loudness blocks short enough for a 120 ms pop
AROUND_S = 1.5  # how much speech either side of an effect it is measured against
PREROLL_S = 0.1  # real audio either side of a sped up run, so the stretch has settled by the cut
TARGET = "I=-14:TP=-1.5:LRA=11"
MUSIC_FADE_S = 0.3  # the music fades out over the clip's last 300 ms
BED_LUFS = -26.0  # where the music sits under a clip with no speech to sit it under
DUCK_DB = 6.0  # how much further the music drops while someone speaks
DUCK_ATTACK_S = 0.03  # how quickly it drops when speech starts
DUCK_RELEASE_S = 0.3  # and how slowly it comes back after
VO_DUCK_DB = 10.0  # how far the speech drops while the voiceover talks over it
SPEAKING_DB = 20.0  # a 50 ms stretch within this of the speech's loudness counts as someone speaking
SEAM_S = 0.05  # each loop of a short track crossfades into the next over this
# loudnorm's look-ahead leaves a gap in its timestamps 3 s from the end, though no sample is missing
RETIME = "asetpts=N/SR/TB"
AAC = ["-c:a", "aac", "-profile:a", "aac_low", "-b:a", "128k", "-ar", "48000", "-ac", "2"]
TIMEOUT_S = 600
# K-weighting (ITU-R BS.1770-4) at any sample rate, with the coefficients libebur128 (MIT) derives
SHELF_F0, SHELF_G, SHELF_Q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
SHELF_VB = 0.4996667741545416
HIGHPASS_F0, HIGHPASS_Q = 38.13547087602444, 0.5003270373238773
ABS_GATE = 10 ** ((-70.0 + 0.691) / 10)
K_SIZE, K_PAD = 1 << 18, 8192  # the K filter runs in FFTs of up to K_SIZE, K_PAD of context each side


class AudioReport(BaseModel):
    input_lufs: float
    output_lufs: float
    true_peak: float
    silent: bool  # under SILENT_LUFS, so it was written as it was, not levelled


def decode_audio(src: ClipSource, start_s: float, end_s: float, sr: int = SR) -> np.ndarray:
    """The source's sound from ``start_s`` to ``end_s`` (source time) as frames x 2 float32 at ``sr``, with
    silence wherever the file has none: before it starts, after it ends, or when it has no sound at all."""
    n = max(0, round((end_s - start_s) * sr))
    out = np.zeros((n, 2), np.float32)
    a = src.file_time(start_s)
    lead = max(0, round(-a * sr))
    if lead >= n or not probe(src.path).has_audio:
        return out
    seek = ["-ss", f"{a:.6f}"] if a > 0 else []
    args = [ffmpeg_bin(), *QUIET, *seek, "-t", f"{(n - lead) / sr:.6f}"]
    args += ["-i", str(Path(src.path).absolute()), "-map", "0:a:0"]
    pcm = _pcm(run([*args, *_raw_out(sr)], timeout_s=TIMEOUT_S).stdout)
    m = min(len(pcm), n - lead)
    out[lead : lead + m] = pcm[:m]
    return out


def assemble(pcm: np.ndarray, base_s: float, edl: EDL, sr: int = SR) -> np.ndarray:
    """The clip's sound in output time, exactly ``edl.duration`` long: each segment's source audio (``pcm``
    starts at source time ``base_s``) at its speed with the pitch kept, joined by XFADE_S equal-power
    crossfades centred on the joins, faded in over FADE_IN_S and out over the clip's last ``audio_fade_s``."""
    n = round(edl.duration * sr)
    out = np.zeros((n, 2), np.float64)
    segs = edl.segments
    pos = [round(s.out_start * sr) for s in segs] + [n]
    h = round(XFADE_S * sr / 2)
    i = 0
    while i < len(segs):
        j = i + 1
        while j < len(segs) and segs[j].speed == segs[i].speed:
            j += 1
        hin, hout = (h if i > 0 else 0), (h if j < len(segs) else 0)
        piece = _run(pcm, base_s, segs[i:j], pos[i : j + 1], hin, hout, sr)
        if hin:
            piece[: 2 * hin] *= _ramp(2 * hin)
        if hout:
            piece[-2 * hout :] *= _ramp(2 * hout)[::-1]
        a = pos[i] - hin
        lo, hi = max(a, 0), min(a + len(piece), n)
        out[lo:hi] += piece[lo - a : hi - a]
        i = j
    m = min(n, round(FADE_IN_S * sr))
    out[:m] *= np.linspace(0.0, 1.0, m, endpoint=False)[:, None]
    m = min(n, round(edl.audio_fade_s * sr))
    out[n - m :] *= np.linspace(1.0, 0.0, m)[:, None]
    return out.astype(np.float32)


def _run(
    pcm: np.ndarray, base_s: float, segs: Sequence[Segment], pos: Sequence[int], hin: int, hout: int, sr: int
) -> np.ndarray:
    """A run of segments at one speed, from ``hin`` samples before its start to ``hout`` after its end in
    output time: joined at the source's pace, then stretched to the run's speed."""
    speed = segs[0].speed
    h = round(XFADE_S * sr / 2)
    if speed == 1.0:
        lead, tail, hs = hin, hout, h
    else:
        lead = tail = round(PREROLL_S * sr)
        hs = round(h * speed)
    lens = [round((pos[k + 1] - pos[k]) * speed) for k in range(len(segs))]
    joined = np.zeros((lead + sum(lens) + tail, 2), np.float64)
    at = lead
    for k, seg in enumerate(segs):
        a = round((seg.src_start - base_s) * sr)
        pre = lead if k == 0 else hs
        post = tail if k == len(segs) - 1 else hs
        x = _take(pcm, a - pre, a + lens[k] + post).astype(np.float64)
        if k > 0:
            x[: 2 * hs] *= _ramp(2 * hs)
        if k < len(segs) - 1:
            x[-2 * hs :] *= _ramp(2 * hs)[::-1]
        joined[at - pre : at + lens[k] + post] += x
        at += lens[k]
    if speed == 1.0:
        return joined
    stretched = _stretch(joined, speed, sr).astype(np.float64)
    start = round(lead / speed) - hin
    return _take(stretched, start, start + hin + pos[-1] - pos[0] + hout)


def _stretch(x: np.ndarray, speed: float, sr: int) -> np.ndarray:
    """``x`` played ``speed`` times as fast with its pitch kept (ffmpeg's atempo)."""
    args = [ffmpeg_bin(), *QUIET, *_raw_in(sr), "-af", f"atempo={speed:.6f}", *_raw_out(sr)]
    return _pcm(run(args, timeout_s=TIMEOUT_S, stdin=_bytes(x)).stdout)


def _ramp(m: int) -> np.ndarray:
    """An equal-power fade in over ``m`` samples; reversed, it is the matching fade out."""
    return np.sin(0.5 * np.pi * (np.arange(m) + 0.5) / m)[:, None]


def loudness(pcm: np.ndarray, sr: int = SR, *, block_s: float = 0.4) -> float:
    """The BS.1770-4 integrated loudness of ``pcm`` (frames x channels) in LUFS, over ``block_s`` blocks
    overlapping by 75 %, with the absolute and the relative gate; -inf when no block clears the first."""
    n = len(pcm)
    if n == 0:
        return -math.inf
    power = np.square(_k_weighted(pcm, sr)).sum(axis=1)
    nb = max(1, min(n, round(block_s * sr)))
    starts = np.arange(0, n - nb + 1, max(1, nb // 4))
    total = np.concatenate([[0.0], np.cumsum(power)])
    z = (total[starts + nb] - total[starts]) / nb
    z = z[z > ABS_GATE]
    if not z.size:
        return -math.inf
    z = z[z > 0.1 * z.mean()]
    return -0.691 + 10 * math.log10(float(z.mean()))


def _k_weighted(x: np.ndarray, sr: int) -> np.ndarray:
    """``x`` through the K filter's magnitude response (its phase does not change a block's power)."""
    n = len(x)
    size = min(K_SIZE, 1 << (n + 2 * K_PAD - 1).bit_length())
    chunk = size - 2 * K_PAD
    gain = _k_gain(size, sr)
    out = np.empty(x.shape, np.float64)
    for i in range(0, n, chunk):
        y = np.fft.irfft(np.fft.rfft(_take(x, i - K_PAD, i + chunk + K_PAD), axis=0) * gain, size, axis=0)
        out[i : i + chunk] = y[K_PAD : K_PAD + min(chunk, n - i)]
    return out


@lru_cache(maxsize=8)
def _k_gain(size: int, sr: int) -> np.ndarray:
    w = np.exp(-2j * np.pi * np.fft.rfftfreq(size))  # z to the minus one, around the unit circle
    k = math.tan(math.pi * SHELF_F0 / sr)
    vh = 10 ** (SHELF_G / 20)
    vb = vh**SHELF_VB
    a0 = 1 + k / SHELF_Q + k * k
    b = [(vh - vb * k / SHELF_Q + k * k) / a0, 2 * (k * k - vh) / a0, (vh + vb * k / SHELF_Q + k * k) / a0]
    shelf = np.polyval(b, w) / np.polyval([(1 - k / SHELF_Q + k * k) / a0, 2 * (k * k - 1) / a0, 1.0], w)
    k = math.tan(math.pi * HIGHPASS_F0 / sr)
    d = 1 + k / HIGHPASS_Q + k * k
    a = [(1 - k / HIGHPASS_Q + k * k) / d, 2 * (k * k - 1) / d, 1.0]
    high = np.polyval([1.0, -2.0, 1.0], w) / np.polyval(a, w)
    gain = np.abs(shelf * high)[:, None]
    gain.flags.writeable = False
    return gain


def clean_speech(pcm: np.ndarray, sr: int = SR) -> np.ndarray:
    """The speech with its rumble cut (80 Hz high-pass), its loud moments brought down gently (3:1 above
    -18 dB once it sits at PRE_LUFS) and its harsh esses softened. Silence comes back as it went in."""
    level = loudness(pcm, sr)
    if level < SILENT_LUFS:
        return pcm
    chain = f"volume={PRE_LUFS - level:.2f}dB,highpass=f=80,{COMPRESS},{DEESS}"
    args = [ffmpeg_bin(), *QUIET, *_raw_in(sr), "-af", chain, *_raw_out(sr)]
    y = _pcm(run(args, timeout_s=TIMEOUT_S, stdin=_bytes(pcm)).stdout)
    return _take(y, 0, len(pcm))


def mix_sfx(
    pcm: np.ndarray, cues: Iterable[dict[str, Any]], sr: int = SR, *, fade_s: float = FADE_S
) -> np.ndarray:
    """``pcm`` with each cue's effect from its ``at`` on both channels, SFX_UNDER_DB under the speech in
    the AROUND_S either side of it, and faded with the clip over its last ``fade_s``. An effect with only
    silence around it (nothing over the loudness gate at -70 LUFS) comes out silent."""
    out = np.array(pcm, np.float32)
    n = len(out)
    fade_from = n - min(n, round(fade_s * sr))
    fade = np.linspace(1.0, 0.0, n - fade_from)
    reach = round(AROUND_S * sr)
    for cue in cues:
        fx, level = _effect(cue["kind"], sr)
        i = max(0, round(cue["at"] * sr))
        if i >= n:
            continue
        j = min(n, i + len(fx))
        around = loudness(pcm[max(0, i - reach) : j + reach], sr, block_s=SFX_BLOCK_S)
        x = fx[: j - i] * 10 ** ((around - SFX_UNDER_DB - level) / 20)
        if j > fade_from:
            k = max(i, fade_from)
            x[k - i :] *= fade[k - fade_from : j - fade_from]
        out[i:j] += x[:, None]
    return out


def decode_track(path: Path, n: int, sr: int = SR) -> np.ndarray:
    """The track as ``n`` frames x 2 float32 at ``sr``: cut to length when it is long enough, looped when
    it isn't, each loop crossfading into the next over SEAM_S (equal power) so the seam doesn't click. Only
    as much of the file as the clip needs is decoded."""
    args = [ffmpeg_bin(), *QUIET, "-t", f"{n / sr + 0.1:.6f}"]  # a little over, so rounding never loops it
    args += ["-i", str(Path(path).absolute()), "-map", "0:a:0", *_raw_out(sr)]
    x = np.array(_pcm(run(args, timeout_s=TIMEOUT_S).stdout), np.float32)
    m = len(x)
    if m >= n or m == 0:
        return _take(x, 0, n)
    seam = min(round(SEAM_S * sr), m // 2)
    fade = _ramp(seam).astype(np.float32)
    out = np.zeros((n + m, 2), np.float32)
    for i in range(0, n, m - seam):
        piece = x.copy()
        if i:
            piece[:seam] *= fade
        piece[m - seam :] *= fade[::-1]
        out[i : i + m] += piece
    return out[:n]


def decode_take(path: Path, max_s: float, sr: int = SR) -> np.ndarray:
    """The file's first sound stream as frames x 2 float32 at ``sr``, no more than ``max_s`` seconds of it.
    ToolError when ffmpeg can't read one."""
    args = [ffmpeg_bin(), *QUIET, "-t", f"{max_s:.6f}", "-i", str(Path(path).absolute()), "-map", "0:a:0"]
    return np.array(_pcm(run([*args, *_raw_out(sr)], timeout_s=TIMEOUT_S).stdout), np.float32)


def mix_music(speech: np.ndarray, bed: np.ndarray, sr: int = SR, *, under_db: float = -12.0) -> np.ndarray:
    """``speech`` with ``bed`` under it: the bed levelled to ``under_db`` under the speech's loudness (to
    BED_LUFS when there is no speech), a further DUCK_DB down while someone speaks, faded in over FADE_IN_S
    and out over the last MUSIC_FADE_S. A bed with nothing over the loudness gate adds nothing."""
    out = np.array(speech, np.float32)
    n = len(out)
    bed = _take(np.asarray(bed, np.float64), 0, n)
    have = loudness(bed, sr)
    if n == 0 or have < SILENT_LUFS:
        return out
    level = loudness(speech, sr)
    talking = level >= SILENT_LUFS
    x = bed * 10 ** (((level + under_db if talking else BED_LUFS) - have) / 20)
    if talking:
        x *= _duck(speech, level, sr)[:, None]
    k = min(n, round(FADE_IN_S * sr))
    x[:k] *= np.linspace(0.0, 1.0, k)[:, None]
    m = min(n, round(MUSIC_FADE_S * sr))
    x[n - m :] *= np.linspace(1.0, 0.0, m)[:, None]
    return (out + x).astype(np.float32)


def mix_voiceover(speech: np.ndarray, take: np.ndarray, at_s: float, sr: int = SR) -> np.ndarray:
    """``speech`` with ``take`` played from ``at_s`` seconds in and cut at its end: the take levelled to the
    speech's loudness (to PRE_LUFS when there is no speech) and the speech VO_DUCK_DB down while it talks,
    moving as the music's duck does. A take with nothing over the loudness gate adds nothing."""
    out = np.array(speech, np.float32)
    n = len(out)
    start = min(n, max(0, round(at_s * sr)))
    placed = np.zeros((n, 2), np.float64)
    part = np.asarray(take, np.float64)[: n - start]
    placed[start : start + len(part)] = part
    have = loudness(placed, sr)
    if n == 0 or have < SILENT_LUFS:
        return out
    level = loudness(speech, sr)
    placed *= 10 ** (((level if level >= SILENT_LUFS else PRE_LUFS) - have) / 20)
    gain = _duck(placed, loudness(placed, sr), sr, depth_db=VO_DUCK_DB)
    return (out * gain[:, None] + placed).astype(np.float32)


def _duck(speech: np.ndarray, level: float, sr: int, *, depth_db: float = DUCK_DB) -> np.ndarray:
    """The music's gain at each sample: 1 where no one speaks and ``depth_db`` down where someone does, judged
    from the speech's K-weighted loudness over 50 ms at 1 ms steps, moving down within DUCK_ATTACK_S and
    back up within DUCK_RELEASE_S."""
    n = len(speech)
    hop = max(1, round(sr / 1000))
    k = -(-n // hop)
    power = _take(np.square(_k_weighted(speech, sr)).sum(axis=1), 0, k * hop).reshape(k, hop).mean(axis=1)
    width = min(k, 50)
    near = np.convolve(power, np.ones(width) / width, mode="same")
    speaking = -0.691 + 10 * np.log10(np.maximum(near, 1e-20)) >= level - SPEAKING_DB
    down, up = hop / sr / DUCK_ATTACK_S, hop / sr / DUCK_RELEASE_S
    depth = np.empty(k)
    at = 1.0 if speaking[0] else 0.0  # a clip that opens on speech starts ducked
    for i, on in enumerate(speaking.tolist()):
        at = min(1.0, at + down) if on else max(0.0, at - up)
        depth[i] = at
    centres = (np.arange(k) + 0.5) * hop
    return 10 ** (-depth_db * np.interp(np.arange(n), centres, depth) / 20)


@lru_cache(maxsize=16)
def _effect(kind: Kind, sr: int) -> tuple[np.ndarray, float]:
    """The effect, and its loudness on both channels over blocks short enough for it."""
    fx = synth(kind, sr)
    fx.flags.writeable = False
    return fx, loudness(np.stack([fx, fx], axis=1), sr, block_s=min(SFX_BLOCK_S, len(fx) / sr))


def finish(pcm: np.ndarray, sr: int, out: Path) -> AudioReport:
    """``pcm`` levelled by two-pass loudnorm (I -14, TP -1.5, LRA 11, linear when the peak allows) and
    written to ``out`` as AAC-LC at 128 kbps, 48 kHz stereo. Audio under SILENT_LUFS is written as it is
    and reported silent, so silence is never pumped up into noise."""
    data = _bytes(pcm)
    first = _loudnorm_stats(_raw_in(sr), data)
    silent = float(first["input_i"]) < SILENT_LUFS
    chain = [] if silent else ["-af", f"{_second_pass(first)},{RETIME}"]
    args = [ffmpeg_bin(), *QUIET, *_raw_in(sr), *chain, *AAC, str(Path(out).absolute())]
    run(args, timeout_s=TIMEOUT_S, stdin=data)
    done = _loudnorm_stats(["-i", str(Path(out).absolute())], None)
    return AudioReport(
        input_lufs=_db(first["input_i"]),
        output_lufs=_db(done["input_i"]),
        true_peak=_db(done["input_tp"]),
        silent=silent,
    )


def _second_pass(first: dict[str, str]) -> str:
    """The loudnorm that applies the first pass's measures, linear whenever the measured peak allows."""
    measured = {
        "measured_I": _clamp(first["input_i"], -99, 0),
        "measured_TP": _clamp(first["input_tp"], -99, 99),
        "measured_LRA": _clamp(first["input_lra"], 0, 99),
        "measured_thresh": _clamp(first["input_thresh"], -99, 0),
        "offset": _clamp(first["target_offset"], -99, 99),
    }
    return f"loudnorm={TARGET}:" + ":".join(f"{k}={v}" for k, v in measured.items()) + ":linear=true"


def _loudnorm_stats(inputs: list[str], stdin: bytes | None) -> dict[str, str]:
    """What loudnorm measures of ``inputs``: its JSON summary, the last thing it writes to stderr."""
    args = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-nostats", "-loglevel", "info", *inputs]
    args += ["-af", f"loudnorm={TARGET}:print_format=json", "-f", "null", "-"]
    err = run(args, timeout_s=TIMEOUT_S, stdin=stdin).stderr.decode("utf-8", "replace")
    start = err.rfind("{")
    end = err.find("}", start)
    try:
        stats = json.loads(err[start : end + 1]) if start >= 0 and end > start else None
    except ValueError:
        stats = None
    if not isinstance(stats, dict) or not {"input_i", "input_tp", "input_lra"} <= stats.keys():
        raise ToolError(
            "loudnorm_failed",
            "ffmpeg could not measure how loud this clip is. Try again; the details show what it said.",
            details=err[-2000:],
        )
    return stats


def _clamp(value: str, lo: float, hi: float) -> str:
    return f"{min(hi, max(lo, float(value))):.2f}"


def _db(value: str) -> float:
    """A level from ffmpeg, silence (-inf) written as FLOOR_DB."""
    v = float(value)
    return FLOOR_DB if math.isnan(v) else round(max(FLOOR_DB, v), 2)


def _take(x: np.ndarray, a: int, b: int) -> np.ndarray:
    """``x[a:b]`` with zeros wherever that runs past either end of ``x``."""
    out = np.zeros((max(0, b - a), *x.shape[1:]), x.dtype)
    lo, hi = max(a, 0), min(b, len(x))
    if hi > lo:
        out[lo - a : hi - a] = x[lo:hi]
    return out


def _bytes(pcm: np.ndarray) -> bytes:
    return np.ascontiguousarray(pcm, "<f4").tobytes()


def _pcm(data: bytes) -> np.ndarray:
    return np.frombuffer(data, "<f4").reshape(-1, 2)


def _raw_in(sr: int) -> list[str]:
    return ["-f", "f32le", "-ar", str(sr), "-ac", "2", "-i", "pipe:0"]


def _raw_out(sr: int) -> list[str]:
    return ["-f", "f32le", "-c:a", "pcm_f32le", "-ac", "2", "-ar", str(sr), "pipe:1"]
