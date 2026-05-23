"""Generalized block contract tests (Phase A: conditions / faults / chain)."""
from engine.block import Block
from engine.graph import Graph
from engine.bus import EventBus
from engine.faults import WeakTone
from blocks.dialer import DialerBlock
from blocks.analog_line import AnalogLineBlock


def test_block_conditions_default_empty():
    assert Block().conditions(0.0) == []


def test_set_and_clear_fault():
    d = DialerBlock()
    assert d.set_fault("weak_tone") is True
    assert isinstance(d.fault, WeakTone)
    d.clear_fault()
    assert d.fault is None
    assert d.set_fault("does_not_exist") is False


def test_fault_menu_has_labels():
    menu = AnalogLineBlock().fault_menu()
    types = {m["type"] for m in menu}
    assert {"line_noise", "no_loop_current", "hum_50hz"} <= types
    assert all(m.get("label") for m in menu)


def test_graph_collects_conditions_from_reached_blocks():
    d = DialerBlock(); d.dial("5", leadin_ms=0)
    line = AnalogLineBlock(); line.set_fault("line_noise")
    g = Graph([d, line], EventBus())
    g.run(6)
    codes = [c["code"] for c in g.last_conditions]
    assert "LINE_LOW_SNR" in codes


def test_cut_appears_in_graph_conditions():
    d = DialerBlock(); d.dial("5", leadin_ms=0)
    g = Graph([d, AnalogLineBlock()], EventBus())
    g.patch("Dialer", "AnalogLine").connected = False
    g.run(4)
    assert any(c["code"] == "SIGNAL_CUT" for c in g.last_conditions)
