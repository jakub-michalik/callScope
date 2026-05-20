"""Frame — a portion of signal flowing through the graph (DESIGN.md §2.1)."""
from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np

from .const import FRAME_N


@dataclass
class Frame:
    seq: int                       # frame number since session start
    t: float                       # simulation time [s]
    samples: np.ndarray            # audio float32 [FRAME_N]
    meta: dict = field(default_factory=dict)  # e.g. {"loop_mA": 23.0}

    def copy(self) -> "Frame":
        return Frame(self.seq, self.t, self.samples.copy(), dict(self.meta))

    @staticmethod
    def silence(seq: int = 0, t: float = 0.0, n: int = FRAME_N) -> "Frame":
        return Frame(seq, t, np.zeros(n, dtype=np.float32))
