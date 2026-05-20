"""DTMF tone synthesis (DESIGN.md §5.1)."""
from __future__ import annotations
import numpy as np

from engine.const import FS, DTMF_TONE


def dtmf_samples(digit: str, dur_ms: float = 100.0, level: float = 0.5,
                 twist_dB: float = 0.0, ramp_ms: float = 5.0,
                 fs: int = FS) -> np.ndarray:
    """Generates DTMF tone samples for a digit.

    twist_dB: positive -> high group louder (forward twist).
    ramp_ms: envelope ramp (anti-click).
    """
    if digit not in DTMF_TONE:
        raise ValueError(f"unknown DTMF digit: {digit!r}")
    f_low, f_high = DTMF_TONE[digit]
    n = int(round(dur_ms / 1000.0 * fs))
    t = np.arange(n) / fs

    g_high = 10 ** (twist_dB / 20.0)
    sig = level * (np.sin(2 * np.pi * f_low * t) + g_high * np.sin(2 * np.pi * f_high * t))

    # envelope with ramp (raised-cosine at the edges)
    r = int(round(ramp_ms / 1000.0 * fs))
    if r > 0 and 2 * r < n:
        ramp = 0.5 * (1 - np.cos(np.pi * np.arange(r) / r))
        env = np.ones(n)
        env[:r] = ramp
        env[-r:] = ramp[::-1]
        sig = sig * env
    return sig.astype(np.float32)


def pure_tone(freq: float, dur_ms: float = 100.0, level: float = 0.5,
              fs: int = FS) -> np.ndarray:
    n = int(round(dur_ms / 1000.0 * fs))
    t = np.arange(n) / fs
    return (level * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def silence(dur_ms: float = 100.0, fs: int = FS) -> np.ndarray:
    n = int(round(dur_ms / 1000.0 * fs))
    return np.zeros(n, dtype=np.float32)
