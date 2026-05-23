"""Golden vectors + fault cases for the DTMF detector (DESIGN.md §13a)."""
import numpy as np
import pytest

from blocks.dtmf import DtmfBlock
from dsp.tone_gen import dtmf_samples, silence
from engine.const import DTMF_KEYS
from harness import run_block, codes, detected_digit, has_error
from signals import dual_tone, speech_like, framize

ALL_DIGITS = [k for row in DTMF_KEYS for k in row]


def _detected_list(diags):
    return [d.measured.get("digit") for d in diags if d.code == "DTMF_DETECTED"]


@pytest.mark.parametrize("digit", ALL_DIGITS)
def test_roundtrip_all_digits(digit):
    """Each of the 16 digits: 100 ms tone -> correct detection, no errors."""
    frames = framize(dual_tone(digit, 100))
    _, diags = run_block(DtmfBlock(), frames)
    assert detected_digit(diags) == digit
    assert not has_error(diags)


def test_too_short():
    """A tone clearly shorter than the threshold -> TOO_SHORT, no detection."""
    block = DtmfBlock()
    block.detector.set_param("min_tone_ms", 100)   # require 5 frames
    frames = framize(dual_tone("7", 60))           # ~3 frames
    _, diags = run_block(block, frames)
    assert "DTMF_TOO_SHORT" in codes(diags)
    assert detected_digit(diags) is None


def test_twist_out_of_range():
    frames = framize(dual_tone("7", 100, twist_dB=18))   # > TWIST_MAX_DB (12)
    _, diags = run_block(DtmfBlock(), frames)
    assert "DTMF_TWIST_OOR" in codes(diags)
    assert detected_digit(diags) is None


def test_low_level_clean_detected():
    """Gain-independent: a quiet but CLEAN tone is still detected."""
    frames = framize(dual_tone("7", 100, level=0.01))
    _, diags = run_block(DtmfBlock(), frames)
    assert detected_digit(diags) == "7"


def test_low_snr_rejected():
    """A tone buried in noise (low SNR) -> rejected, no digit."""
    from signals import white_noise
    sig = (dual_tone("7", 120, level=0.03) + white_noise(120, level=0.3)).astype(np.float32)
    frames = framize(sig)
    _, diags = run_block(DtmfBlock(), frames)
    assert detected_digit(diags) is None
    assert "DTMF_REJECTED" in codes(diags)


def test_collision_rejected():
    """Two digits at once -> no dominant peak per group -> rejected."""
    mix = (dtmf_samples("1", 100) + dtmf_samples("9", 100)).astype(np.float32)
    _, diags = run_block(DtmfBlock(), framize(mix))
    assert "DTMF_REJECTED" in codes(diags)
    assert detected_digit(diags) is None


def test_speech_no_false_positive():
    frames = framize(speech_like(300))
    _, diags = run_block(DtmfBlock(), frames)
    assert detected_digit(diags) is None
    assert "DTMF_TWIST_OOR" not in codes(diags)


def test_sequence_of_digits():
    """Sequence 1-2-3 with pauses -> three detections in order."""
    seq = []
    for d in "123":
        seq.append(dtmf_samples(d, 100))
        seq.append(silence(60))
    sig = np.concatenate(seq).astype(np.float32)
    _, diags = run_block(DtmfBlock(), framize(sig))
    assert _detected_list(diags) == ["1", "2", "3"]


def test_repeated_same_digit_with_pause():
    """Same digit pressed twice (tone-pause-tone) -> detected twice."""
    sig = np.concatenate([
        dtmf_samples("5", 100), silence(80),
        dtmf_samples("5", 100),
    ]).astype(np.float32)
    _, diags = run_block(DtmfBlock(), framize(sig))
    assert _detected_list(diags) == ["5", "5"]


def test_same_digit_no_pause_detected_once():
    """Same digit held continuously (no pause) -> a single detection."""
    sig = dtmf_samples("5", 200)
    _, diags = run_block(DtmfBlock(), framize(sig))
    assert _detected_list(diags) == ["5"]
