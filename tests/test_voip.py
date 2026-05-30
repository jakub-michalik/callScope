"""Native SIP/RTP building-block tests (digest, RTP, G.711)."""
import numpy as np

from voip import digest
from voip import rtp


def test_digest_rfc2617_vector():
    """RFC 2617 §3.5 worked example must produce the documented response."""
    r = digest.response(
        "GET", "/dir/index.html", "Mufasa", "Circle Of Life",
        "testrealm@host.com", "dcd98b7102dd2f0e8b11d0f600bfb0c093",
        qop="auth", nc="00000001", cnonce="0a4f113b")
    assert r == "6629fae49393a05397450978507c4ef1"


def test_digest_parse_challenge():
    h = 'Digest realm="asterisk",nonce="abc/def",qop="auth",opaque="xy",algorithm=MD5'
    c = digest.parse_challenge(h)
    assert c["realm"] == "asterisk"
    assert c["nonce"] == "abc/def"
    assert c["qop"] == "auth"
    assert c["opaque"] == "xy"


def test_digest_authorization_header_shape():
    c = {"realm": "asterisk", "nonce": "n0", "qop": "auth"}
    hdr = digest.authorization("REGISTER", "sip:127.0.0.1", "u", "p", c)
    assert hdr.startswith("Digest ")
    assert 'username="u"' in hdr and "qop=auth" in hdr and "response=" in hdr


def test_rtp_pack_unpack_roundtrip():
    payload = b"\x01\x02\x03\x04"
    pkt = rtp.pack(payload, seq=7, timestamp=160, ssrc=0xDEADBEEF, pt=0, marker=True)
    u = rtp.unpack(pkt)
    assert u["version"] == 2 and u["pt"] == 0 and u["marker"] is True
    assert u["seq"] == 7 and u["timestamp"] == 160 and u["ssrc"] == 0xDEADBEEF
    assert u["payload"] == payload


def test_g711_ulaw_roundtrip_is_close():
    pcm = (np.sin(2 * np.pi * 770 * np.arange(160) / 8000) * 8000).astype(np.int16)
    ulaw = rtp.pcm16_to_ulaw(pcm)
    assert len(ulaw) == 160                      # 1 byte per sample
    back = rtp.ulaw_to_pcm16(ulaw)
    # mu-law is lossy but should track the signal (correlation high)
    corr = np.corrcoef(pcm.astype(float), back.astype(float))[0, 1]
    assert corr > 0.99


def test_rtp_stats_loss_and_order():
    st = rtp.RtpStats()
    for i, seq in enumerate([0, 1, 2, 4]):        # seq 3 missing
        st.on_packet(seq, ts=seq * 160, arrival_s=i * 0.02)
    snap = st.snapshot()
    assert snap["received"] == 4
    assert snap["loss_pct"] > 0                    # 1 of 5 expected lost
