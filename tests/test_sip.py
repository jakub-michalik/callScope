"""SIP state machine tests — synthetic timeline, no DSP (DESIGN.md §13a)."""
from blocks.sip import SipSession, SipBlock
from engine.frame import Frame
from dsp.metrics import rms


def _run(sip, t0=0.0, until=1.2, dt=0.02):
    msgs = []
    t = t0
    while t <= t0 + until:
        msgs += sip.tick(t)
        t += dt
    return msgs


def test_normal_callflow_reaches_incall():
    sip = SipSession()
    sip.start(0.0, "112")
    msgs = _run(sip)
    codes = [m["code"] for m in msgs]
    assert codes == ["INVITE", "100", "180", "200", "ACK"]
    assert sip.state == "INCALL"
    assert sip.media is True


def test_forced_503_fails_the_call():
    sip = SipSession()
    sip.force_code = "503"
    sip.start(0.0, "112")
    msgs = _run(sip)
    codes = [m["code"] for m in msgs]
    assert "503" in codes
    assert "200" not in codes
    assert sip.state == "FAILED"
    assert sip.media is False
    assert sip.conditions(1.0)[0]["code"] == "SIP_503"


def test_busy_486_fails():
    sip = SipSession()
    sip.force_code = "486"
    sip.start(0.0)
    _run(sip)
    assert sip.state == "FAILED"
    assert sip.conditions(1.0)[0]["code"] == "SIP_486"


def test_auto_bye_after_call_duration():
    """The call stays up, then the simulated caller hangs up (BYE) — in order."""
    from blocks.sip import CALL_DURATION
    sip = SipSession()
    sip.start(0.0)
    msgs = _run(sip, until=CALL_DURATION + 0.2)
    codes = [m["code"] for m in msgs]
    assert codes == ["INVITE", "100", "180", "200", "ACK", "BYE"]   # BYE last, after ACK
    assert sip.state == "TERMINATED"


def test_hangup_cancels_pending_messages():
    """Hanging up mid-flow cancels scheduled 200/ACK (no messages after BYE)."""
    sip = SipSession()
    sip.start(0.0)
    _run(sip, until=0.4)                 # only INVITE/100/180 so far
    bye = sip.hangup(0.5)
    assert [m["code"] for m in bye] == ["BYE"]
    assert _run(sip, t0=0.6, until=1.0) == []   # nothing fires after BYE


def test_sipblock_gates_media_on_call_state():
    """SipBlock passes media only while INCALL (RTP follows signaling)."""
    sess = SipSession()
    blk = SipBlock(sess)
    out = blk.process(Frame.silence(0, 0.0))
    assert rms(out.samples) < 1e-4              # not in call -> no media
    sess.start(0.0); _run(sess)                 # advance to INCALL
    out2 = blk.process(Frame.silence(1, 0.0))
    assert rms(out2.samples) > 1e-3             # in call -> comfort-noise media flows


def test_hangup_after_incall_sends_bye():
    sip = SipSession()
    sip.start(0.0)
    _run(sip)
    assert sip.state == "INCALL"
    bye = sip.hangup(2.0)
    assert [m["code"] for m in bye] == ["BYE"]
    assert sip.state == "TERMINATED"
    assert sip.media is False
