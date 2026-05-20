"""Goertzel filter bank for the 8 DTMF frequencies (DESIGN.md §3, §5.3)."""
from __future__ import annotations
import numpy as np

from engine.const import FS, DTMF_WIN_N, DTMF_FREQS


class GoertzelBank:
    """Computes energy at the 8 DTMF frequencies over a window of N samples.

    Generalized Goertzel: the resonance coefficient is computed directly from the
    frequency (omega = 2π·f/fs), so the filter is EXACTLY tuned to each DTMF
    frequency — without the error of rounding the bin to an integer k (important for
    twist/energy, especially for a microphone signal).
    """

    def __init__(self, freqs=DTMF_FREQS, n: int = DTMF_WIN_N, fs: int = FS):
        self.freqs = tuple(freqs)
        self.n = n
        self.fs = fs
        self.omega = 2 * np.pi * np.array(self.freqs, dtype=float) / fs
        self.coeff = 2 * np.cos(self.omega)          # [8] — exact tuning

    def energy(self, x: np.ndarray) -> np.ndarray:
        """Returns [8] energies for window x (length >= n; takes the last n samples)."""
        if len(x) < self.n:
            xx = np.zeros(self.n, dtype=float)
            xx[-len(x):] = x
        else:
            xx = np.asarray(x[-self.n:], dtype=float)

        # 2nd-order IIR filter per frequency, vectorized over 8 channels
        s_prev = np.zeros(8)
        s_prev2 = np.zeros(8)
        for sample in xx:
            s = sample + self.coeff * s_prev - s_prev2
            s_prev2 = s_prev
            s_prev = s
        power = s_prev ** 2 + s_prev2 ** 2 - self.coeff * s_prev * s_prev2
        return np.maximum(power, 0.0)

    def magnitudes(self, x: np.ndarray) -> dict:
        """Map {freq_Hz: magnitude} for the spectrum preview."""
        e = self.energy(x)
        mag = np.sqrt(e)
        return {int(f): float(m) for f, m in zip(self.freqs, mag)}
