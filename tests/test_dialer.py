"""Dialer block cases (DESIGN.md §13a)."""
from blocks.dialer import DialerBlock
from engine.frame import Frame


def _drive(dialer, n):
    out = []
    for i in range(n):
        out.append(dialer.process(Frame.silence(i, i * 0.02)))
    return out


def test_offhook_during_dial():
    d = DialerBlock()
    d.dial("12", tone_ms=100, pause_ms=100, leadin_ms=200)
    out = _drive(d, 5)               # 100ms lead-in = 10 frames; 5 -> still offhook
    assert all(f.meta["offhook"] for f in out)


def test_emits_programmed_digits():
    d = DialerBlock()
    d.dial("7", tone_ms=100, pause_ms=100, leadin_ms=0)
    out = _drive(d, 5)               # 100ms tone = 5 frames
    digits = {f.meta.get("dial_digit") for f in out}
    assert "7" in digits


def test_stays_offhook_after_dialing_until_onhook():
    d = DialerBlock()
    d.dial("1", tone_ms=40, pause_ms=40, leadin_ms=0)
    out = _drive(d, 20)              # well past the dialed digits
    assert out[-1].meta["offhook"] is True    # line stays seized for the whole call
    assert not d.active                        # but no longer playing tones
    d.onhook()
    out2 = _drive(d, 1)
    assert out2[-1].meta["offhook"] is False   # on-hook releases the line
