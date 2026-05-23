"""Isolated block harness (DESIGN.md §13a)."""
from __future__ import annotations
import numpy as np

from engine.frame import Frame
from engine.const import FRAME_N, FS


def run_block(block, frames, fault=None, flush_frames: int = 4):
    """Passes frames through the block, collects output and diagnostics.

    fault: optional FaultSpec set on the block.
    flush_frames: additional silence frames at the end (closes the tone segment).
    """
    block.fault = fault
    out, diags = [], []
    last_seq, last_t = 0, 0.0
    for f in frames:
        g = block.process(f)
        diags += block.detect()
        out.append(g)
        last_seq, last_t = f.seq, f.t
    # flush with silence
    for i in range(flush_frames):
        seq = last_seq + 1 + i
        t = last_t + (1 + i) * FRAME_N / FS
        f = Frame(seq, t, np.zeros(FRAME_N, np.float32))
        block.process(f)
        diags += block.detect()
    return out, diags


def codes(diags) -> list[str]:
    return [d.code for d in diags]


def detected_digit(diags):
    """Last confirmed DTMF digit from diagnostics (or None)."""
    digs = [d.measured.get("digit") for d in diags if d.code == "DTMF_DETECTED"]
    return digs[-1] if digs else None


def has_error(diags) -> bool:
    return any(d.severity == "error" for d in diags)
