"""Root-cause correlator tests — synthetic, no DSP (DESIGN.md §13a)."""
from engine.bus import EventBus
from diag.correlator import Correlator

CHAIN = ["Dialer", "AnalogLine", "DTMF", "SIP", "CodecRTP", "Gateway"]


def _c(code, block, sev="warn", t=0.0):
    return {"code": code, "block": block, "severity": sev, "t": t}


def _rootcauses(bus):
    return [e["data"] for e in bus.by_ch("rootcause")]


def test_most_upstream_is_root():
    """A cascade across stages -> the most-upstream condition is the root."""
    bus = EventBus()
    c = Correlator(bus, chain=CHAIN, window=1.0)
    c.update([_c("LINE_LOW_SNR", "AnalogLine")])
    c.feed(_c("DTMF_REJECTED", "DTMF", t=0.05))   # momentary downstream event
    c.tick(0.06)
    rc = _rootcauses(bus)[-1]
    assert rc["root_block"] == "AnalogLine"
    assert rc["root_code"] == "LINE_LOW_SNR"
    assert "DTMF_REJECTED" in rc["consequences"]


def test_link_cut_ranks_after_its_source():
    bus = EventBus()
    c = Correlator(bus, chain=CHAIN, window=1.0)
    c.update([_c("SIGNAL_CUT", "AnalogLine→DTMF")])
    c.feed(_c("DTMF_REJECTED", "DTMF", t=0.02))
    c.tick(0.03)
    assert _rootcauses(bus)[-1]["root_block"] == "AnalogLine→DTMF"


def test_snapshot_clears_when_empty():
    """When the condition snapshot becomes empty, the banner clears."""
    bus = EventBus()
    c = Correlator(bus, chain=CHAIN)
    c.update([_c("LINE_LOW_SNR", "AnalogLine")]); c.tick(0.0)
    c.update([]); c.tick(0.05)
    assert _rootcauses(bus)[-1].get("cleared") is True


def test_orphaned_condition_disappears():
    """Key fix: a condition on a no-longer-reachable block is dropped from the snapshot."""
    bus = EventBus()
    c = Correlator(bus, chain=CHAIN)
    # AnalogLine condition active, plus an upstream cut
    c.update([_c("SIGNAL_CUT", "Dialer→AnalogLine"), _c("LINE_LOW_SNR", "AnalogLine")])
    c.tick(0.0)
    # next tick AnalogLine is unreachable -> only the cut remains in the snapshot
    c.update([_c("SIGNAL_CUT", "Dialer→AnalogLine")])
    c.tick(0.05)
    rc = _rootcauses(bus)[-1]
    assert rc["root_block"] == "Dialer→AnalogLine"
    assert "LINE_LOW_SNR" not in rc["consequences"]


def test_momentary_events_age_out():
    bus = EventBus()
    c = Correlator(bus, chain=CHAIN, window=0.3)
    c.feed(_c("DTMF_REJECTED", "DTMF", t=0.0)); c.tick(0.05)
    c.tick(1.0)
    assert _rootcauses(bus)[-1].get("cleared") is True


def test_info_and_sticky_ignored_by_feed():
    bus = EventBus()
    c = Correlator(bus, chain=CHAIN)
    c.feed({"code": "DTMF_DETECTED", "block": "DTMF", "severity": "info", "t": 0.0})
    c.feed({"code": "X", "block": "DTMF", "severity": "warn", "t": 0.0, "sticky": True})
    c.tick(0.05)
    assert _rootcauses(bus) == []


def test_emits_only_on_change():
    bus = EventBus()
    c = Correlator(bus, chain=CHAIN)
    c.update([_c("FXS_NO_LOOP_CURRENT", "AnalogLine", sev="error")])
    c.tick(0.02)
    c.tick(0.04)                      # same snapshot -> no new emission
    assert len(_rootcauses(bus)) == 1


def test_summary_has_no_block_duplication():
    """Banner prepends the block, so the summary must not repeat it (no 'SIP: SIP:')."""
    assert "SIP: SIP" not in Correlator._summary({"block": "SIP", "code": "SIP_487"})
    assert Correlator._summary({"block": "SIP", "code": "SIP_484"}).startswith("address incomplete")
    assert Correlator._summary({"block": "SIP", "code": "SIP_487"}) == "provider returned 487"
