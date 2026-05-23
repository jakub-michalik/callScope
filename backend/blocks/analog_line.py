"""Analog Line / FXS block: electrical model of the line (DESIGN.md §5.2)."""
from __future__ import annotations

from engine.block import Block, Detector, TapData
from engine.frame import Frame
from engine.const import PRESENCE_RMS
from engine.faults import NoLoopCurrent, LineNoise, Hum50Hz
from dsp.metrics import rms

V_ONHOOK = 48.0      # V
V_OFFHOOK = 7.0      # V
LOOP_OFFHOOK_MA = 23.0
LOOP_MIN_MA = 18.0   # threshold for detecting loss of loop
SNR_MIN_DB = 20.0


class FxsDetector(Detector):
    """Edge-triggered: each condition is raised once and cleared once (sticky),
    so it does not spam a diagnostic every frame while the condition holds."""

    def __init__(self):
        self._on: dict[str, bool] = {}

    def _edge(self, code: str, cond: bool, make) -> list:
        was = self._on.get(code, False)
        if cond and not was:
            self._on[code] = True
            return [make(True)]
        if not cond and was:
            self._on[code] = False
            return [make(False)]
        return []

    @staticmethod
    def conditions(meta: dict, t: float) -> list:
        """Currently-active conditions (stateless level check, for the correlator snapshot)."""
        c = []
        loop = meta.get("loop_mA", 0.0)
        snr = meta.get("snr_dB")
        if bool(meta.get("offhook")) and loop < LOOP_MIN_MA:
            c.append({"code": "FXS_NO_LOOP_CURRENT", "block": "AnalogLine",
                      "severity": "error", "t": t})
        if snr is not None and snr < SNR_MIN_DB:
            c.append({"code": "LINE_LOW_SNR", "block": "AnalogLine",
                      "severity": "warn", "t": t})
        return c

    def check(self, frame_in: Frame, frame_out: Frame) -> list:
        from diag.diagnostic import Diagnostic
        m = frame_out.meta
        t = frame_out.t
        sid = m.get("session_id")
        loop = m.get("loop_mA", 0.0)
        snr = m.get("snr_dB")
        out = []
        out += self._edge(
            "FXS_NO_LOOP_CURRENT", bool(m.get("offhook")) and loop < LOOP_MIN_MA,
            lambda a: Diagnostic("FXS_NO_LOOP_CURRENT", "AnalogLine", "error",
                                 protocol_code="loop signaling",
                                 message="no loop current at off-hook",
                                 measured={"loop_mA": round(loop, 1)},
                                 t=t, session_id=sid, active=a, sticky=True))
        out += self._edge(
            "LINE_LOW_SNR", snr is not None and snr < SNR_MIN_DB,
            lambda a: Diagnostic("LINE_LOW_SNR", "AnalogLine", "warn",
                                 message="low line SNR",
                                 measured={"snr_dB": round(snr, 1) if snr is not None else None},
                                 t=t, session_id=sid, active=a, sticky=True))
        return out


class AnalogLineBlock(Block):
    name = "AnalogLine"
    FAULTS = {
        "no_loop_current": (NoLoopCurrent, {}, "No loop current"),
        "line_noise": (LineNoise, {"snr_dB": 6}, "Line noise (SNR 6 dB)"),
        "hum_50hz": (Hum50Hz, {"level": 0.2}, "50 Hz hum"),
    }

    def __init__(self):
        super().__init__()
        self.detector = FxsDetector()

    def conditions(self, t: float) -> list:
        return FxsDetector.conditions(self._last_out.meta, t) if self._last_out else []

    def dsp(self, frame: Frame) -> Frame:
        f = frame.copy()
        # off-hook: explicit meta from the Dialer or detected from signal presence
        offhook = f.meta.get("offhook")
        if offhook is None:
            offhook = rms(f.samples) > PRESENCE_RMS
        f.meta["offhook"] = bool(offhook)

        if f.meta.get("force_no_loop"):          # no_loop_current fault
            loop = 0.0
        else:
            loop = LOOP_OFFHOOK_MA if offhook else 0.0
        f.meta["loop_mA"] = loop
        f.meta["line_voltage"] = V_OFFHOOK if offhook else V_ONHOOK
        return f

    def tap(self) -> TapData:
        m = self._last_out.meta if self._last_out is not None else {}
        return TapData(
            waveform=self._last_out.samples if self._last_out is not None else None,
            metrics={"loop_mA": m.get("loop_mA", 0.0),
                     "line_voltage": m.get("line_voltage", V_ONHOOK),
                     "offhook": m.get("offhook", False)})
