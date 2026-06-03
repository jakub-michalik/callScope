"""CallScope runtime — FastAPI + asynchronous graph clock + WebSocket (Phase 0).

Run:  python backend/run.py   (or uvicorn app.main:app from the backend directory)
Dashboard: http://localhost:8000
"""
from __future__ import annotations
import asyncio
import os
import subprocess
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from engine.const import TICK_S, FRAME_N
from engine.bus import EventBus
from engine.graph import Graph
from blocks.sip import SipSession
from blocks.codec_rtp import _mos
from audio.io import AudioIO
from diag.correlator import Correlator
from scenario import load_scenarios, build_blocks

STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")

SIP_MODES = ("sim", "native", "live")              # selectable SIP backends
EMERGENCY_NUMBERS = {"112", "911", "110", "999"}   # dial as soon as the digits match
INTERDIGIT_TIMEOUT = 2.0                            # else dial after this idle gap
# faults and per-block menus now come from each block (block.FAULTS / block.fault_menu())


class Runtime:
    """Holds the graph, the clock loop and the list of connected clients."""

    def __init__(self):
        self.bus = EventBus(collect=False)
        # live-SIP target (editable from the UI). Default 5062 = the bundled Asterisk
        # container, which binds 5062 to coexist with a host Asterisk on 5060.
        self.sip_host = os.environ.get("CALLSCOPE_ASTERISK", "127.0.0.1")
        self.sip_port = int(os.environ.get("CALLSCOPE_SIP_PORT", "5062"))
        self.sip, self.sip_mode, self.sip_error = self._make_sip()
        self.requested_sip_mode = os.environ.get("CALLSCOPE_SIP_MODE") or "sim"
        self.scenarios = load_scenarios()
        self.scenario_id = "full_chain" if "full_chain" in self.scenarios else next(iter(self.scenarios))
        self.dialed = ""               # digits collected from DTMF while off-hook
        self.last_digit_t = None
        self.active_faults: dict[str, str] = {}
        self.clients: set[asyncio.Queue] = set()
        self.rt_factor = 1.0
        self.bus.subscribe(self._broadcast)
        self.bus.subscribe(self._feed_correlator)
        self.audio = AudioIO().start()         # Phase 1 audio; fallback to generator
        self._wire_sip_audio()                 # mic -> RTP, RTP -> speaker (live backends)
        self._build_graph()                    # builds graph/blocks/refs/correlator from scenario

    def _make_sip(self, mode=None):
        """Build a SIP backend for `mode` (defaults to $CALLSCOPE_SIP_MODE).

          "sim"     -> simulated SipSession (default, no network)
          "native"  -> CallScope's own pure-Python SIP UAC + RTP (no external client)
          "live"    -> legacy baresip->Asterisk adapter
        Falls back to the simulator (and reports the error) if the requested live
        stack cannot bind/connect, so the dashboard always comes up.

        Target host/port come from self.sip_host/self.sip_port (editable in the UI);
        credentials from the environment (CALLSCOPE_SIP_USER / _SIP_PASS).
        """
        if mode is None:
            mode = os.environ.get("CALLSCOPE_SIP_MODE") or "sim"
        host = self.sip_host
        user = os.environ.get("CALLSCOPE_SIP_USER", "callscope")
        pw = os.environ.get("CALLSCOPE_SIP_PASS", "callscope")
        port = self.sip_port
        def _native():
            from voip.sip_native import SipNativeBackend
            return SipNativeBackend(registrar=host, user=user, password=pw,
                                    registrar_port=port).start_stack()
        if mode == "native":
            try:
                a = _native()
                if a.available:
                    return a, "native", None
                return SipSession(), "sim", a.error
            except Exception as e:                       # noqa: BLE001
                return SipSession(), "sim", str(e)
        if mode == "live":
            try:
                from sip_adapter import SipAdapter
                a = SipAdapter(registrar=host, user=user, password=pw,
                               registrar_port=port).start_stack()
                if a.available:
                    return a, "live", None
                err = a.error
            except Exception as e:                       # noqa: BLE001
                err = str(e)
            # baresip unavailable -> fall back to the self-contained native stack
            # (same Asterisk, no external client) rather than dead simulated mode
            try:
                n = _native()
                if n.available:
                    return n, "native", f"baresip unavailable ({err}) — using native"
            except Exception:                            # noqa: BLE001
                pass
            return SipSession(), "sim", err
        return SipSession(), "sim", None

    def switch_sip(self, mode: str, host=None, port=None):
        """Swap the SIP backend at runtime (sim/native/live) and rewire the graph.

        Optional host/port retarget the live backends (e.g. Asterisk on :5062).
        """
        if mode not in SIP_MODES:
            return
        if host:
            self.sip_host = str(host)
        if port:
            try:
                self.sip_port = int(port)
            except (TypeError, ValueError):
                pass
        try:                                             # end any call on the old backend
            self.sip.hangup(self.graph.t)
        except Exception:
            pass
        if hasattr(self.sip, "stop"):                    # release sockets/subprocess
            try:
                self.sip.stop()
            except Exception:
                pass
        self.sip, self.sip_mode, self.sip_error = self._make_sip(mode)
        self.requested_sip_mode = mode
        self._wire_sip_audio()                           # mic -> RTP, RTP -> speaker
        self._build_graph()                              # rewires SipBlock with the new backend
        self.bus.emit("reload", 0.0, {})                 # frontend reloads -> fresh hello

    def _wire_sip_audio(self):
        """Stream the mic into the live SIP call and play received RTP to the speaker."""
        if hasattr(self.sip, "audio_source"):
            self.sip.audio_source = self.audio.read_call_frame if self.audio.has_input else None
        if hasattr(self.sip, "audio_sink"):
            self.sip.audio_sink = self.audio.write_frame if self.audio.has_output else None

    def _build_graph(self):
        """(Re)build the chain from the current scenario JSON."""
        blocks = build_blocks(self.scenarios[self.scenario_id], self.sip)
        self.graph = Graph(blocks, self.bus)
        self.graph.session_id = "s-1"
        self.blocks = {b.name: b for b in blocks}
        # named handles (any may be None depending on the scenario)
        self.dialer = self.blocks.get("Dialer")
        self.line = self.blocks.get("AnalogLine")
        self.dtmf = self.blocks.get("DTMF")
        self.codec = self.blocks.get("CodecRTP")
        self.gateway = self.blocks.get("Gateway")
        self.sipblock = self.blocks.get("SIP")
        self.correlator = Correlator(self.bus, chain=[b.name for b in blocks])
        if self.dialer is not None:
            self.dialer.mic = self.audio.read_frame
        self.sip.reset()
        self.dialed = ""
        self.last_digit_t = None

    def _feed_correlator(self, env: dict):
        if env["ch"] == "diag":
            self.correlator.feed(env["data"])
            d = env["data"]
            if d["code"] == "DTMF_DETECTED" and self.dialer is not None and self.dialer.offhook:
                self._collect_digit(d["measured"]["digit"], env["t"])

    def _collect_digit(self, digit: str, t: float):
        """The box collects dialed digits; it places the SIP call once complete."""
        if self.sip.state in ("CALLING", "RINGING", "ANSWERED", "INCALL"):
            return                                   # a call is already up
        self.dialed += digit
        self.last_digit_t = t
        self.bus.emit("dialed", t, {"number": self.dialed})
        if self.dialed in EMERGENCY_NUMBERS:         # emergency number -> dial immediately
            self._place_call()

    def _place_call(self):
        num, self.dialed = self.dialed, ""
        self.last_digit_t = None
        self.sip.start(self.graph.t, num)
        self.bus.emit("dialed", self.graph.t, {"number": "", "calling": num})

    def _clear_dialed(self):
        self.dialed = ""
        self.last_digit_t = None
        self.bus.emit("dialed", self.graph.t, {"number": "", "calling": ""})

    def _patch_into(self, name: str):
        for p in self.graph.patches:
            if p.dst == name:
                return p.id
        return None

    def _topology(self) -> dict:
        """Layout the frontend renders from: media row + control plane above."""
        blocks = self.graph.blocks
        order = [b.name for b in blocks]
        media = [b for b in blocks if getattr(b, "PLANE", "media") == "media"]
        nodes, mediaX = [], {}
        n = max(len(media) - 1, 1)
        for i, b in enumerate(media):
            x = round(0.09 + i * (0.82 / n), 4)
            mediaX[b.name] = x
            nodes.append({"name": b.name, "plane": "media", "x": x, "y": 0.74})
        medges = [{"from": a.name, "to": b.name, "drive": self._patch_into(b.name)}
                  for a, b in zip(media, media[1:])]
        cedges = []
        for b in blocks:
            if getattr(b, "PLANE", "media") != "control":
                continue
            idx = order.index(b.name)
            left = order[idx - 1]
            right = order[idx + 1] if idx + 1 < len(order) else None
            x = (mediaX.get(left, 0.5) + mediaX.get(right, mediaX.get(left, 0.5))) / 2
            nodes.append({"name": b.name, "plane": "control", "x": round(x, 4), "y": 0.22})
            cedges.append({"from": left, "to": b.name, "drive": f"{left}→{b.name}"})
            if right:
                cedges.append({"from": b.name, "to": right, "drive": f"{b.name}→{right}"})
        return {"nodes": nodes, "media_edges": medges, "ctrl_edges": cedges}

    def _conditions_snapshot(self) -> list[dict]:
        """Conditions gathered generically by the graph + cross-cutting rules."""
        conds = list(self.graph.last_conditions)   # from reached blocks + cuts (generic)
        # one-way audio is genuinely cross-block: call up but media misses the far end
        if (self.gateway is not None and self.sip.state == "INCALL"
                and "Gateway" not in self.graph.reached):
            conds.append({"code": "ONE_WAY_AUDIO", "block": "Gateway",
                          "severity": "error", "t": self.graph.t})
        return conds

    @staticmethod
    def _docker_logs(tail: int = 120) -> dict:
        """Tail the bundled Asterisk/baresip container logs (for the dashboard overlay)."""
        out = {}
        for key, container in (("asterisk", "callscope-asterisk"),
                               ("baresip", "callscope-baresip")):
            try:
                r = subprocess.run(
                    ["docker", "logs", "--timestamps", "--tail", str(tail), container],
                    capture_output=True, text=True, timeout=3)
                text = (r.stdout + r.stderr).strip()
                out[key] = text or "(no output)"
            except Exception as e:                       # noqa: BLE001
                out[key] = f"(unavailable: {type(e).__name__})"
        return out

    def _broadcast(self, env: dict):
        for q in list(self.clients):
            try:
                q.put_nowait(env)
            except asyncio.QueueFull:
                pass

    async def loop(self):
        while True:
            self.graph.tick()
            for m in self.sip.tick(self.graph.t):     # advance SIP state machine
                self.bus.emit("sip", self.graph.t, {**m, "state": self.sip.state})
                if m["code"] == "BYE":                # caller hung up (call timeout) -> on-hook
                    if self.dialer is not None:
                        self.dialer.onhook()
                    self._clear_dialed()
                    self.bus.emit("hook", self.graph.t, {"offhook": False})
            # place the collected number after the inter-digit gap (non-emergency numbers)
            if (self.dialed and self.last_digit_t is not None
                    and self.graph.t - self.last_digit_t > INTERDIGIT_TIMEOUT
                    and self.sip.state in ("IDLE", "TERMINATED", "FAILED")):
                self._place_call()
            self.correlator.update(self._conditions_snapshot())
            self.correlator.tick(self.graph.t)
            self._emit_taps()
            # monitor the generated tone on the speaker (generator mode only -> no feedback),
            # but NOT during a live call: there the SIP backend owns the speaker (far-end
            # audio), and writing the line monitor too would fight for it and halve the volume
            live_call = (self.sip_mode in ("native", "live") and self.sip.state == "INCALL")
            if (not live_call and self.dialer is not None and self.dialer.source == "gen"
                    and self.line is not None and self.line._last_out is not None):
                self.audio.write_frame(self.line._last_out.samples)
            await asyncio.sleep(TICK_S / max(self.rt_factor, 0.05))

    def _emit_taps(self):
        if self.line is not None:
            wf = self.line.tap().waveform
            if wf is not None:
                self.bus.emit("scope", self.graph.t,
                              {"node": "AnalogLine",
                               "wave": [round(float(x), 4) for x in wf[::2]]})
            self.bus.emit("fxs", self.graph.t, self.line.tap().metrics or {})
        if self.dtmf is not None:
            dt = self.dtmf.tap()
            if dt.spectrum is not None:
                self.bus.emit("spectrum", self.graph.t, {"bins": dt.spectrum})
            self.bus.emit("dtmfinfo", self.graph.t, dt.metrics or {})
        if self.gateway is not None:
            metrics = dict(self.gateway.tap().metrics or {})
            # in native/live mode, overlay the REAL measured RTP stats from the
            # backend (actual packets on the wire) onto the simulated panel
            if (self.sip_mode in ("native", "live") and self.sip.state == "INCALL"
                    and hasattr(self.sip, "rtp_stats")):
                live = self.sip.rtp_stats()
                metrics.update(loss_pct=live["loss_pct"], jitter_ms=live["jitter_ms"],
                               received=live.get("received", 0),
                               sent=live.get("sent", 0),
                               pps=live.get("pps", 0),
                               audio=live.get("audio", False),
                               mos=_mos(live["loss_pct"], live["jitter_ms"]))
            self.bus.emit("rtp", self.graph.t, metrics)

    def _use_generator(self):
        """Switch the source to the generator (and tell the UI)."""
        if self.dialer is not None and self.dialer.source != "gen":
            self.dialer.source = "gen"
            self.bus.emit("source", self.graph.t, {"mode": "gen"})

    # --- control ---
    def handle(self, msg: dict):
        cmd = msg.get("cmd")
        a = msg.get("args", {}) or {}
        if cmd == "start_call":
            # dials the number; the box collects the digits and places the SIP call
            num = a.get("number", "112")
            self._use_generator()
            self._clear_dialed()
            self.dialer.dial(num, leadin_ms=200)  # picks up the line + plays the DTMF
            self.bus.emit("hook", self.graph.t, {"offhook": True})
        elif cmd == "press_digit":
            self._use_generator()
            self.dialer.dial(str(a.get("digit", "1")), tone_ms=120, pause_ms=80, leadin_ms=0)
            self.bus.emit("hook", self.graph.t, {"offhook": True})
        elif cmd == "hangup":
            self.dialer.onhook()
            self._clear_dialed()
            self.bus.emit("hook", self.graph.t, {"offhook": False})
            for m in self.sip.hangup(self.graph.t):
                self.bus.emit("sip", self.graph.t, {**m, "state": self.sip.state})
        elif cmd == "cut_patch":
            from diag.diagnostic import Diagnostic
            try:
                p = self.graph.patch(a["src"], a["dst"])
            except KeyError:
                return                            # link not in the current scenario
            p.connected = not p.connected
            self.bus.emit("patchstate", self.graph.t,
                          {"edge": p.id, "connected": p.connected})
            # log event only (the banner is driven by the per-tick snapshot)
            if not p.connected:
                self.bus.diag(Diagnostic("SIGNAL_CUT", p.id, "warn",
                                         message=f"signal cut on {p.id}",
                                         t=self.graph.t, session_id=self.graph.session_id))
        elif cmd == "set_rt_factor":
            self.rt_factor = float(a.get("value", 1.0))
        elif cmd == "set_source":
            mode = a.get("mode", "gen")
            if mode == "mic" and not self.audio.has_input:
                return
            self.dialer.source = mode
            if mode == "mic":
                self.dialer.onhook()                # start on-hook (line idle)
                self._clear_dialed()
                self.bus.emit("hook", self.graph.t, {"offhook": False})
        elif cmd == "set_hook":
            self.dialer.pickup() if a.get("offhook") else self.dialer.onhook()
            if not self.dialer.offhook:
                self._clear_dialed()
            self.bus.emit("hook", self.graph.t, {"offhook": self.dialer.offhook})
        elif cmd == "set_dtmf_param":
            if self.dtmf is not None:
                self.dtmf.detector.set_param(a.get("name", ""), a.get("value", 0))
        elif cmd == "set_scenario":
            sid = a.get("name")
            if sid in self.scenarios and sid != self.scenario_id:
                self.scenario_id = sid
                self._build_graph()
                self.bus.emit("reload", 0.0, {})     # frontend reloads -> fresh hello/topology
        elif cmd == "set_mic_gain":
            if hasattr(self.sip, "tx_gain"):
                try:
                    self.sip.tx_gain = max(0.0, float(a.get("value", 1.0)))
                except (TypeError, ValueError):
                    pass
        elif cmd == "get_logs":
            self.bus.emit("logs", self.graph.t, self._docker_logs())
        elif cmd == "set_sip_mode":
            mode = a.get("mode", self.requested_sip_mode)
            host, port = a.get("host"), a.get("port")
            try:
                target_changed = (host and host != self.sip_host) or \
                                 (port and int(port) != self.sip_port)
            except (TypeError, ValueError):
                target_changed = False
            if mode in SIP_MODES and (mode != self.requested_sip_mode or target_changed):
                self.switch_sip(mode, host, port)
        elif cmd == "inject_fault":
            blk = self.blocks.get(a.get("block"))
            if blk is not None and blk.set_fault(a.get("type", "")):
                self.active_faults[blk.name] = a["type"]
                self.bus.emit("faultstate", self.graph.t,
                              {"block": blk.name, "fault": a["type"]})
        elif cmd == "clear_fault":
            blk = self.blocks.get(a.get("block"))
            if blk is not None:
                blk.clear_fault()
                self.active_faults.pop(blk.name, None)
                self.bus.emit("faultstate", self.graph.t,
                              {"block": blk.name, "fault": None})


