"""Native SIP UAC + RTP (Phase D, no external client) — drop-in for SipSession.

CallScope is the SIP user agent itself: it opens UDP sockets, sends real
INVITE/ACK/BYE to Asterisk, handles digest challenges, and streams G.711 RTP.
Same interface as SipSession (start/tick/hangup/state/media/conditions/reset).
"""
from __future__ import annotations
import os
import queue
import random
import re
import socket
import threading
import time

import numpy as np

from voip import digest, rtp

_DEBUG = bool(os.environ.get("SIP_DEBUG"))   # set SIP_DEBUG=1 to trace SIP I/O

SIP_REASON = {"200": "OK", "180": "Ringing", "100": "Trying",
              "486": "Busy Here", "503": "Service Unavailable",
              "404": "Not Found", "408": "Request Timeout", "407": "Proxy Auth"}

_FAIL_HINT = {"486": "callee busy (Q.850 #17)",
              "503": "congestion / no circuit (Q.850 #34)",
              "404": "number not in dialplan (Q.850 #1)",
              "408": "no answer / request timeout",
              "603": "call declined / endpoint unreachable"}


def _parse(data: bytes) -> dict:
    text = data.decode("utf-8", "replace")
    head, _, body = text.partition("\r\n\r\n")
    lines = head.split("\r\n")
    start = lines[0]
    headers: dict = {}
    for ln in lines[1:]:
        if ":" in ln:
            k, v = ln.split(":", 1)
            headers.setdefault(k.strip().lower(), []).append(v.strip())
    return {"start": start, "headers": headers, "body": body}


def _hdr(msg: dict, name: str, default=""):
    vals = msg["headers"].get(name.lower())
    return vals[0] if vals else default


def _sdp_remote_rtp(body: str):
    ip = (re.search(r"c=IN IP4 (\S+)", body) or [None, None])[1]
    port = (re.search(r"m=audio (\d+) ", body) or [None, None])[1]
    return (ip, int(port)) if ip and port else (None, None)


def _uri_in_angle(value: str):
    """Extract a SIP URI from a header value like `"X" <sip:a@b>;tag=..`."""
    m = re.search(r"<([^>]+)>", value)
    return m.group(1) if m else (value.split(";")[0].strip() if value else "")


def _sdp_codec(body: str):
    """First audio codec from an SDP body, e.g. 'PCMU/8000'."""
    m = re.search(r"a=rtpmap:\d+ ([\w./-]+)", body or "")
    return m.group(1) if m else "?"


