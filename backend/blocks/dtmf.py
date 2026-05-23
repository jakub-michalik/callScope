"""DTMF Detector block: Goertzel + validation + tone state machine (DESIGN.md §5.3).

Detection is GAIN-INDEPENDENT: it is based on the SNR of each peak relative to the
background (the other bins), not on absolute amplitude. This works both for a loud
synthetic tone and for a quiet/noisy microphone signal.

Tone boundaries are detected by ABSENCE OF A CLEAR PEAK ("gap"), not by digital
silence — because over a microphone the inter-digit pause is room noise, never a true
zero. This lets the same digit pressed twice (tone-pause-tone) be counted twice.
"""
from __future__ import annotations
import math
import numpy as np

from engine.block import Block, Detector, TapData
from engine.frame import Frame
from engine.const import (FS, FRAME_MS, DTMF_WIN_N, DTMF_LOW, DTMF_HIGH,
                          DTMF_KEYS, DTMF_FREQS)
from dsp.goertzel import GoertzelBank
from dsp.metrics import rms, twist_db
from diag.diagnostic import Diagnostic

# Validation thresholds (DESIGN.md §5.3) — tuned for acoustic / microphone input.
SILENCE_RMS = 1e-4      # gate against digital silence (zeros) only — not a level gate
GAP_SNR_DB = 3.0        # below this on BOTH groups -> no clear peak -> idle / gap
SNR_MIN_DB = 8.0        # a peak must exceed the mean of the other bins by >= this
TWIST_MAX_DB = 12.0     # looser than the network spec (8 dB) — acoustics attenuate lows
# Default well above the ITU-T Q.24 receiver floor (40 ms): requiring several
# consecutive same-digit frames makes random-noise false positives very unlikely.
MIN_TONE_MS = 80.0      # 4 consecutive frames
MIN_FRAMES = math.ceil(MIN_TONE_MS / FRAME_MS)
HANGOVER_FRAMES = 2     # gap frames needed to end a tone (merges brief drop-outs)


def _digit_for(i_low: int, i_high: int) -> str:
    return DTMF_KEYS[i_low][i_high]


