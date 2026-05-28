"""Scenario loading + block registry (Phase C) — config-driven chains."""
from __future__ import annotations
import glob
import json
import os

from blocks.dialer import DialerBlock
from blocks.analog_line import AnalogLineBlock
from blocks.dtmf import DtmfBlock
from blocks.sip import SipBlock
from blocks.codec_rtp import CodecRtpBlock
from blocks.gateway import GatewayBlock

# type string (used in scenario JSON) -> block class
BLOCK_REGISTRY = {
    "Dialer": DialerBlock, "AnalogLine": AnalogLineBlock, "DTMF": DtmfBlock,
    "SIP": SipBlock, "CodecRTP": CodecRtpBlock, "Gateway": GatewayBlock,
}

SCENARIO_DIR = os.path.join(os.path.dirname(__file__), "..", "scenarios")


def load_scenarios(directory: str = SCENARIO_DIR) -> dict:
    """Returns {scenario_id: scenario_dict}, id = filename without .json."""
    out = {}
    for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
        with open(path) as f:
            out[os.path.splitext(os.path.basename(path))[0]] = json.load(f)
    return out


def build_blocks(scenario: dict, sip) -> list:
    """Instantiate the block list for a scenario (SIP gets the shared session)."""
    blocks = []
    for spec in scenario["blocks"]:
        cls = BLOCK_REGISTRY[spec["type"]]
        blk = SipBlock(sip) if spec["type"] == "SIP" else cls()
        for k, v in spec.get("params", {}).items():
            setattr(blk, k, v)
        blocks.append(blk)
    return blocks
