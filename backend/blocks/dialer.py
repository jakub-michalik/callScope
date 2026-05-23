"""Dialer block (source): generates a DTMF sequence + off-hook (DESIGN.md §5.1)."""
from __future__ import annotations
import numpy as np

from engine.block import Block, Detector, TapData
from engine.frame import Frame
from engine.const import FS, FRAME_N, PRESENCE_RMS
from dsp.tone_gen import dtmf_samples, silence
from dsp.metrics import rms
from engine.faults import WeakTone

SRC_WEAK_RMS = 0.05      # generated tone level below this -> source weak


class SrcDetector(Detector):
    """Flags a generated tone whose level is too low (e.g. weak_tone fault).

    Edge-triggered (once per weak tone, on the rising edge) so it does not fire
    every frame; emitted as a momentary event, not a sticky condition.
    """
    def __init__(self):
        self._on = False

    def check(self, frame_in: Frame, frame_out: Frame) -> list:
        from diag.diagnostic import Diagnostic
        m = frame_out.meta
        cond = m.get("dial_digit") is not None and rms(frame_out.samples) < SRC_WEAK_RMS
        if cond and not self._on:
            self._on = True
            return [Diagnostic("SRC_WEAK_LEVEL", "Dialer", "warn",
                               message="generated tone level too low",
                               measured={"rms": round(rms(frame_out.samples), 4)},
                               t=frame_out.t, session_id=m.get("session_id"))]
        if not cond:
            self._on = False
        return []


class DialerBlock(Block):
    name = "Dialer"
    FAULTS = {"weak_tone": (WeakTone, {"gain_dB": -22}, "Weak tone (−22 dB)")}

    def __init__(self, level: float = 0.5):
        super().__init__()
        self.level = level
        self.detector = SrcDetector()
        self._buf = np.zeros(0, np.float32)
        self._digit_at = []              # active digit per frame (or None)
        self._cursor = 0
        self.active = False
        self.number = ""
        self.source = "gen"              # "gen" | "mic"
        self.mic = None                  # callable -> np.float32[FRAME_N]
        self.offhook = False             # line hook state (persists for the whole call)

    def pickup(self):
        self.offhook = True

    def onhook(self):
        self.offhook = False
        self.active = False

    def dial(self, number: str, tone_ms: float = 100.0, pause_ms: float = 100.0,
             leadin_ms: float = 200.0):
        """Picks up the line and dials a number. The line stays off-hook until onhook()."""
        self.number = number
        self.offhook = True              # pick up the handset
        parts, segments, pos = [], [], 0
        def add(samples, digit):
            nonlocal pos
            parts.append(samples)
            segments.append((pos, pos + len(samples), digit))
            pos += len(samples)

        add(silence(leadin_ms), None)
        for d in number:
            add(dtmf_samples(d, tone_ms, level=self.level), d)
            add(silence(pause_ms), None)
        self._buf = (np.concatenate(parts).astype(np.float32)
                     if parts else np.zeros(0, np.float32))

        # digit markers aligned to ACTUAL frames (by the frame's start sample)
        n_frames = (len(self._buf) + FRAME_N - 1) // FRAME_N
        self._digit_at = []
        for i in range(n_frames):
            s = i * FRAME_N
            digit = next((dg for a, b, dg in segments if a <= s < b), None)
            self._digit_at.append(digit)

        self._cursor = 0
        self.active = True

    def dsp(self, frame: Frame) -> Frame:
        if self.source == "mic":
            return self._mic_frame(frame)
        f = frame.copy()
        if not self.offhook:               # on-hook: line idle
            f.samples = np.zeros(FRAME_N, np.float32)
            f.meta["offhook"] = False
            f.meta["dial_digit"] = None
            self.active = False
            return f
        # off-hook: play the dialed tones if any remain, otherwise silence (line stays seized)
        start = self._cursor * FRAME_N
        if self.active and start < len(self._buf):
            chunk = self._buf[start:start + FRAME_N]
            if len(chunk) < FRAME_N:
                chunk = np.concatenate([chunk, np.zeros(FRAME_N - len(chunk), np.float32)])
            f.samples = chunk.astype(np.float32)
            idx = self._cursor
            f.meta["dial_digit"] = self._digit_at[idx] if idx < len(self._digit_at) else None
        else:
            f.samples = np.zeros(FRAME_N, np.float32)
            f.meta["dial_digit"] = None
            self.active = False
        f.meta["offhook"] = True           # off-hook for the whole call, not just while dialing
        self._cursor += 1
        return f

    def _mic_frame(self, frame: Frame) -> Frame:
        f = frame.copy()
        if not self.offhook:               # on-hook: line idle, ignore the microphone
            f.samples = np.zeros(FRAME_N, np.float32)
            f.meta["offhook"] = False
            f.meta["dial_digit"] = None
            return f
        s = self.mic() if self.mic else np.zeros(FRAME_N, np.float32)
        f.samples = np.asarray(s, np.float32)
        f.meta["offhook"] = True           # line seized while off-hook
        f.meta["dial_digit"] = None
        return f

    def tap(self) -> TapData:
        m = self._last_out.meta if self._last_out is not None else {}
        return TapData(
            waveform=self._last_out.samples if self._last_out is not None else None,
            metrics={"offhook": m.get("offhook", False),
                     "digit": m.get("dial_digit"),
                     "source": self.source})
