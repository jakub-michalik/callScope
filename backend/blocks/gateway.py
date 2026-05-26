"""Gateway / Provider block (DESIGN.md §5.6): RTP egress sink + MOS condition."""
from __future__ import annotations

from engine.block import Block, Detector, TapData
from engine.frame import Frame

MOS_MIN = 3.5


class GatewayDetector(Detector):
    @staticmethod
    def conditions(meta: dict, t: float) -> list:
        s = meta.get("rtp")
        if s and s.get("audio") and s.get("mos", 4.4) < MOS_MIN:
            return [{"code": "MOS_LOW", "block": "Gateway", "severity": "warn", "t": t}]
        return []


class GatewayBlock(Block):
    name = "Gateway"

    def __init__(self):
        super().__init__()
        self.detector = GatewayDetector()
        self.last_rtp = {}

    def conditions(self, t: float) -> list:
        return GatewayDetector.conditions(self._last_out.meta, t) if self._last_out else []

    def dsp(self, frame: Frame) -> Frame:
        self.last_rtp = frame.meta.get("rtp", {})
        return frame

    def tap(self) -> TapData:
        return TapData(metrics=dict(self.last_rtp))
