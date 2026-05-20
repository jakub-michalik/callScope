"""Block + Detector contract (DESIGN.md §4)."""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np

from .frame import Frame
from .const import DTMF_FREQS


class Detector:
    """Local detector of block problems. check() -> list[Diagnostic]."""

    def check(self, frame_in: Frame, frame_out: Frame) -> list:
        return []

    def reset(self) -> None:
        pass


@dataclass
class TapData:
    """Block preview for UI (DESIGN.md §5)."""
    waveform: np.ndarray | None = None    # scope
    spectrum: dict | None = None          # {freq: magnitude}
    metrics: dict | None = None           # gauges/readouts


class Block:
    """Base block: fault -> DSP -> detector -> tap."""

    name: str = "BLOCK"
    PLANE: str = "media"               # "media" | "control" (layout, Phase B)
    # injectable faults: {type: (FaultSpecClass, kwargs, label)}
    FAULTS: dict = {}

    def __init__(self, name: str | None = None):
        if name:
            self.name = name
        self.enabled: bool = True
        self.fault = None                  # FaultSpec | None
        self.detector: Detector = Detector()
        self._last_in: Frame | None = None
        self._last_out: Frame | None = None

    # --- conditions: currently-active level conditions for the correlator snapshot ---
    def conditions(self, t: float) -> list:
        return []

    # --- faults declared by the block (one place to add a fault) ---
    def fault_menu(self) -> list:
        return [{"type": k, "label": v[2]} for k, v in self.FAULTS.items()]

    def set_fault(self, ftype: str) -> bool:
        spec = self.FAULTS.get(ftype)
        if not spec:
            return False
        cls, kw, _ = spec
        self.fault = cls(**kw)
        return True

    def clear_fault(self) -> None:
        self.fault = None

    # --- separate DSP path (overridden by a concrete block) ---
    def dsp(self, frame: Frame) -> Frame:
        return frame

    # --- full block run: (fault pre) -> dsp -> (fault post) ---
    def process(self, frame: Frame) -> Frame:
        self._last_in = frame
        f = frame
        # "pre" faults (behavior change, read by dsp from meta) — e.g. no_loop_current
        if self.fault is not None and getattr(self.fault, "stage", "post") == "pre":
            f = self.fault.apply(f)
        out = self._bypass(f) if not self.enabled else self.dsp(f)
        # "post" faults (signal degradation at the output) — also work for source blocks,
        # whose dsp() generates samples from scratch (e.g. weak_tone on the Dialer)
        if self.fault is not None and getattr(self.fault, "stage", "post") == "post":
            out = self.fault.apply(out)
        self._last_out = out
        return out

    def _bypass(self, frame: Frame) -> Frame:
        """enabled=False: passes through by default (control blocks may mute)."""
        return frame

    def detect(self) -> list:
        if self._last_in is None or self._last_out is None:
            return []
        return self.detector.check(self._last_in, self._last_out)

    def tap(self) -> TapData:
        out = self._last_out
        wf = out.samples if out is not None else None
        return TapData(waveform=wf)