class DtmfDecoder(Detector):
    """Streaming decoder: 205-sample window, lock + gap-release state machine.

    A digit "locks" only after MIN_FRAMES consecutive valid frames of the same digit
    (robust against frame-to-frame argmax flicker on noisy mic input). A tone ends
    after HANGOVER_FRAMES of "gap" (no clear peak), which releases the lock so the
    same digit pressed again is detected as a new press.
    """

    # parameters tunable live from the dashboard
    PARAMS = ("snr_min_db", "twist_max_db", "min_tone_ms")

    def __init__(self):
        self.bank = GoertzelBank()
        self.n_low = len(DTMF_LOW)
        self.buf = np.zeros(DTMF_WIN_N, dtype=float)
        self.snr_min_db = SNR_MIN_DB
        self.twist_max_db = TWIST_MAX_DB
        self.min_tone_ms = MIN_TONE_MS
        self.last_info = {}
        self.reset()

    @property
    def min_frames(self) -> int:
        return max(1, math.ceil(self.min_tone_ms / FRAME_MS))

    def set_param(self, name: str, value: float) -> bool:
        if name not in self.PARAMS:
            return False
        setattr(self, name, float(value))
        return True

    def params(self) -> dict:
        return {k: getattr(self, k) for k in self.PARAMS}

    def reset(self):
        self.buf[:] = 0.0
        self.cand_digit = None        # current candidate digit
        self.cand_run = 0             # consecutive valid frames of the candidate
        self.locked = None            # digit already emitted for the current tone
        self.tone_valid_frames = 0    # valid frames seen in the current tone
        self.gap_run = 0              # consecutive gap frames (hangover)
        self.reject_run = 0           # consecutive "signal but unreadable" frames
        self.reject_reason = None     # "snr" | "twist"
        self.reject_latched = False   # reject already reported for this stretch
        self.last_mag = {int(f): 0.0 for f in DTMF_FREQS}
        self.last_digit = None        # last confirmed digit

    # --- classify one frame: gap | reject | valid (gain-independent) ---
    def _classify(self, e: np.ndarray, wrms: float):
        if wrms < SILENCE_RMS:
            return "gap", None, {}

        low = e[:self.n_low]
        high = e[self.n_low:]
        i_low = int(np.argmax(low))
        i_high = int(np.argmax(high))
        peak_low, peak_high = float(low[i_low]), float(high[i_high])

        # background = mean energy of the OTHER bins (excluding the two peaks).
        # Near 0 for a clean tone (high SNR); high for speech/collision/noise (low SNR).
        others = ([v for j, v in enumerate(low) if j != i_low]
                  + [v for j, v in enumerate(high) if j != i_high])
        noise = float(np.mean(others)) + 1e-12
        snr_low = 10 * math.log10((peak_low + 1e-12) / noise)
        snr_high = 10 * math.log10((peak_high + 1e-12) / noise)
        tw = twist_db(peak_low, peak_high)
        digit = _digit_for(i_low, i_high)
        info = {"twist_dB": round(tw, 2), "snr_low": round(snr_low, 1),
                "snr_high": round(snr_high, 1), "rms": round(wrms, 5)}

        # no clearly dominant peak -> idle / inter-digit gap (room noise)
        if max(snr_low, snr_high) < GAP_SNR_DB:
            return "gap", None, info
        # both peaks strong and twist in range -> a valid tone
        if min(snr_low, snr_high) >= self.snr_min_db and abs(tw) <= self.twist_max_db:
            return "valid", digit, info
        # peaks strong but twist out of range
        if min(snr_low, snr_high) >= self.snr_min_db:
            return "reject", "twist", info
        # signal present but a peak is weak (collision / marginal)
        return "reject", "snr", info

    def check(self, frame_in: Frame, frame_out: Frame) -> list:
        # sliding 205-sample analysis window
        s = frame_out.samples
        n = len(s)
        if n >= DTMF_WIN_N:
            self.buf[:] = s[-DTMF_WIN_N:]
        else:
            self.buf[:-n] = self.buf[n:]
            self.buf[-n:] = s
        e = self.bank.energy(self.buf)
        self.last_mag = {int(f): float(math.sqrt(m)) for f, m in zip(DTMF_FREQS, e)}
        wrms = rms(self.buf)
        t = frame_out.t
        sid = frame_out.meta.get("session_id")

        kind, val, info = self._classify(e, wrms)
        self.last_info = info
        diags: list = []

        if kind == "valid":
            self.gap_run = 0
            self.reject_run = 0
            self.reject_latched = False
            self.reject_reason = None
            if val == self.cand_digit:
                self.cand_run += 1
            else:
                self.cand_digit = val      # new candidate digit
                self.cand_run = 1
                self.locked = None         # digit change releases the lock
            self.tone_valid_frames += 1
            # lock only after MIN_FRAMES of the same digit -> ignores flicker
            if self.cand_run >= self.min_frames and self.locked != self.cand_digit:
                self.locked = self.cand_digit
                self.last_digit = self.cand_digit
                diags.append(Diagnostic(
                    code="DTMF_DETECTED", block="DTMF", severity="info",
                    message=f"digit {self.cand_digit}",
                    measured={"digit": self.cand_digit, **info},
                    t=t, session_id=sid))

        elif kind == "reject":
            self.gap_run = 0               # signal present -> not a gap
            self.reject_run += 1
            if self.reject_reason is None:
                self.reject_reason = val
            # report once when a present-but-unreadable signal has no locked digit
            if (self.reject_run >= self.min_frames and not self.reject_latched
                    and self.locked is None and self.tone_valid_frames == 0):
                self.reject_latched = True
                if self.reject_reason == "twist":
                    diags.append(Diagnostic("DTMF_TWIST_OOR", "DTMF", "warn",
                                            message="twist out of range",
                                            measured={"digit": self.cand_digit},
                                            t=t, session_id=sid))
                else:
                    diags.append(Diagnostic("DTMF_REJECTED", "DTMF", "warn",
                                            message="rejected (low SNR / not a tone)",
                                            measured={"reason": "snr"},
                                            t=t, session_id=sid))

        else:  # gap (silence / idle)
            self.gap_run += 1
            dirty = (self.cand_digit is not None or self.locked is not None
                     or self.tone_valid_frames > 0 or self.reject_run > 0)
            if dirty and self.gap_run >= HANGOVER_FRAMES:
                # a valid digit appeared but never reached the duration threshold
                if (self.locked is None and 0 < self.tone_valid_frames < self.min_frames):
                    diags.append(Diagnostic(
                        "DTMF_TOO_SHORT", "DTMF", "warn",
                        message=f"tone too short (<{int(self.min_tone_ms)}ms)",
                        measured={"digit": self.cand_digit,
                                  "dur_ms": self.tone_valid_frames * FRAME_MS},
                        t=t, session_id=sid))
                self._reset_tone()
        return diags

    def _reset_tone(self):
        self.cand_digit = None
        self.cand_run = 0
        self.locked = None
        self.tone_valid_frames = 0
        self.reject_run = 0
        self.reject_reason = None
        self.reject_latched = False


class DtmfBlock(Block):
    name = "DTMF"

    def __init__(self):
        super().__init__()
        self.detector = DtmfDecoder()

    def dsp(self, frame: Frame) -> Frame:
        return frame  # the detector analyses; audio passes through unchanged

    def tap(self) -> TapData:
        d: DtmfDecoder = self.detector
        info = d.last_info or {}
        return TapData(spectrum=dict(d.last_mag),
                       metrics={"digit": d.last_digit,
                                "snr_low": info.get("snr_low"),
                                "snr_high": info.get("snr_high"),
                                "twist_dB": info.get("twist_dB"),
                                **d.params()})
