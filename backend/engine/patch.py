"""Patch — link between blocks (DESIGN.md §4)."""
from __future__ import annotations

from .frame import Frame
from .faults import Impairment


class Patch:
    def __init__(self, src: str, dst: str, kind: str = "analog"):
        self.src = src
        self.dst = dst
        self.kind = kind                  # analog | dtmf | rtp (token type in UI)
        self.connected = True             # signal cut
        self.impairment: Impairment | None = None

    @property
    def id(self) -> str:
        return f"{self.src}→{self.dst}"

    def apply(self, frame: Frame) -> Frame:
        if self.impairment is not None:
            return self.impairment.apply(frame)
        return frame
