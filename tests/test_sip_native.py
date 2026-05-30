"""Integration test: the native SIP UAC against a local, correct mini-UAS.

This proves CallScope's own SIP stack drives a full call end-to-end —
INVITE -> 401 challenge -> digest re-INVITE -> 200 OK (+SDP) -> ACK -> BYE —
and that the digest it sends is *verifiable* by an independent verifier
(here, our own digest.response, used server-side). It deliberately does NOT
depend on any external Asterisk image: the UAS below is the reference peer.
"""
import re
import socket
import threading
import time

import numpy as np
import pytest

from voip import digest, rtp
from voip.sip_native import SipNativeBackend, _parse, _hdr

REALM = "testrealm"
NONCE = "abc123nonce"


def _free_udp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _mini_uas(sock, password, uas_rtp_port, state, stop):
    """A minimal SIP UAS that challenges, verifies digest, and answers."""
    sock.settimeout(0.2)
    uas_tag = "uas-tag-9f"
    while not stop.is_set():
        try:
            data, addr = sock.recvfrom(8192)
        except socket.timeout:
            continue
        msg = _parse(data)
        start = msg["start"]
        via, frm, to = _hdr(msg, "via"), _hdr(msg, "from"), _hdr(msg, "to")
        callid, cseq = _hdr(msg, "call-id"), _hdr(msg, "cseq")
        to_tagged = to if ";tag=" in to else f"{to};tag={uas_tag}"

        def send(lines):
            sock.sendto(("\r\n".join(lines)).encode(), addr)

        if start.startswith("INVITE"):
            authz = _hdr(msg, "authorization")
            if not authz:
                state["challenged"] += 1
                send(["SIP/2.0 401 Unauthorized", f"Via: {via}", f"From: {frm}",
                      f"To: {to_tagged}", f"Call-ID: {callid}", f"CSeq: {cseq}",
                      f'WWW-Authenticate: Digest realm="{REALM}", nonce="{NONCE}", '
                      "qop=\"auth\", algorithm=MD5", "Content-Length: 0", "", ""])
                continue
            p = digest.parse_challenge(authz)            # parses k=v pairs in the header
            expected = digest.response(
                "INVITE", p["uri"], p["username"], password, p["realm"], p["nonce"],
                p.get("qop"), p.get("nc", "00000001"), p.get("cnonce", "0a4f113b"))
            if expected == p.get("response"):
                state["auth_ok"] = True
                sdp = ("v=0\r\n"
                       "o=uas 1 1 IN IP4 127.0.0.1\r\ns=uas\r\nt=0 0\r\n"
                       "c=IN IP4 127.0.0.1\r\n"
                       f"m=audio {uas_rtp_port} RTP/AVP 0\r\n"
                       "a=rtpmap:0 PCMU/8000\r\na=sendrecv\r\n")
                send(["SIP/2.0 200 OK", f"Via: {via}", f"From: {frm}",
                      f"To: {to_tagged}", f"Call-ID: {callid}", f"CSeq: {cseq}",
                      "Contact: <sip:600@127.0.0.1>", "Content-Type: application/sdp",
                      f"Content-Length: {len(sdp)}", "", sdp])
            else:
                state["auth_fail"] += 1
                send(["SIP/2.0 401 Unauthorized", f"Via: {via}", f"From: {frm}",
                      f"To: {to_tagged}", f"Call-ID: {callid}", f"CSeq: {cseq}",
                      f'WWW-Authenticate: Digest realm="{REALM}", nonce="{NONCE}", '
                      "qop=\"auth\", algorithm=MD5", "Content-Length: 0", "", ""])
        elif start.startswith("ACK"):
            state["ack"] = True
        elif start.startswith("BYE"):
            state["bye"] = True
            send(["SIP/2.0 200 OK", f"Via: {via}", f"From: {frm}", f"To: {to_tagged}",
                  f"Call-ID: {callid}", f"CSeq: {cseq}", "Content-Length: 0", "", ""])


def _run(password_client, password_server):
    uas_port = _free_udp_port()
    uas_rtp_port = _free_udp_port()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", uas_port))
    state = {"challenged": 0, "auth_ok": False, "auth_fail": 0, "ack": False, "bye": False}
    stop = threading.Event()
    th = threading.Thread(target=_mini_uas,
                          args=(sock, password_server, uas_rtp_port, state, stop), daemon=True)
    th.start()

    b = SipNativeBackend(registrar="127.0.0.1", registrar_port=uas_port,
                         user="callscope", password=password_client,
                         sip_port=_free_udp_port(), rtp_port=_free_udp_port(),
                         register=False).start_stack()
    assert b.available, b.error
    try:
        b.start(0.0, "600")
        t0 = time.time()
        while time.time() - t0 < 4:
            b.tick(time.time() - t0)
            if b.state in ("INCALL", "FAILED"):
                break
            time.sleep(0.05)
        result_state = b.state
        remote = b._d.get("remote_rtp")
        if b.state == "INCALL":
            b.hangup(0.0)
            time.sleep(0.3)
        final = b.state
    finally:
        b.stop()
        stop.set()
        th.join(timeout=1)
        sock.close()
    return state, result_state, remote, final, uas_rtp_port


def test_native_uac_completes_authenticated_call():
    """Correct password -> challenge, verified digest, 200, ACK, INCALL, BYE."""
    state, mid, remote, final, uas_rtp = _run("s3cret", "s3cret")
    assert state["challenged"] >= 1, "UAS never challenged"
    assert state["auth_ok"], "UAS did not accept the digest"
    assert mid == "INCALL", f"call did not connect (state={mid})"
    assert remote == ("127.0.0.1", uas_rtp), f"remote RTP not parsed from SDP: {remote}"
    assert state["ack"], "UAC never sent ACK"
    assert final == "TERMINATED", f"BYE did not terminate cleanly (state={final})"
    assert state["bye"], "UAS never received BYE"


