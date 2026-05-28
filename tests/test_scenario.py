"""Config-driven chain tests (Phase C)."""
from scenario import load_scenarios, build_blocks, BLOCK_REGISTRY
from blocks.sip import SipSession
from engine.graph import Graph
from engine.bus import EventBus


def test_scenarios_load():
    sc = load_scenarios()
    assert "full_chain" in sc
    assert [b["type"] for b in sc["full_chain"]["blocks"]][0] == "Dialer"


def test_build_each_scenario_matches_spec():
    sip = SipSession()
    for sid, sc in load_scenarios().items():
        blocks = build_blocks(sc, sip)
        g = Graph(blocks, EventBus())
        assert [b.name for b in g.blocks] == [s["type"] for s in sc["blocks"]]


def test_registry_covers_all_scenario_types():
    for sc in load_scenarios().values():
        for spec in sc["blocks"]:
            assert spec["type"] in BLOCK_REGISTRY


def test_analog_only_has_no_voip_blocks():
    sip = SipSession()
    blocks = build_blocks(load_scenarios()["analog_only"], sip)
    names = [b.name for b in blocks]
    assert "SIP" not in names and "CodecRTP" not in names
    assert names == ["Dialer", "AnalogLine", "DTMF"]
