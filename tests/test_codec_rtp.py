"""Codec/RTP block tests (DESIGN.md §13a)."""
from blocks.codec_rtp import CodecRtpBlock
from engine.faults import PacketLoss
from dsp.tone_gen import dtmf_samples
from harness import run_block
from signals import framize


def _rtp(out):
    return [f.meta.get("rtp") for f in out if f.meta.get("rtp", {}).get("audio")]


def test_clean_stream_high_mos():
    frames = framize(dtmf_samples("5", 300))
    out, _ = run_block(CodecRtpBlock(), frames)
    s = _rtp(out)[-1]
    assert s["loss_pct"] == 0.0
    assert s["mos"] > 4.0
    assert s["pt"] == 0                 # PCMU
    assert s["seq"] >= 5               # sequence advanced


def test_packet_loss_lowers_mos_and_flags():
    frames = framize(dtmf_samples("5", 600))
    block = CodecRtpBlock()
    out, _ = run_block(block, frames, fault=PacketLoss(pct=30.0))
    s = _rtp(out)[-1]
    assert s["loss_pct"] > 1.0
    assert s["mos"] < 4.0
    conds = block.detector.conditions(out[-2].meta, 1.0)
    assert any(c["code"] == "RTP_LOSS_SPIKE" for c in conds)


def test_silence_no_packets():
    from dsp.tone_gen import silence
    out, _ = run_block(CodecRtpBlock(), framize(silence(200)))
    assert _rtp(out) == []             # no media -> no RTP packets
