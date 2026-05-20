"""Fault injection + link degradation (DESIGN.md §6)."""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np

from .frame import Frame
from .const import FS


class FaultSpec:
    """Base fault: apply(frame) -> frame.

    stage="post" (default): signal degradation at the block OUTPUT — also works for
        source blocks, whose dsp() generates samples from scratch.
    stage="pre": block behavior change that dsp() reads from meta (e.g. no_loop_current).
    """
    name = "fault"
    stage = "post"

    def apply(self, frame: Frame) -> Frame:
        return frame


@dataclass
class WeakTone(FaultSpec):
    gain_dB: float = -20.0
    name: str = "weak_tone"

    def apply(self, frame: Frame) -> Frame:
        g = 10 ** (self.gain_dB / 20.0)
        f = frame.copy()
        f.samples = (f.samples * g).astype(np.float32)
        return f


@dataclass
class LineNoise(FaultSpec):
    snr_dB: float = 10.0
    name: str = "line_noise"

    def apply(self, frame: Frame) -> Frame:
        f = frame.copy()
        sig_pow = float(np.mean(f.samples ** 2)) + 1e-12
        noise_pow = sig_pow / (10 ** (self.snr_dB / 10.0))
        rng = np.random.default_rng(f.seq)  # deterministic per frame
        noise = rng.normal(0, np.sqrt(noise_pow), size=f.samples.shape)
        f.samples = (f.samples + noise).astype(np.float32)
        f.meta["snr_dB"] = self.snr_dB
        return f


@dataclass
class Hum50Hz(FaultSpec):
    level: float = 0.1
    name: str = "hum_50hz"

    def apply(self, frame: Frame) -> Frame:
        f = frame.copy()
        n = len(f.samples)
        t0 = f.seq * n
        t = (t0 + np.arange(n)) / FS
        f.samples = (f.samples + self.level * np.sin(2 * np.pi * 50 * t)).astype(np.float32)
        return f


@dataclass
class NoLoopCurrent(FaultSpec):
    name: str = "no_loop_current"
    stage: str = "pre"        # read by AnalogLine.dsp from meta

    def apply(self, frame: Frame) -> Frame:
        f = frame.copy()
        f.meta["force_no_loop"] = True
        return f


@dataclass
class PacketLoss(FaultSpec):
    pct: float = 5.0
    name: str = "packet_loss"
    stage: str = "pre"        # CodecRTP.dsp reads meta["rtp_drop"]

    def apply(self, frame: Frame) -> Frame:
        f = frame.copy()
        rng = np.random.default_rng(f.seq)   # deterministic per frame
        if rng.random() < self.pct / 100.0:
            f.meta["rtp_drop"] = True
        return f


@dataclass
class Jitter(FaultSpec):
    ms: float = 60.0
    name: str = "jitter"
    stage: str = "pre"

    def apply(self, frame: Frame) -> Frame:
        f = frame.copy()
        f.meta["rtp_jitter_ms"] = self.ms
        return f


class Impairment:
    """Degradation on a link (patch) — DESIGN.md §6, patch table."""
    def __init__(self, delay_ms=0.0, atten_dB=0.0, noise_snr=None, loss_pct=0.0):
        self.delay_ms = delay_ms
        self.atten_dB = atten_dB
        self.noise_snr = noise_snr
        self.loss_pct = loss_pct

    def apply(self, frame: Frame) -> Frame:
        f = frame.copy()
        if self.atten_dB:
            f.samples = (f.samples * 10 ** (-self.atten_dB / 20.0)).astype(np.float32)
        return f
