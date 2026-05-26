"""SIP signaling state machine + media gate (DESIGN.md §5.5).

A minimal UAC callflow on a timeline:
  INVITE -> 100 Trying -> 180 Ringing -> 200 OK -> ACK -> (in call) -> BYE
With real response codes; a forced 4xx/5xx fails the call (no media).

SipBlock sits between DTMF and CodecRTP: it GATES the media path — RTP only flows
once signaling reaches INCALL (signaling sets up the call, then media flows).
"""
from __future__ import annotations
import numpy as np

from engine.block import Block, TapData
from engine.frame import Frame
from engine.const import FRAME_N

SIP_REASON = {
    "200": "OK", "100": "Trying", "180": "Ringing",
    "404": "Not Found", "408": "Request Timeout",
    "486": "Busy Here", "503": "Service Unavailable",
}

_FAIL_HINT = {
    "486": "callee busy (Q.850 #17)",
    "503": "congestion / no circuit (Q.850 #34)",
    "404": "number not in dialplan (Q.850 #1)",
    "408": "no answer / request timeout",
}

CALL_DURATION = 6.0     # seconds in call before the simulated caller hangs up


class SipSession:
    def __init__(self):
        self.force_code: str | None = None
        self.reset()

    def reset(self):
        self.state = "IDLE"        # IDLE CALLING RINGING ANSWERED INCALL TERMINATED FAILED
        self._sched: list = []     # (t, dir, code, reason)
        self.media = False
        self.number = ""

    def start(self, t: float, number: str = "112"):
        keep = self.force_code
        self.reset()
        self.force_code = keep
        self.number = number
        self.state = "CALLING"
        sched = [
            (t + 0.00, "out", "INVITE", None, f"→ sip:{number}@provider · SDP offer G.711µ (PCMU/8000)"),
            (t + 0.05, "in", "100", "Trying", "provider accepted the request"),
            (t + 0.30, "in", "180", "Ringing", "remote end is ringing"),
        ]
        if self.force_code:
            sched.append((t + 0.9, "in", self.force_code, SIP_REASON.get(self.force_code, ""),
                          _FAIL_HINT.get(self.force_code, "call setup failed")))
        else:
            sched += [
                (t + 0.9, "in", "200", "OK", "answer PCMU/8000 · media channel up"),
                (t + 0.95, "out", "ACK", None, "dialog confirmed — RTP flowing"),
                (t + CALL_DURATION, "out", "BYE", None, "caller hung up (call timeout)"),
            ]
        self._sched = sched

    def hangup(self, t: float) -> list:
        if self.state in ("INCALL", "ANSWERED", "RINGING"):
            self._sched = []                     # cancel any pending messages
            self.state = "TERMINATED"
            self.media = False
            return [self._msg(t, "out", "BYE", None, "caller hung up")]
        return []

    def tick(self, t: float) -> list:
        due = [m for m in self._sched if m[0] <= t]
        self._sched = [m for m in self._sched if m[0] > t]
        out = []
        for (mt, d, code, reason, detail) in due:
            self._apply(code)
            out.append(self._msg(mt, d, code, reason, detail))
        return out

    def _apply(self, code: str):
        if code == "180":
            self.state = "RINGING"
        elif code == "200":
            self.state = "ANSWERED"
        elif code == "ACK":
            self.state = "INCALL"
            self.media = True
        elif code == "BYE":
            self.state = "TERMINATED"
            self.media = False
        elif code in ("404", "408", "486", "503"):
            self.state = "FAILED"
            self.media = False

    @staticmethod
    def _msg(t, d, code, reason, detail="") -> dict:
        label = code if reason is None else f"{code} {reason}"
        return {"dir": d, "code": code, "label": label, "detail": detail, "t": t}

    def conditions(self, t: float) -> list:
        """Active SIP condition for the correlator snapshot (a failed call)."""
        if self.state == "FAILED" and self.force_code:
            return [{"code": f"SIP_{self.force_code}", "block": "SIP",
                     "severity": "error", "t": t,
                     "protocol_code": f"{self.force_code} {SIP_REASON.get(self.force_code, '')}"}]
        return []


class SipBlock(Block):
    """Control-plane gate in the chain: passes media only while the call is up (INCALL).

    Before 200 OK there is no RTP media; once INCALL, a comfort-noise voice channel
    flows so the downstream RTP block has media to measure. The DTMF dialing tones
    (which happen before call setup) are therefore not carried as RTP.
    """
    name = "SIP"
    PLANE = "control"
    FAULTS = {
        "sip_503": (None, {}, "Force 503 Service Unavailable"),
        "sip_486": (None, {}, "Force 486 Busy Here"),
    }

    def __init__(self, session: SipSession):
        super().__init__()
        self.sip = session

    # SIP faults act on the signaling state machine, not on a frame
    def set_fault(self, ftype: str) -> bool:
        if ftype in ("sip_503", "sip_486"):
            self.sip.force_code = ftype.split("_")[1]
            return True
        return False

    def clear_fault(self) -> None:
        self.sip.force_code = None

    def conditions(self, t: float) -> list:
        return self.sip.conditions(t)

    def dsp(self, frame: Frame) -> Frame:
        f = frame.copy()
        if self.sip.state == "INCALL":
            rng = np.random.default_rng(frame.seq)        # comfort noise = the voice channel
            f.samples = (0.02 * rng.standard_normal(FRAME_N)).astype(np.float32)
        else:
            f.samples = np.zeros(FRAME_N, np.float32)      # no media until the call is up
        f.meta["sip_state"] = self.sip.state
        return f

    def tap(self) -> TapData:
        return TapData(metrics={"state": self.sip.state})
