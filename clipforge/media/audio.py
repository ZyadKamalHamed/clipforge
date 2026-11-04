"""Loudness per second and percentile ranks, for the delivery energy signal (SPEC 7.4)."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
_BLOCK_S = 600  # seconds squared at a time, so a six hour source stays small in memory


def read_wav16k(path: Path, *, start: float = 0.0, end: float | None = None) -> np.ndarray:
    """The samples of a 16 kHz mono 16 bit WAV, as float32 in -1..1, from ``start`` to ``end`` seconds
    (the whole file by default). A stretch running past the end of the file stops where the file does."""
    with wave.open(str(path), "rb") as wav:
        layout = (wav.getframerate(), wav.getnchannels(), wav.getsampwidth())
        if layout != (SAMPLE_RATE, 1, 2):
            raise ValueError(f"{path} is not 16 kHz mono 16 bit audio (rate, channels, bytes: {layout})")
        total = wav.getnframes()
        first = min(max(round(start * SAMPLE_RATE), 0), total)
        last = total if end is None else min(max(round(end * SAMPLE_RATE), first), total)
        wav.setpos(first)
        frames = wav.readframes(last - first)
    samples = np.frombuffer(frames, dtype="<i2").astype(np.float32)
    samples /= 32768.0
    return samples


def write_wav16k(path: Path, samples: np.ndarray) -> Path:
    """Samples in -1..1 as a 16 kHz mono 16 bit WAV, the inverse of read_wav16k, so a stretch read and
    written back keeps its exact samples. Anything outside -1..1 is clipped."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(np.round(np.asarray(samples, dtype=np.float64) * 32768.0), -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm.tobytes())
    return path


def wav_seconds(path: Path) -> float:
    """How long a WAV lasts, from its header."""
    with wave.open(str(path), "rb") as wav:
        return wav.getnframes() / wav.getframerate()


def rms_per_second(samples: np.ndarray, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Root mean square of every started second; a short last second is measured on its own samples."""
    count = -(-len(samples) // sr)
    rms = np.zeros(count)
    for first in range(0, count, _BLOCK_S):
        block = np.asarray(samples[first * sr : (first + _BLOCK_S) * sr], dtype=np.float64)
        squares = block * block
        whole = len(block) // sr
        rms[first : first + whole] = np.sqrt(squares[: whole * sr].reshape(whole, sr).mean(axis=1))
        if whole * sr < len(block):
            rms[first + whole] = np.sqrt(squares[whole * sr :].mean())
    return rms


def percentile_ranks(values) -> np.ndarray:
    """Where each value sits among all of them: 0 for the lowest, 1 for the highest, ties share
    their average rank, and a single value sits in the middle."""
    values = np.asarray(values, dtype=np.float64)
    n = len(values)
    if n < 2:
        return np.full(n, 0.5)
    _, inverse, counts = np.unique(values, return_inverse=True, return_counts=True)
    first = np.concatenate(([0], np.cumsum(counts)[:-1]))
    return (first + (counts - 1) / 2)[inverse] / (n - 1)
