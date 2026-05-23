"""Integration test: Dialer -> AnalogLine -> DTMF (DESIGN.md §13a)."""
from blocks.dialer import DialerBlock
from blocks.analog_line import AnalogLineBlock
from blocks.dtmf import DtmfBlock
from engine.graph import Graph
from engine.bus import EventBus


def _build(number):
    d = DialerBlock()
    d.dial(number, tone_ms=100, pause_ms=100, leadin_ms=200)
    g = Graph([d, AnalogLineBlock(), DtmfBlock()], EventBus())
    g.session_id = "s-test"
    return g


def _detected(bus):
    return [e["data"]["measured"]["digit"]
            for e in bus.by_ch("diag") if e["data"]["code"] == "DTMF_DETECTED"]


def test_end_to_end_decodes_dialed_number():
    g = _build("123")
    g.run(50)
    assert _detected(g.bus) == ["1", "2", "3"]


def test_flow_events_on_all_edges():
    g = _build("5")
    g.run(40)
    edges = {e["data"]["edge"] for e in g.bus.by_ch("flow")}
    assert "Dialer→AnalogLine" in edges
    assert "AnalogLine→DTMF" in edges
    # during the tone, flow is active somewhere along the chain
    assert any(e["data"]["active"] for e in g.bus.by_ch("flow"))


def test_cut_patch_stops_flow_and_decode():
    g = _build("5")
    g.patch("AnalogLine", "DTMF").connected = False
    g.run(40)
    # token stopped: no active flow on the cut link
    cut_edge = [e for e in g.bus.by_ch("flow") if e["data"]["edge"] == "AnalogLine→DTMF"]
    assert cut_edge and all(not e["data"]["active"] for e in cut_edge)
    # nothing reached DTMF -> no detection
    assert _detected(g.bus) == []
