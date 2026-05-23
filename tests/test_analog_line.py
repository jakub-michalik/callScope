"""Analog Line / FXS block cases (DESIGN.md §13a)."""
from blocks.analog_line import AnalogLineBlock, LOOP_OFFHOOK_MA
from engine.faults import NoLoopCurrent, LineNoise
from harness import run_block, codes
from signals import dual_tone, framize


def test_offhook_loop_current():
    frames = framize(dual_tone("5", 100), meta={"offhook": True})
    out, diags = run_block(AnalogLineBlock(), frames)
    # off-hook -> loop current within range, no errors
    assert out[0].meta["loop_mA"] == LOOP_OFFHOOK_MA
    assert out[0].meta["line_voltage"] == 7.0
    assert "FXS_NO_LOOP_CURRENT" not in codes(diags)


def test_no_loop_current_fault():
    frames = framize(dual_tone("5", 100), meta={"offhook": True})
    _, diags = run_block(AnalogLineBlock(), frames, fault=NoLoopCurrent())
    assert "FXS_NO_LOOP_CURRENT" in codes(diags)


def test_line_noise_low_snr():
    frames = framize(dual_tone("5", 100), meta={"offhook": True})
    _, diags = run_block(AnalogLineBlock(), frames, fault=LineNoise(snr_dB=10))
    assert "LINE_LOW_SNR" in codes(diags)


def test_onhook_no_loop():
    frames = framize(dual_tone("5", 100), meta={"offhook": False})
    out, _ = run_block(AnalogLineBlock(), frames)
    assert out[0].meta["loop_mA"] == 0.0
    assert out[0].meta["line_voltage"] == 48.0
