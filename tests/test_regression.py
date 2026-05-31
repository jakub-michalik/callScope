"""Regression tests for bugs from review (#1 fault-on-source, #2 back-to-back, #3 off-by-one)."""
import numpy as np

from blocks.dialer import DialerBlock
from blocks.dtmf import DtmfBlock
from engine.frame import Frame
from engine.faults import WeakTone, NoLoopCurrent
from blocks.analog_line import AnalogLineBlock
from dsp.tone_gen import dtmf_samples
from dsp.metrics import rms
from harness import run_block, codes
from signals import framize


def _drive(dialer, n, fault=None):
    dialer.fault = fault
    return [dialer.process(Frame.silence(i, i * 0.02)) for i in range(n)]


def test_weak_tone_fault_affects_source():
    """#1: 'post' fault on a source block actually attenuates the generated signal."""
    d_clean = DialerBlock(); d_clean.dial("5", leadin_ms=0)
    d_weak = DialerBlock(); d_weak.dial("5", leadin_ms=0)
    out_clean = _drive(d_clean, 3)
    out_weak = _drive(d_weak, 3, fault=WeakTone(gain_dB=-20))
    rc = rms(out_clean[0].samples)
    rw = rms(out_weak[0].samples)
    assert rw < rc * 0.2          # ~-20 dB
    assert rc > 0.01              # sanity: the clean signal exists


def test_pre_fault_still_works_on_analogline():
    """#1 regression: 'pre' fault (no_loop_current) still works via meta."""
    frames = framize(dtmf_samples("5", 100), meta={"offhook": True})
    _, diags = run_block(AnalogLineBlock(), frames, fault=NoLoopCurrent())
    assert "FXS_NO_LOOP_CURRENT" in codes(diags)


def test_back_to_back_digits_both_detected():
    """#2: two valid digits with no pause -> both detected."""
    sig = np.concatenate([dtmf_samples("5", 120), dtmf_samples("6", 120)]).astype(np.float32)
    _, diags = run_block(DtmfBlock(), framize(sig))
    got = [d.measured.get("digit") for d in diags if d.code == "DTMF_DETECTED"]
    assert got == ["5", "6"]


def test_dialer_digit_marks_aligned_leadin_zero():
    """#3: with leadin_ms=0, frame 0 carries the tone and is marked with the correct digit."""
    d = DialerBlock(); d.dial("5", tone_ms=80, pause_ms=80, leadin_ms=0)
    out = _drive(d, 1)
    assert out[0].meta["dial_digit"] == "5"
    assert rms(out[0].samples) > 0.01     # frame 0 actually contains the tone
