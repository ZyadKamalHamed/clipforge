"""Sound effects (SPEC 8.5), synthesised by ClipForge so none of them is anyone else's: a whoosh, a pop, a
soft hit, a riser and a click, the same every time, each peaking at full scale so the mix sets its level."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

import numpy as np

Kind = Literal["whoosh", "pop", "hit", "riser", "click"]
KINDS: tuple[Kind, ...] = ("whoosh", "pop", "hit", "riser", "click")
LENGTH_S = {"whoosh": 0.45, "pop": 0.12, "hit": 0.5, "riser": 1.2, "click": 0.015}
WHOOSH_TOP = 0.6  # the whoosh is loudest this far through
PEAK_S = {  # from an effect's start to its loudest point, the moment it marks
    "whoosh": WHOOSH_TOP * LENGTH_S["whoosh"],
    "pop": 0.0,
    "hit": 0.0,
    "riser": LENGTH_S["riser"],
    "click": 0.0,
}
SEED = 85  # each kind's noise comes from SEED plus its place in KINDS
EDGE_S = 0.002  # raised cosine edges, so each starts and ends at silence


def synth(kind: Kind, sr: int = 48000) -> np.ndarray:
    """``kind`` as mono float32 at ``sr``: LENGTH_S long, the same on every call, peaking at exactly 1.0, and
    starting and ending at silence."""
    if kind not in KINDS:
        raise ValueError(f"no sound effect called {kind!r}")
    n = round(LENGTH_S[kind] * sr)
    t = np.arange(n) / sr
    rng = np.random.default_rng(SEED + KINDS.index(kind))
    x = MAKERS[kind](t, sr, rng) * edges(n, sr)
    return (x / np.abs(x).max()).astype(np.float32)


def edges(n: int, sr: int) -> np.ndarray:
    m = min(round(EDGE_S * sr), n // 2)
    w = np.ones(n)
    ramp = 0.5 - 0.5 * np.cos(np.pi * np.arange(m) / m)
    w[:m], w[n - m :] = ramp, ramp[::-1]
    return w


def _whoosh(t: np.ndarray, sr: int, rng: np.random.Generator) -> np.ndarray:
    """Air: noise through a band that sweeps up to 2.5 kHz and back down, swelling to its top."""
    u = t / LENGTH_S["whoosh"]
    up = u < WHOOSH_TOP
    down = (u - WHOOSH_TOP) / (1 - WHOOSH_TOP)
    fc = np.where(up, 300 * (2500 / 300) ** (u / WHOOSH_TOP), 2500 * (900 / 2500) ** down)
    env = np.where(up, np.sin(np.pi / 2 * u / WHOOSH_TOP) ** 2, np.cos(np.pi / 2 * down) ** 2)
    return band(rng.standard_normal(len(t)), fc, 1.2, sr) * env


def _pop(t: np.ndarray, sr: int, rng: np.random.Generator) -> np.ndarray:
    """A bubble: a tone falling from 1.1 kHz to 450 Hz as it dies away, with a tick at the front."""
    f = 450 + 650 * np.exp(-t / 0.012)
    tone = np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-t / 0.025)
    return tone + 0.15 * rng.standard_normal(len(t)) * np.exp(-t / 0.002)


def _hit(t: np.ndarray, sr: int, rng: np.random.Generator) -> np.ndarray:
    """A soft thump: a low tone dropping from 140 Hz to 55 Hz, with a dull knock on top."""
    f = 55 + 85 * np.exp(-t / 0.03)
    body = np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-t / 0.12)
    knock = lowpass(rng.standard_normal(len(t)), 2000, sr) * np.exp(-t / 0.015)
    return body + 0.4 * knock


def _riser(t: np.ndarray, sr: int, rng: np.random.Generator) -> np.ndarray:
    """A build: noise sweeping from 300 Hz to 6 kHz over a tone climbing two octaves, louder to the end."""
    u = t / LENGTH_S["riser"]
    noise = band(rng.standard_normal(len(t)), 300 * 20**u, 2.0, sr)
    tone = np.sin(2 * np.pi * np.cumsum(200 * 4**u) / sr)
    return (noise + 0.3 * tone) * u**2 * np.minimum(1.0, (LENGTH_S["riser"] - t) / 0.03)


def _click(t: np.ndarray, sr: int, rng: np.random.Generator) -> np.ndarray:
    """A tick: a short 2.5 kHz ping with a little noise."""
    return np.sin(2 * np.pi * 2500 * t) * np.exp(-t / 0.002) + 0.2 * rng.standard_normal(len(t)) * np.exp(
        -t / 0.001
    )


MAKERS: dict[str, Callable[[np.ndarray, int, np.random.Generator], np.ndarray]] = {
    "whoosh": _whoosh,
    "pop": _pop,
    "hit": _hit,
    "riser": _riser,
    "click": _click,
}


def band(x: np.ndarray, fc: np.ndarray, q: float, sr: int) -> np.ndarray:
    """``x`` through a band pass with unity gain at its centre, which follows ``fc`` sample by sample (a
    state variable filter in its trapezoidal form, stable at any centre)."""
    k = 1.0 / q
    g = np.tan(np.pi * np.minimum(fc, 0.45 * sr) / sr)
    a1 = 1.0 / (1.0 + g * (g + k))
    a2 = g * a1
    a3 = g * a2
    out = []
    s1 = s2 = 0.0
    for v0, c1, c2, c3 in zip(x.tolist(), a1.tolist(), a2.tolist(), a3.tolist(), strict=True):
        v3 = v0 - s2
        v1 = c1 * s1 + c2 * v3
        v2 = s2 + c2 * s1 + c3 * v3
        s1, s2 = 2 * v1 - s1, 2 * v2 - s2
        out.append(k * v1)
    return np.array(out)


def lowpass(x: np.ndarray, fc: float, sr: int) -> np.ndarray:
    """``x`` through a gentle 6 dB per octave low pass at ``fc``."""
    f = np.fft.rfftfreq(len(x), 1 / sr)
    return np.fft.irfft(np.fft.rfft(x) / np.sqrt(1 + (f / fc) ** 2), len(x))
