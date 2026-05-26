"""Codec / RTP block (DESIGN.md §5.4): G.711 packetization + loss/jitter/MOS.

Audio passes through; the block tracks an RTP stream (sequence, timestamp, payload
type) and per-window quality metrics, and exposes them for the dashboard. The
packet_loss / jitter faults degrade the stream.
"""
from __future__ import annotations
import numpy as np

from engine.block import Block, Detector, TapData
from engine.frame import Frame
from engine.const import FRAME_N, PRESENCE_RMS
from engine.faults import PacketLoss, Jitter
from dsp.metrics import rms

PT_PCMU = 0             # G.711 mu-law
LOSS_WARN_PCT = 1.0     # RTP_LOSS_SPIKE threshold (% over the window)
JITTER_WARN_MS = 30.0
WIN = 50                # ~1 s window of packets


def _mos(loss_pct: float, jitter_ms: float) -> float:
    """Simplified E-model: MOS from packet loss and jitter."""
    r = 93.2 - 2.5 * loss_pct - 0.15 * max(jitter_ms - 20.0, 0.0)
    r = max(0.0, min(100.0, r))
    mos = 1 + 0.035 * r + r * (r - 60) * (100 - r) * 7e-6
    return round(max(1.0, min(4.4, mos)), 2)


class RtpDetector(Detector):
    """Stateless condition check from the RTP stats placed in meta."""
    @staticmethod
    def conditions(meta: dict, t: float) -> list:
        s = meta.get("rtp")
        if not s:
            return []
        c = []
        if s["loss_pct"] > LOSS_WARN_PCT:
            c.append({"code": "RTP_LOSS_SPIKE", "block": "CodecRTP", "severity": "error", "t": t})
        if s["jitter_ms"] > JITTER_WARN_MS:
            c.append({"code": "RTP_JITTER_HIGH", "block": "CodecRTP", "severity": "warn", "t": t})
        return c


class CodecRtpBlock(Block):
    name = "CodecRTP"
    FAULTS = {
        "packet_loss": (PacketLoss, {"pct": 8.0}, "Packet loss (8%)"),
        "jitter": (Jitter, {"ms": 60.0}, "Jitter (60 ms)"),
    }

    def __init__(self):
        super().__init__()
        self.detector = RtpDetector()
        self.reset()

    def conditions(self, t: float) -> list:
        return RtpDetector.conditions(self._last_out.meta, t) if self._last_out else []

    def reset(self):
        self.seq = 0
        self.timestamp = 0
        self._sent = []        # 1 = delivered, 0 = lost (window)
        self._jit = 0.0
        self.stats = {"seq": 0, "loss_pct": 0.0, "jitter_ms": 0.0,
                      "mos": 4.4, "pt": PT_PCMU, "pps": 0, "audio": False}

    def dsp(self, frame: Frame) -> Frame:
        f = frame.copy()
        audio = rms(f.samples) > PRESENCE_RMS
        if audio:                      # only stream packets while there is media
            self.seq += 1
            self.timestamp += FRAME_N
            lost = bool(f.meta.get("rtp_drop"))
            self._sent.append(0 if lost else 1)
            if len(self._sent) > WIN:
                self._sent.pop(0)
            # jitter: grows under the jitter fault, decays otherwise
            self._jit = 0.9 * self._jit + (f.meta.get("rtp_jitter_ms", 0.0)) * 0.1
            delivered = sum(self._sent)
            total = len(self._sent)
            loss = 100.0 * (total - delivered) / total if total else 0.0
            self.stats = {"seq": self.seq, "loss_pct": round(loss, 1),
                          "jitter_ms": round(self._jit, 1), "mos": _mos(loss, self._jit),
                          "pt": PT_PCMU, "pps": total, "audio": True}
        else:
            self.stats = {**self.stats, "audio": False, "pps": 0}
        f.meta["rtp"] = self.stats
        return f

    def tap(self) -> TapData:
        return TapData(metrics=dict(self.stats))
