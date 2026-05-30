"""Minimal RTP (RFC 3550) + G.711 codec — pure helpers, unit-tested."""
from __future__ import annotations
import struct
import numpy as np

try:
    import audioop                       # stdlib G.711 (removed in 3.13)
    _HAVE_AUDIOOP = True
except Exception:
    audioop = None
    _HAVE_AUDIOOP = False

PT_PCMU = 0
PT_PCMA = 8
RTP_VERSION = 2


def pcm16_to_ulaw(pcm16: np.ndarray) -> bytes:
    """int16 PCM -> G.711 mu-law bytes."""
    data = np.asarray(pcm16, dtype="<i2").tobytes()
    if _HAVE_AUDIOOP:
        return audioop.lin2ulaw(data, 2)
    return _lin2ulaw_np(np.asarray(pcm16, dtype=np.int32))


def ulaw_to_pcm16(ulaw: bytes) -> np.ndarray:
    if _HAVE_AUDIOOP:
        return np.frombuffer(audioop.ulaw2lin(ulaw, 2), dtype="<i2").copy()
    return _ulaw2lin_np(np.frombuffer(ulaw, dtype=np.uint8))


def _lin2ulaw_np(x: np.ndarray) -> bytes:
    BIAS, CLIP = 0x84, 32635
    sign = np.where(x < 0, 0x80, 0).astype(np.int32)
    mag = np.minimum(np.abs(x), CLIP) + BIAS
    exp = np.zeros_like(mag)
    for e in range(7, -1, -1):
        exp = np.where(mag >= (1 << (e + 7)), e, exp)
    mant = (mag >> (exp + 3)) & 0x0F
    ulaw = ~(sign | (exp << 4) | mant) & 0xFF
    return ulaw.astype(np.uint8).tobytes()


def _ulaw2lin_np(u: np.ndarray) -> np.ndarray:
    u = (~u) & 0xFF
    sign = u & 0x80
    exp = (u >> 4) & 0x07
    mant = u & 0x0F
    mag = ((mant.astype(np.int32) << 3) + 0x84) << exp
    mag = mag - 0x84
    val = np.where(sign != 0, -mag, mag)
    return np.clip(val, -32768, 32767).astype("<i2")


def pack(payload: bytes, seq: int, timestamp: int, ssrc: int,
         pt: int = PT_PCMU, marker: bool = False) -> bytes:
    b0 = (RTP_VERSION << 6)
    b1 = (0x80 if marker else 0) | (pt & 0x7F)
    return struct.pack("!BBHII", b0, b1, seq & 0xFFFF, timestamp & 0xFFFFFFFF,
                       ssrc & 0xFFFFFFFF) + payload


def unpack(data: bytes) -> dict:
    b0, b1, seq, ts, ssrc = struct.unpack("!BBHII", data[:12])
    return {"version": b0 >> 6, "marker": bool(b1 & 0x80), "pt": b1 & 0x7F,
            "seq": seq, "timestamp": ts, "ssrc": ssrc, "payload": data[12:]}


class RtpStats:
    """Receiver-side loss + interarrival jitter (RFC 3550)."""
    def __init__(self, clock=8000):
        self.clock = clock
        self.received = 0
        self.expected_base = None
        self.max_seq = None
        self.jitter = 0.0
        self._last_transit = None
        self._first_arrival = None
        self._last_arrival = None

    def on_packet(self, seq: int, ts: int, arrival_s: float):
        self.received += 1
        if self._first_arrival is None:
            self._first_arrival = arrival_s
        self._last_arrival = arrival_s
        if self.max_seq is None:
            self.expected_base = seq
            self.max_seq = seq
        else:
            self.max_seq = max(self.max_seq, seq)
        # interarrival jitter
        transit = arrival_s - ts / self.clock
        if self._last_transit is not None:
            d = abs(transit - self._last_transit)
            self.jitter += (d - self.jitter) / 16.0
        self._last_transit = transit

    def snapshot(self) -> dict:
        if self.max_seq is None:
            return {"loss_pct": 0.0, "jitter_ms": 0.0, "received": 0, "pps": 0}
        expected = self.max_seq - self.expected_base + 1
        lost = max(0, expected - self.received)
        loss = 100.0 * lost / expected if expected else 0.0
        span = (self._last_arrival or 0) - (self._first_arrival or 0)
        pps = round(self.received / span) if span > 0.05 else 0
        return {"loss_pct": round(loss, 1), "jitter_ms": round(self.jitter * 1000, 1),
                "received": self.received, "pps": pps}
