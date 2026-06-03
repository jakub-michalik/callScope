"""Real SIP via baresip (Phase D) — a drop-in for SipSession.

CallScope becomes a real SIP user agent: it spawns `baresip` (which registers to
Asterisk) and drives it over baresip's ctrl_tcp interface (netstring-framed JSON).
Real INVITE / answer / hangup happen against Asterisk; the call-flow ladder is
approximated from baresip's call states. Wireshark/sngrep see the real SIP/RTP.

Same interface as SipSession (start/tick/hangup/state/media/conditions/force_code/
reset) so SipBlock and the Runtime are unchanged. Defensive: if baresip or the
control connection is unavailable, `.available` stays False and the Runtime falls
back to the simulated SipSession.
"""
from __future__ import annotations
import json
import os
import queue
import re
import socket
import subprocess
import threading
import time

SIP_REASON = {
    "200": "OK", "180": "Ringing", "404": "Not Found", "408": "Request Timeout",
    "486": "Busy Here", "503": "Service Unavailable",
}


# ---- pure helpers (unit-tested without baresip) -----------------------------

def parse_netstrings(buf: bytes):
    """Split a buffer of netstrings (`<len>:<payload>,`). Returns (payloads, rest)."""
    payloads, rest = [], buf
    while True:
        m = re.match(rb"(\d+):", rest)
        if not m:
            break
        n = int(m.group(1))
        start = m.end()
        if len(rest) < start + n + 1:        # payload + trailing comma not complete
            break
        payloads.append(rest[start:start + n])
        rest = rest[start + n + 1:]          # skip the comma
    return payloads, rest


_FAIL_HINT = {"486": "callee busy (Q.850 #17)",
              "503": "congestion / no circuit (Q.850 #34)",
              "404": "number not in dialplan (Q.850 #1)",
              "408": "no answer / request timeout",
              "603": "call declined / endpoint unreachable"}


def map_event(ev: dict) -> dict | None:
    """Map a baresip ctrl_tcp event to {msgs, state, media}. Returns None to ignore.

    baresip reports call *state* (not raw SIP messages), so the detail here is the
    peer / cause / registration baresip exposes — the per-message 401/digest/re-INVITE
    is handled internally by baresip and not visible on this channel (unlike the
    native backend, which is the SIP UA itself).
    """
    if not ev.get("event"):
        return None
    typ = ev.get("type", "")
    peer = ev.get("peeruri", "") or ev.get("peerdisplayname", "")
    aor = ev.get("accountaor", "")
    if typ in ("REGISTER_OK",):
        return {"state": "IDLE", "media": False,
                "msgs": [("in", "200", "REGISTER 200 OK",
                          f"registered as {aor} (baresip ↔ Asterisk)")]}
    if typ in ("REGISTER_FAIL", "UNREGISTERING") and typ == "REGISTER_FAIL":
        return {"state": "IDLE", "media": False,
                "msgs": [("in", "4xx", "REGISTER failed",
                          f"registration rejected for {aor}")]}
    if typ in ("CALL_RINGING", "CALL_PROGRESS"):
        return {"state": "RINGING", "media": False,
                "msgs": [("in", "180", "180 Ringing",
                          f"remote ringing{' · ' + peer if peer else ''}")]}
    if typ == "CALL_ESTABLISHED":
        return {"state": "INCALL", "media": True,
                "msgs": [("in", "200", "200 OK",
                          f"call established{' with ' + peer if peer else ''} · "
                          "media via baresip ↔ Asterisk"),
                         ("out", "ACK", "ACK", "dialog confirmed — media flowing")]}
    if typ == "CALL_CLOSED":
        param = str(ev.get("param", ""))
        code = re.match(r"\s*(\d{3})", param)
        if code and code.group(1)[0] in "456":      # a SIP failure code
            c = code.group(1)
            return {"state": "FAILED", "media": False, "fail_code": c,
                    "msgs": [("in", c, f"{c} {SIP_REASON.get(c, '')}".strip(),
                              _FAIL_HINT.get(c, param or "call setup failed"))]}
        return {"state": "TERMINATED", "media": False,
                "msgs": [("out", "BYE", "BYE",
                          f"call closed{' · ' + param if param else ''}")]}
    return None


# ---- the adapter ------------------------------------------------------------

