"""Signal metrics: rms, dBm0, twist (DESIGN.md §5.3, §7)."""
from __future__ import annotations
import numpy as np

EPS = 1e-12


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.asarray(x, dtype=float) ** 2) + EPS))


def db(value: float) -> float:
    """Level in dB relative to 1.0 (full scale)."""
    return 20.0 * np.log10(max(value, EPS))


def twist_db(e_low: float, e_high: float) -> float:
    """Twist [dB] = 10log10(E_low/E_high). Positive => low group louder."""
    return 10.0 * np.log10((e_low + EPS) / (e_high + EPS))