def test_native_uac_rejects_wrong_password():
    """Wrong password -> UAS re-challenges, UAC gives up -> FAILED 401."""
    state, mid, _remote, _final, _ = _run("wrong-pass", "s3cret")
    assert state["challenged"] >= 1
    assert not state["auth_ok"], "UAS wrongly accepted a bad digest"
    assert state["auth_fail"] >= 1, "UAS never saw a bad digest attempt"
    assert mid == "FAILED", f"bad auth should fail, got {mid}"


def test_native_answers_incoming_call():
    """UAS path: an inbound INVITE is auto-answered (100 -> 200+SDP), ACK, then BYE."""
    caller_port = _free_udp_port()
    caller_rtp = _free_udp_port()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", caller_port))
    sock.settimeout(2)
    # the native backend replies to (registrar, registrar_port) -> point it at our caller
    b = SipNativeBackend(registrar="127.0.0.1", registrar_port=caller_port,
                         user="callscope", password="callscope",
                         sip_port=_free_udp_port(), rtp_port=_free_udp_port()).start_stack()
    assert b.available, b.error
    try:
        invite = "\r\n".join([
            f"INVITE sip:callscope@127.0.0.1:{b.sip_port} SIP/2.0",
            f"Via: SIP/2.0/UDP 127.0.0.1:{caller_port};branch=z9hG4bKinbound;rport",
            "Max-Forwards: 70",
            "From: <sip:phone@127.0.0.1>;tag=callertag",
            f"To: <sip:callscope@127.0.0.1:{b.sip_port}>",
            "Call-ID: inbound-call-1@test",
            "CSeq: 1 INVITE",
            f"Contact: <sip:phone@127.0.0.1:{caller_port}>",
            "Content-Type: application/sdp",
            "Content-Length: 129", "",
            "v=0\r\no=phone 1 1 IN IP4 127.0.0.1\r\ns=p\r\nc=IN IP4 127.0.0.1\r\n"
            f"t=0 0\r\nm=audio {caller_rtp} RTP/AVP 0\r\na=rtpmap:0 PCMU/8000\r\n"]).encode()
        sock.sendto(invite, ("127.0.0.1", b.sip_port))

        codes, answered_sdp = [], None
        for _ in range(6):
            try:
                data, _a = sock.recvfrom(4096)
            except socket.timeout:
                break
            msg = _parse(data)
            mm = re.match(r"SIP/2\.0 (\d{3})", msg["start"])
            if mm:
                codes.append(mm.group(1))
                if mm.group(1) == "200" and "m=audio" in msg["body"]:
                    answered_sdp = msg["body"]
                    # ACK the 200 OK
                    sock.sendto(("\r\n".join([
                        f"ACK sip:callscope@127.0.0.1:{b.sip_port} SIP/2.0",
                        f"Via: SIP/2.0/UDP 127.0.0.1:{caller_port};branch=z9hG4bKack;rport",
                        "From: <sip:phone@127.0.0.1>;tag=callertag",
                        f"To: <sip:callscope@127.0.0.1:{b.sip_port}>;tag=x",
                        "Call-ID: inbound-call-1@test", "CSeq: 1 ACK",
                        "Content-Length: 0", "", ""])).encode(), ("127.0.0.1", b.sip_port))
                    break

        assert "200" in codes, f"native did not answer 200 OK (got {codes})"
        assert answered_sdp and "m=audio" in answered_sdp, "200 OK carried no SDP answer"
        assert b.state == "INCALL", f"native not in call (state={b.state})"

        # native hangs up -> it should send us a BYE
        b.hangup(0.0)
        got_bye = False
        for _ in range(4):
            try:
                data, _a = sock.recvfrom(4096)
            except socket.timeout:
                break
            if data.startswith(b"BYE "):
                got_bye = True
                break
        assert got_bye, "native did not send BYE on hangup"
        assert b.state == "TERMINATED"
    finally:
        b.stop()
        sock.close()


def test_native_streams_mic_audio_as_rtp():
    """With an audio_source wired, outgoing RTP carries that signal, not silence."""
    recv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    recv.bind(("127.0.0.1", 0))
    recv_port = recv.getsockname()[1]
    recv.settimeout(1.0)
    b = SipNativeBackend(sip_port=_free_udp_port(), rtp_port=_free_udp_port()).start_stack()
    assert b.available, b.error
    try:
        phase = {"n": 0}
        def tone():                                  # 440 Hz at half scale
            n = np.arange(160)
            f = 0.5 * np.sin(2 * np.pi * 440 * (phase["n"] + n) / 8000.0)
            phase["n"] += 160
            return f.astype(np.float32)
        b.audio_source = tone
        b._d["remote_rtp"] = ("127.0.0.1", recv_port)   # point RTP at our listener
        b._rtp_on = True
        payloads = []
        for _ in range(6):
            try:
                data, _a = recv.recvfrom(2048)
            except socket.timeout:
                break
            payloads.append(rtp.unpack(data)["payload"])
        assert len(payloads) >= 3, "no RTP packets received"
        pcm = rtp.ulaw_to_pcm16(payloads[2])
        assert np.abs(pcm).mean() > 200, "outgoing RTP looks like silence, not mic audio"
    finally:
        b.stop()
        recv.close()
