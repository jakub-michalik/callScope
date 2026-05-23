"""Signal generators + framize for tests (DESIGN.md §13a)."""
from __future__ import annotations
import numpy as np

from engine.const import FS, FRAME_N
from engine.frame import Frame
from dsp.tone_gen import dtmf_samples, pure_tone, silence


def dual_tone(digit: str, dur_ms: float = 100.0, level: float = 0.5,
              twist_dB: float = 0.0) -> np.ndarray:
    return dtmf_samples(digit, dur_ms=dur_ms, level=level, twist_dB=twist_dB)


def white_noise(dur_ms: float = 100.0, level: float = 0.1, seed: int = 0) -> np.ndarray:
    n = int(round(dur_ms / 1000.0 * FS))
    rng = np.random.default_rng(seed)
    return (level * rng.standard_normal(n)).astype(np.float32)


def speech_like(dur_ms: float = 200.0, seed: int = 1) -> np.ndarray:
    """Wideband signal (several formants + noise) — should not yield a digit."""
    n = int(round(dur_ms / 1000.0 * FS))
    t = np.arange(n) / FS
    rng = np.random.default_rng(seed)
    sig = (0.3 * np.sin(2 * np.pi * 300 * t)
           + 0.25 * np.sin(2 * np.pi * 1100 * t)
           + 0.2 * np.sin(2 * np.pi * 2400 * t)
           + 0.2 * rng.standard_normal(n))
    return sig.astype(np.float32)


def framize(samples: np.ndarray, seq0: int = 0, t0: float = 0.0,
            meta: dict | None = None) -> list[Frame]:
    """Cuts the signal into FRAME_N frames, padding the last one with zeros."""
    frames = []
    seq = seq0
    for start in range(0, len(samples), FRAME_N):
        chunk = samples[start:start + FRAME_N]
        if len(chunk) < FRAME_N:
            chunk = np.concatenate([chunk, np.zeros(FRAME_N - len(chunk), np.float32)])
        t = t0 + seq * FRAME_N / FS
        frames.append(Frame(seq, t, np.asarray(chunk, np.float32), dict(meta or {})))
        seq += 1
    return frames


def silence_frames(count: int, seq0: int = 0, t0: float = 0.0) -> list[Frame]:
    return framize(silence(dur_ms=count * FRAME_N / FS * 1000.0), seq0, t0)
