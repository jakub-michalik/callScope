"""Diagnostic — a detected problem (DESIGN.md §2.5, §7)."""
from __future__ import annotations
from dataclasses import dataclass, field

SEVERITIES = ("info", "warn", "error")


@dataclass
class Diagnostic:
    code: str                       # internal, e.g. "DTMF_TWIST_OOR"
    block: str                      # where it was detected
    severity: str = "warn"          # info | warn | error
    protocol_code: str | None = None  # real code, e.g. "503 Service Unavailable"
    message: str = ""
    measured: dict = field(default_factory=dict)
    t: float = 0.0
    session_id: str | None = None
    # sticky = a persistent CONDITION (raised on a rising edge, cleared with active=False);
    # non-sticky = a momentary EVENT. active=False signals that a sticky condition cleared.
    active: bool = True
    sticky: bool = False

    def to_envelope(self) -> dict:
        return {
            "ch": "diag",
            "t": self.t,
            "data": {
                "code": self.code,
                "block": self.block,
                "severity": self.severity,
                "protocol_code": self.protocol_code,
                "message": self.message,
                "measured": self.measured,
                "session_id": self.session_id,
                "t": self.t,
                "active": self.active,
                "sticky": self.sticky,
            },
        }
