#!/usr/bin/env python3
"""Wireshark-style trace of a CallScope native SIP/RTP call — no capture privileges.

tcpdump/Wireshark need CAP_NET_RAW to sniff loopback. This taps the *same* native
stack at the application layer instead: it prints every SIP message on the wire
(TX/RX, full text, like Wireshark's raw view) and an RTP stream summary including
the first packet's header. For a real .pcap, use the sudo command this prints at the end.

Usage:
    python tools/sip_trace.py [number] [host] [port]
    python tools/sip_trace.py 600 127.0.0.1 5062     # default
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))

from voip.sip_native import SipNativeBackend          # noqa: E402
from voip import rtp                                   # noqa: E402

number = sys.argv[1] if len(sys.argv) > 1 else "600"
host = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1"
port = int(sys.argv[3]) if len(sys.argv) > 3 else 5062

# --- tap the first received RTP packet header (Wireshark "RTP" detail) ---
first_rtp = {}
_orig_unpack = rtp.unpack
def _cap_unpack(data):
    p = _orig_unpack(data)
    if not first_rtp and len(data) >= 12:
        first_rtp.update(p)
    return p
rtp.unpack = _cap_unpack

t0 = time.monotonic()
def wire(direction, text):
    dt = time.monotonic() - t0
    first = text.splitlines()[0]
    arrow = "──►" if direction == "TX" else "◄──"
    print(f"\n┌─ {dt:6.3f}s  {direction} {arrow}  {first}")
    for ln in text.rstrip("\r\n").split("\r\n"):
        print(f"│ {ln}")
    print("└" + "─" * 60)

print(f"# CallScope native SIP/RTP trace → {host}:{port}, dialing {number}\n")
b = SipNativeBackend(registrar=host, registrar_port=port, user="callscope",
                     password="callscope", sip_port=5078, rtp_port=40008)
b.wire_log = wire
b.start_stack()
if not b.available:
    print("!! could not start native stack:", b.error)
    sys.exit(1)

b.start(0.0, number)
while time.monotonic() - t0 < 5:
    b.tick(0.0)
    if b.state in ("INCALL", "FAILED"):
        break
    time.sleep(0.05)

print(f"\n>>> call state: {b.state}")
if b.state == "INCALL":
    print(f">>> RTP negotiated to {b._d.get('remote_rtp')}  — streaming ~2s ...")
    time.sleep(2.0)
    s = b.rtp_stats()
    if first_rtp:
        print(f">>> first RTP packet: PT={first_rtp['pt']} (0=PCMU/G.711µ) "
              f"SSRC=0x{first_rtp['ssrc']:08x} seq={first_rtp['seq']} ts={first_rtp['timestamp']}")
    print(f">>> RTP stream: received={s['received']} loss={s['loss_pct']}% "
          f"jitter={s['jitter_ms']}ms audio={s['audio']}")
    b.hangup(0.0)
    time.sleep(0.5)
    print(f">>> after BYE: {b.state}")
elif b.state == "FAILED":
    print(f">>> failure code: SIP_{b.fail_code}")
b.stop()

print(f"\n# For a real packet capture / Wireshark .pcap:")
print(f"#   sudo tcpdump -i lo -n 'udp and (port {port} or portrange 40000-40010)' -w /tmp/callscope.pcap")
print(f"#   wireshark /tmp/callscope.pcap   →  Telephony ▸ VoIP Calls ▸ Flow Sequence")