class SipNativeBackend:
    def __init__(self, registrar="127.0.0.1", user="callscope", password="callscope",
                 local_ip="127.0.0.1", sip_port=5070, rtp_port=40000, register=True,
                 registrar_port=5060):
        self.registrar = registrar
        self.registrar_port = registrar_port
        self.user, self.password = user, password
        self.local_ip = local_ip
        self.sip_port, self.rtp_port = sip_port, rtp_port
        self.do_register = register
        self.force_code = None
        self.available = False
        self.error: str | None = None
        self.wire_log = None          # optional callable(dir, text): taps raw SIP on the wire
        self.audio_source = None      # optional callable()->float32[160]: mic frames to send as RTP
        self.audio_sink = None        # optional callable(float32[160]): play received RTP
        self.tx_gain = 3.0            # mic gain before encoding (speech is naturally low level)
        self._q: queue.Queue = queue.Queue()
        self._sip: socket.socket | None = None
        self._rtp: socket.socket | None = None
        self._running = False
        self._reg = {}
        self.reset()

    # --- SipSession-compatible API ---
    def reset(self):
        self.state = "IDLE"
        self.media = False
        self.fail_code = None
        self._d = {}            # dialog: callid, ftag, ttag, branch, cseq, ruri, remote_rtp
        self._stats = rtp.RtpStats()
        self._rtp_on = False
        self._auth_tries = 0
        self._sent = 0

    def start(self, t: float, number: str):
        self.reset()
        self.number = number
        self.state = "CALLING"
        self._emit("out", "INVITE", "INVITE",
                   f"→ sip:{number}@{self.registrar}:{self.registrar_port} · "
                   f"SDP offer G.711µ (PCMU/8000), RTP :{self.rtp_port}")
        self._invite()

    def hangup(self, t: float):
        if self.state in ("INCALL", "ANSWERED", "RINGING"):
            if self._d.get("inbound"):
                self._bye_inbound()
            else:
                self._bye()
        return []

    def _emit(self, d, code, label, detail=""):
        self._q.put((d, code, label, detail))

    def tick(self, t: float) -> list:
        out = []
        while True:
            try:
                item = self._q.get_nowait()
            except queue.Empty:
                break
            d, code, label = item[0], item[1], item[2]
            detail = item[3] if len(item) > 3 else ""
            out.append({"dir": d, "code": code, "label": label, "detail": detail, "t": t})
        return out

    def conditions(self, t: float) -> list:
        if self.state == "FAILED" and self.fail_code:
            return [{"code": f"SIP_{self.fail_code}", "block": "SIP", "severity": "error",
                     "t": t, "protocol_code": f"{self.fail_code} {SIP_REASON.get(self.fail_code, '')}"}]
        return []

    def rtp_stats(self) -> dict:
        s = self._stats.snapshot()
        s["audio"] = self._rtp_on
        s["sent"] = self._sent
        return s

    # --- lifecycle ---
    def start_stack(self) -> "SipNativeBackend":
        try:
            self._sip = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sip.bind((self.local_ip, self.sip_port))
            self._sip.settimeout(0.3)
            self._rtp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._rtp.bind((self.local_ip, self.rtp_port))
            self._rtp.settimeout(0.1)
            self._running = True
            # separate RX and a steadily-paced 20 ms TX so send timing never
            # depends on when packets arrive (otherwise audio gets choppy)
            self._threads = [threading.Thread(target=self._sip_loop, daemon=True),
                             threading.Thread(target=self._rtp_rx_loop, daemon=True),
                             threading.Thread(target=self._rtp_tx_loop, daemon=True)]
            for th in self._threads:
                th.start()
            self.available = True
        except Exception as e:                       # noqa: BLE001
            # don't leak a half-bound socket (e.g. SIP bound, RTP port busy)
            self.stop()
            self.error = f"{type(e).__name__}: {e}"
            self.available = False
        return self

    def stop(self):
        self._running = False
        # join the receive loops so their sockets are released before any rebind
        for th in getattr(self, "_threads", []):
            try:
                th.join(timeout=0.6)
            except Exception:
                pass
        self._threads = []
        for s in (self._sip, self._rtp):
            try:
                s and s.close()
            except Exception:
                pass
        self._sip = self._rtp = None

    # --- SIP request building ---
    def _new_dialog(self):
        self._d = {"callid": f"{random.getrandbits(48):x}@callscope",
                   "ftag": f"{random.getrandbits(32):x}",
                   "branch": f"z9hG4bK{random.getrandbits(32):x}",
                   "cseq": 1, "ttag": None, "remote_rtp": (None, None)}

    def _send(self, msg: str):
        if self.wire_log:
            self.wire_log("TX", msg)
        self._sip.sendto(msg.encode(), (self.registrar, self.registrar_port))

    def _via(self):
        return f"SIP/2.0/UDP {self.local_ip}:{self.sip_port};branch={self._d['branch']};rport"

    def _contact(self):
        return f"<sip:{self.user}@{self.local_ip}:{self.sip_port}>"

    def _sdp(self):
        sid = random.getrandbits(31)
        return ("v=0\r\n"
                f"o=callscope {sid} {sid} IN IP4 {self.local_ip}\r\n"
                "s=callscope\r\nt=0 0\r\n"
                f"c=IN IP4 {self.local_ip}\r\n"
                f"m=audio {self.rtp_port} RTP/AVP 0\r\n"
                "a=rtpmap:0 PCMU/8000\r\na=sendrecv\r\n")

    def _invite(self, auth: str | None = None):
        if auth is None:
            self._new_dialog()
        else:
            self._d["cseq"] += 1
            self._d["branch"] = f"z9hG4bK{random.getrandbits(32):x}"
        ruri = f"sip:{self.number}@{self.registrar}"
        sdp = self._sdp()
        to = f"<sip:{self.number}@{self.registrar}>"
        if self._d.get("ttag"):
            to += f";tag={self._d['ttag']}"
        h = [f"INVITE {ruri} SIP/2.0", f"Via: {self._via()}", "Max-Forwards: 70",
             f"From: <sip:{self.user}@{self.registrar}>;tag={self._d['ftag']}",
             f"To: {to}", f"Call-ID: {self._d['callid']}",
             f"CSeq: {self._d['cseq']} INVITE", f"Contact: {self._contact()}"]
        if auth:
            h.append(f"Authorization: {auth}")
        h += ["Content-Type: application/sdp", f"Content-Length: {len(sdp)}", "", sdp]
        self._d["ruri"] = ruri
        self._send("\r\n".join(h))

    def _ack(self):
        to = f"<sip:{self.number}@{self.registrar}>"
        if self._d.get("ttag"):
            to += f";tag={self._d['ttag']}"
        h = [f"ACK {self._d['ruri']} SIP/2.0",
             f"Via: SIP/2.0/UDP {self.local_ip}:{self.sip_port};branch=z9hG4bK{random.getrandbits(32):x};rport",
             "Max-Forwards: 70",
             f"From: <sip:{self.user}@{self.registrar}>;tag={self._d['ftag']}",
             f"To: {to}", f"Call-ID: {self._d['callid']}",
             f"CSeq: {self._d['cseq']} ACK", "Content-Length: 0", "", ""]
        self._send("\r\n".join(h))

    def _bye(self):
        self._d["cseq"] += 1
        to = f"<sip:{self.number}@{self.registrar}>"
        if self._d.get("ttag"):
            to += f";tag={self._d['ttag']}"
        h = [f"BYE {self._d['ruri']} SIP/2.0",
             f"Via: SIP/2.0/UDP {self.local_ip}:{self.sip_port};branch=z9hG4bK{random.getrandbits(32):x};rport",
             "Max-Forwards: 70",
             f"From: <sip:{self.user}@{self.registrar}>;tag={self._d['ftag']}",
             f"To: {to}", f"Call-ID: {self._d['callid']}",
             f"CSeq: {self._d['cseq']} BYE", "Content-Length: 0", "", ""]
        self._send("\r\n".join(h))
        self.state = "TERMINATED"
        self.media = False
        self._rtp_on = False
        self._emit("out", "BYE", "BYE", "CallScope hung up")

    # --- receive loops ---
    def _sip_loop(self):
        while self._running:
            try:
                data, _ = self._sip.recvfrom(8192)
            except (socket.timeout, OSError):
                continue
            text = data.decode("utf-8", "replace")
            if _DEBUG:
                print("SIP RX:", text.splitlines()[0])
            if self.wire_log:
                self.wire_log("RX", text)
            try:
                self._on_sip(_parse(data))
            except Exception:                     # never let the loop die
                if _DEBUG:
                    import traceback
                    traceback.print_exc()
                continue

    def _on_sip(self, msg: dict):
        start = msg["start"]
        if start.startswith("INVITE "):     # someone is calling us -> UAS, auto-answer
            self._on_invite(msg)
            return
        if start.startswith("ACK "):        # inbound ACK confirming our 200 -> stay in call
            return
        if start.startswith("CANCEL "):     # caller hung up before we answered
            self._respond_200(msg)
            self.state = "TERMINATED"
            self.media = False
            self._rtp_on = False
            return
        if start.startswith("BYE "):        # far end hung up
            self._respond_200(msg)
            self.state = "TERMINATED"
            self.media = False
            self._rtp_on = False
            self._emit("in", "BYE", "BYE", "far end hung up")
            self._emit("out", "200", "200 OK", "released the call")
            return
        m = re.match(r"SIP/2\.0 (\d{3}) (.*)", start)
        if not m:
            return
        code = m.group(1)
        reason = m.group(2).strip()
        server = _hdr(msg, "server")
        cseq = _hdr(msg, "cseq")
        to_tag = (re.search(r"tag=([^;\s]+)", _hdr(msg, "to")) or [None, None])[1]
        if code == "100":
            self._emit("in", "100", "100 Trying",
                       f"provider accepted the request{' · ' + server if server else ''}")
            return
        if code in ("180", "183"):
            self.state = "RINGING"
            self._emit("in", code, f"{code} {reason}", "remote end is ringing")
        elif code in ("401", "407"):
            ch = _hdr(msg, "www-authenticate") or _hdr(msg, "proxy-authenticate")
            if ch and "INVITE" in cseq and self._auth_tries < 2:
                self._auth_tries += 1
                chp = digest.parse_challenge(ch)
                self._emit("in", code, f"{code} {reason}",
                           f'digest challenge · realm="{chp.get("realm", "")}" '
                           f'nonce={chp.get("nonce", "")[:10]}…')
                self._ack()                  # ACK the 401 (INVITE transaction)
                auth = digest.authorization("INVITE", self._d["ruri"], self.user,
                                            self.password, chp)
                self._emit("out", "INVITE", "INVITE (authenticated)",
                           f"+ Authorization: Digest username=\"{self.user}\", response=…")
                self._invite(auth=auth)
            elif "INVITE" in cseq:           # auth kept being rejected -> give up
                self._ack()
                self.state = "FAILED"
                self.fail_code = "401"
                self._emit("in", "401", "401 Unauthorized",
                           "auth rejected (wrong user/pass or realm)")
        elif code == "200" and "INVITE" in cseq:
            self._d["ttag"] = to_tag        # dialog established -> remember the To-tag
            self._d["remote_rtp"] = _sdp_remote_rtp(msg["body"])
            ip, port = self._d["remote_rtp"]
            codec = _sdp_codec(msg["body"])
            self._ack()
            self.state = "INCALL"
            self.media = True
            self._rtp_on = True
            self._emit("in", "200", "200 OK",
                       f"answer {codec} · RTP ⇄ {ip}:{port}"
                       f"{' · ' + server if server else ''}")
            self._emit("out", "ACK", "ACK", "dialog confirmed — media flowing")
        elif code == "200" and "BYE" in cseq:
            self.state = "TERMINATED"
            self._emit("in", "200", "200 OK", "BYE confirmed — call released")
        elif code[0] in ("4", "5", "6") and "INVITE" in cseq:
            self._ack()
            self.state = "FAILED"
            self.media = False
            self.fail_code = code
            self._emit("in", code, f"{code} {reason or SIP_REASON.get(code, '')}".strip(),
                       _FAIL_HINT.get(code, "call setup failed"))

    def _respond_200(self, msg: dict):
        h = ["SIP/2.0 200 OK", f"Via: {_hdr(msg, 'via')}", f"From: {_hdr(msg, 'from')}",
             f"To: {_hdr(msg, 'to')}", f"Call-ID: {_hdr(msg, 'call-id')}",
             f"CSeq: {_hdr(msg, 'cseq')}", "Content-Length: 0", "", ""]
        self._send("\r\n".join(h))

    # --- UAS: answer an incoming call (test bench auto-answers) ---
    def _send_response(self, msg: dict, status: str, to_tagged: str,
                       body: str = "", ctype: str | None = None, extra=()):
        h = [f"SIP/2.0 {status}", f"Via: {_hdr(msg, 'via')}", f"From: {_hdr(msg, 'from')}",
             f"To: {to_tagged}", f"Call-ID: {_hdr(msg, 'call-id')}",
             f"CSeq: {_hdr(msg, 'cseq')}"]
        h += list(extra)
        if body:
            if ctype:
                h.append(f"Content-Type: {ctype}")
            h += [f"Content-Length: {len(body)}", "", body]
        else:
            h += ["Content-Length: 0", "", ""]
        self._send("\r\n".join(h))

    def _on_invite(self, msg: dict):
        """Accept an inbound INVITE: 100 Trying -> 200 OK (+SDP) -> stream RTP."""
        to = _hdr(msg, "to")
        our_tag = f"{random.getrandbits(32):x}"
        to_tagged = to if ";tag=" in to else f"{to};tag={our_tag}"
        cseq_num = (_hdr(msg, "cseq").split() or ["1"])[0]
        # dialog state for an inbound call (used when WE send the BYE)
        self._d = {
            "inbound": True,
            "callid": _hdr(msg, "call-id"),
            "bye_ruri": _uri_in_angle(_hdr(msg, "contact")) or f"sip:{self.registrar}",
            "bye_from": to_tagged,            # our identity (To of the INVITE + our tag)
            "bye_to": _hdr(msg, "from"),      # the caller (From of the INVITE, their tag)
            "remote_rtp": _sdp_remote_rtp(msg["body"]),
            "cseq": int(cseq_num) if cseq_num.isdigit() else 1,
        }
        self._stats = rtp.RtpStats()
        self._auth_tries = 0
        self._send_response(msg, "100 Trying", to_tagged)
        sdp = self._sdp()
        self._send_response(msg, "200 OK", to_tagged, body=sdp, ctype="application/sdp",
                            extra=[f"Contact: {self._contact()}", "Allow: INVITE, ACK, BYE, CANCEL"])
        self.state = "INCALL"
        self.media = True
        self._rtp_on = True
        self.fail_code = None
        caller = _uri_in_angle(_hdr(msg, "from"))
        ip, port = self._d["remote_rtp"]
        self._emit("in", "INVITE", "INVITE (incoming)",
                   f"from {caller} · offer {_sdp_codec(msg['body'])}")
        self._emit("out", "200", "200 OK",
                   f"auto-answer · RTP ⇄ {ip}:{port}, our :{self.rtp_port}")

    def _bye_inbound(self):
        """Send BYE for a call we answered (we are the callee)."""
        self._d["cseq"] = self._d.get("cseq", 1) + 1
        h = [f"BYE {self._d['bye_ruri']} SIP/2.0",
             f"Via: SIP/2.0/UDP {self.local_ip}:{self.sip_port};"
             f"branch=z9hG4bK{random.getrandbits(32):x};rport",
             "Max-Forwards: 70",
             f"From: {self._d['bye_from']}", f"To: {self._d['bye_to']}",
             f"Call-ID: {self._d['callid']}", f"CSeq: {self._d['cseq']} BYE",
             "Content-Length: 0", "", ""]
        self._send("\r\n".join(h))
        self.state = "TERMINATED"
        self.media = False
        self._rtp_on = False
        self._emit("out", "BYE", "BYE", "CallScope hung up (callee leg)")

    def _rtp_rx_loop(self):
        """Receive RTP: drive loss/jitter stats and (optionally) play to the speaker."""
        while self._running:
            try:
                data, _ = self._rtp.recvfrom(2048)
            except (socket.timeout, OSError):
                continue
            if self._rtp_on and len(data) >= 12:
                p = rtp.unpack(data)
                self._stats.on_packet(p["seq"], p["timestamp"], time.monotonic())
                if self.audio_sink and p["payload"]:
                    try:
                        pcm = rtp.ulaw_to_pcm16(p["payload"])
                        self.audio_sink(pcm.astype(np.float32) / 32768.0)
                    except Exception:
                        pass

    def _rtp_tx_loop(self):
        """Send one 20 ms RTP frame on a steady clock (mic audio, else comfort noise)."""
        seq, ts, ssrc = random.getrandbits(16), 0, random.getrandbits(32)
        silence = rtp.pcm16_to_ulaw(np.zeros(160, dtype=np.int16))
        next_send = time.monotonic() + 0.02
        while self._running:
            delay = next_send - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            next_send += 0.02
            if time.monotonic() - next_send > 0.1:    # fell badly behind -> resync clock
                next_send = time.monotonic() + 0.02
            ip, port = self._d.get("remote_rtp", (None, None))
            if not (self._rtp_on and ip and port):
                continue
            payload = silence
            if self.audio_source:
                try:
                    f = np.asarray(self.audio_source(), dtype=np.float32)
                    pcm = np.clip(f * 32767.0 * self.tx_gain, -32768, 32767).astype(np.int16)
                    payload = rtp.pcm16_to_ulaw(pcm)
                except Exception:
                    payload = silence
            try:
                self._rtp.sendto(rtp.pack(payload, seq, ts, ssrc), (ip, port))
                self._sent += 1
            except OSError:
                pass
            seq = (seq + 1) & 0xFFFF
            ts = (ts + 160) & 0xFFFFFFFF