class SipAdapter:
    def __init__(self, registrar="127.0.0.1", user="callscope", password="callscope",
                 ctrl_host="127.0.0.1", ctrl_port=4444, baresip_bin="baresip",
                 conf_dir=None, registrar_port=5060):
        self.registrar = registrar
        self.registrar_port = registrar_port
        self.user, self.password = user, password
        self.ctrl_host, self.ctrl_port = ctrl_host, ctrl_port
        self.baresip_bin = baresip_bin
        self.conf_dir = conf_dir or os.path.expanduser("~/.callscope-baresip")
        self._q: queue.Queue = queue.Queue()
        self._sock: socket.socket | None = None
        self._proc: subprocess.Popen | None = None
        self.available = False
        self.error: str | None = None
        self.force_code = None      # not used in live mode (the dialplan decides)
        self.reset()

    # --- SipSession-compatible API ---
    def reset(self):
        self.state = "IDLE"
        self.media = False
        self.fail_code = None

    def start(self, t: float, number: str):
        self.reset()
        self.state = "CALLING"
        self._q.put(("out", "INVITE", "INVITE",
                     f"→ sip:{number}@{self.registrar}:{self.registrar_port} (via baresip) · "
                     "offer G.711 (PCMU/PCMA); baresip handles 401/digest internally"))
        self._send({"command": "dial", "params": number})

    def hangup(self, t: float):
        if self.state in ("INCALL", "ANSWERED", "RINGING"):
            self._send({"command": "hangup"})
        return []

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

    def _ctrl_is_up(self) -> bool:
        """Is a baresip ctrl_tcp already listening? (i.e. the Docker baresip container)."""
        try:
            with socket.create_connection((self.ctrl_host, self.ctrl_port), timeout=0.5):
                return True
        except OSError:
            return False

    # --- lifecycle ---
    def start_stack(self) -> "SipAdapter":
        try:
            # Prefer an already-running baresip (the Docker `baresip` service): just
            # connect to its ctrl_tcp. Otherwise spawn a host-installed baresip.
            if not self._ctrl_is_up():
                self._write_config()
                self._proc = subprocess.Popen(
                    [self.baresip_bin, "-f", self.conf_dir],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._connect_ctrl()
            threading.Thread(target=self._read_loop, daemon=True).start()
            self.available = True
        except Exception as e:           # noqa: BLE001
            self.error = f"{type(e).__name__}: {e}"
            self.available = False
        return self

    def stop(self):
        for closer in (lambda: self._sock and self._sock.close(),
                       lambda: self._proc and self._proc.terminate()):
            try:
                closer()
            except Exception:
                pass

    # --- internals ---
    def _write_config(self):
        os.makedirs(self.conf_dir, exist_ok=True)
        with open(os.path.join(self.conf_dir, "config"), "w") as f:
            f.write(f"module\t\tctrl_tcp.so\nctrl_tcp_listen\t{self.ctrl_host}:{self.ctrl_port}\n"
                    "module\t\tstdio.so\nmodule\t\taccount.so\n"
                    # menu.so provides the `dial`/`hangup` commands the ctrl_tcp interface drives;
                    # without it baresip answers "command not found (dial)".
                    "module\t\tmenu.so\n"
                    # a codec module is mandatory: without it baresip loads 0 codecs and
                    # silently refuses to register the account.
                    "module\t\tg711.so\n"
                    "audio_source\t\taubypass,nil\naudio_player\t\taubypass,nil\n")
        with open(os.path.join(self.conf_dir, "accounts"), "w") as f:
            # target the registrar port explicitly (default 5060): if it is left off and a
            # different SIP service owns 5060, the REGISTER goes to the wrong server.
            f.write(f"<sip:{self.user}@{self.registrar}:{self.registrar_port};transport=udp>"
                    f";auth_pass={self.password};regint=60;audio_codecs=pcmu,pcma\n")

    def _connect_ctrl(self, retries=20):
        last = None
        for _ in range(retries):
            try:
                self._sock = socket.create_connection((self.ctrl_host, self.ctrl_port), timeout=2)
                return
            except OSError as e:
                last = e
                time.sleep(0.3)
        raise last or OSError("ctrl_tcp connect failed")

    def _send(self, cmd: dict):
        if not self._sock:
            return
        payload = json.dumps(cmd).encode()
        try:
            self._sock.sendall(f"{len(payload)}:".encode() + payload + b",")
        except OSError as e:
            self.error = str(e)

    def _read_loop(self):
        buf = b""
        while True:
            try:
                data = self._sock.recv(4096)
            except OSError:
                break
            if not data:
                break
            buf += data
            payloads, buf = parse_netstrings(buf)
            for p in payloads:
                try:
                    ev = json.loads(p)
                except ValueError:
                    continue
                tr = map_event(ev)
                if tr is None:
                    continue
                self.state = tr["state"]
                self.media = tr["media"]
                if tr.get("fail_code"):
                    self.fail_code = tr["fail_code"]
                for msg in tr["msgs"]:
                    self._q.put(msg)            # (dir, code, label[, detail])