rt = Runtime()
app = FastAPI(title="CallScope")


@app.on_event("startup")
async def _start():
    asyncio.create_task(rt.loop())


@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    q: asyncio.Queue = asyncio.Queue(maxsize=2000)
    rt.clients.add(q)
    # initial state
    await sock.send_json({"ch": "hello", "t": 0.0,
                          "data": {"blocks": [b.name for b in rt.graph.blocks],
                                   "edges": [p.id for p in rt.graph.patches],
                                   "audio": rt.audio.status,
                                   "dtmf_params": rt.dtmf.detector.params(),
                                   "block_faults": {b.name: b.fault_menu() for b in rt.graph.blocks},
                                   "active_faults": rt.active_faults,
                                   "topology": rt._topology(),
                                   "scenarios": [{"id": k, "title": v.get("title", k)}
                                                 for k, v in rt.scenarios.items()],
                                   "scenario": rt.scenario_id,
                                   "sip_mode": rt.sip_mode, "sip_error": rt.sip_error,
                                   "sip_modes": list(SIP_MODES),
                                   "requested_sip_mode": rt.requested_sip_mode,
                                   "sip_host": rt.sip_host, "sip_port": rt.sip_port}})

    async def sender():
        while True:
            env = await q.get()
            await sock.send_json(env)

    send_task = asyncio.create_task(sender())
    try:
        while True:
            msg = await sock.receive_json()
            rt.handle(msg)
    except WebSocketDisconnect:
        pass
    finally:
        send_task.cancel()
        rt.clients.discard(q)


if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
