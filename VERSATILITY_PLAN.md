# CallScope — Versatility Plan (detailed)

Goal: turn CallScope from a hard-wired 6-block demo into a **composable, config-driven,
real-capable** signal-chain test bench — culminating in the ability to **place real calls
through Asterisk**.

Guiding rule: **adding / removing / reordering a block must touch ONE place**, not five.
Everything stays test-first (currently 56 tests).

Today's hard-wiring (the 5 places a block currently lives):
1. `app/main.py` — `Graph([...])` block list + `self.blocks` dict
2. `app/main.py` — `_conditions_snapshot()` per-block `if "X" in reached` checks
3. `app/main.py` — `FAULT_FACTORY` + `BLOCK_FAULTS` central maps + SIP special-casing
4. `diag/correlator.py` — `CHAIN` index map
5. `frontend/index.html` — `NODES / POS / MEDIA_EDGES / CTRL_EDGES`

The phases below collapse these to one (the scenario / block definition).

---

## Phase A — Generalize the block contract (foundation)

**Outcome:** conditions, faults and chain order are owned by the blocks, gathered generically.

### A1. `Block.conditions(t) -> list[dict]`
Add to `engine/block.py` base (default `return []`). A block reports its **currently-active
level conditions** from its own `self._last_out.meta` (no args plumbed from the Runtime).

```python
class Block:
    PLANE = "media"                 # "media" | "control"  (for layout, Phase B)
    def conditions(self, t: float) -> list[dict]:
        return []
```

Move the per-block logic onto the blocks:
- `AnalogLineBlock.conditions(t)` → wraps current `FxsDetector.conditions(meta, t)`
- `CodecRtpBlock.conditions(t)` → wraps `RtpDetector.conditions(meta, t)`
- `GatewayBlock.conditions(t)` → wraps `GatewayDetector.conditions(meta, t)`
- `SipBlock.conditions(t)` → `self.sip.conditions(t)` + sets `PLANE = "control"`

(Keep the static `*Detector.conditions(meta,t)` helpers so existing unit tests pass.)

### A2. Graph gathers conditions generically (`engine/graph.py`)
In `tick()`, after processing reached blocks:
```python
self.last_conditions = []
for blk in reached_blocks:
    self.last_conditions += blk.conditions(self.t)
if self.cut_at:
    self.last_conditions.append({"code":"SIGNAL_CUT","block":self.cut_at,"severity":"warn","t":self.t})
```
`Runtime._conditions_snapshot()` shrinks to:
```python
conds = list(self.graph.last_conditions)
# cross-cutting rule (genuinely spans blocks) stays explicit:
if self.sip.state == "INCALL" and "Gateway" not in self.graph.reached:
    conds.append({"code":"ONE_WAY_AUDIO","block":"Gateway","severity":"error","t":t})
return conds
```
→ deletes the hard-coded `if "AnalogLine"/"CodecRTP"/"Gateway" in reached` blocks.

### A3. Faults owned by blocks
Add to `engine/block.py`:
```python
class Block:
    FAULTS = {}     # {type: (FaultSpecClass, kwargs, label)}
    def fault_menu(self) -> list[dict]:
        return [{"type":k,"label":v[2]} for k,v in self.FAULTS.items()]
    def set_fault(self, ftype: str) -> bool:
        spec = self.FAULTS.get(ftype)
        if not spec: return False
        cls,kw,_ = spec; self.fault = cls(**kw); return True
    def clear_fault(self): self.fault = None
```
Each block declares its faults, e.g. `AnalogLineBlock.FAULTS = {"line_noise":(LineNoise,{"snr_dB":6},"Line noise (SNR 6 dB)"), ...}`.

`SipBlock` overrides `set_fault/clear_fault` (sets `self.sip.force_code` instead of a frame fault),
and `FAULTS = {"sip_503":(...,"Force 503"),"sip_486":(...,"Force 486")}`.

`Runtime.handle()` becomes generic — no SIP special case, no `FAULT_FACTORY`/`BLOCK_FAULTS`:
```python
elif cmd == "inject_fault":
    blk = self.blocks.get(a["block"])
    if blk and blk.set_fault(a["type"]):
        self.active_faults[a["block"]] = a["type"]; emit faultstate
elif cmd == "clear_fault":
    blk = self.blocks.get(a["block"])
    if blk: blk.clear_fault(); self.active_faults.pop(...); emit faultstate
```
`hello.block_faults = {b.name: b.fault_menu() for b in graph.blocks}` (with labels — frontend
drops the hard-coded `FAULT_LABELS`).

### A4. Correlator chain from graph order
`Correlator(bus, chain=[b.name for b in graph.blocks])`. Index = position in the list;
links rank at `src_index + 0.5`. Delete the hard-coded `CHAIN`.

### A5. Tests (new)
- `test_block.py`: `conditions()` default empty; `set_fault/clear_fault`; `fault_menu()`.
- `test_graph.py`: `graph.last_conditions` collects from reached blocks; excludes unreached.
- `test_analog_line.py` etc.: `block.conditions(t)` returns the right codes.
- Correlator: pass `chain=` and assert ranking.

**Effort:** ~half a day. **Risk:** low (mechanical). After this, the "5 places" → 2 (graph list + frontend, fixed next).

---

## Phase B — Backend-driven topology (generic frontend)

**Outcome:** the frontend renders the patchbay from a topology the backend sends; adding a
block is backend-only.

### B1. Layout descriptor from the graph (`app/main.py`)
```python
def _topology(self):
    media = [b for b in self.graph.blocks if b.PLANE == "media"]
    ctrl  = [b for b in self.graph.blocks if b.PLANE == "control"]
    order = [b.name for b in self.graph.blocks]
    nodes, medges, cedges = [], [], []
    for i, b in enumerate(media):
        nodes.append({"name":b.name,"plane":"media",
                      "x": 0.09 + i*(0.82/max(len(media)-1,1)), "y":0.74})
    # a media edge between consecutive media blocks; its activity is driven by the
    # patch feeding the 2nd block (which may come through a control block)
    for a,b in zip(media, media[1:]):
        drive = self._patch_into(b.name)          # patch dst == b.name
        medges.append({"from":a.name,"to":b.name,"drive":drive})
    # control block sits above the gap between its graph neighbours
    for b in ctrl:
        idx = order.index(b.name)
        left, right = order[idx-1], order[idx+1]
        nx = (POS_of(left)+POS_of(right))/2
        nodes.append({"name":b.name,"plane":"control","x":nx,"y":0.22})
        cedges.append({"from":left,"to":b.name,"drive":f"{left}→{b.name}"})
        cedges.append({"from":b.name,"to":right,"drive":f"{b.name}→{right}"})
    return {"nodes":nodes,"media_edges":medges,"ctrl_edges":cedges}
```
`_patch_into(name)` returns the graph patch id whose `dst == name` (e.g. for CodecRTP it is
`SIP→CodecRTP`, so the visual `DTMF→CodecRTP` media edge animates on real media flow).

### B2. `hello` carries the topology
`hello.data.topology = self._topology()`.

### B3. Frontend renders generically (`frontend/index.html`)
Delete literal `NODES / POS / MEDIA_EDGES / CTRL_EDGES`. On `hello`:
```js
buildPatchbay(topology);   // creates node divs from nodes[], stores POS, MEDIA/CTRL edge lists
buildBlogs(topology.nodes.map(n=>n.name));
```
`drawEdges()` iterates `topology.media_edges` / `ctrl_edges`, reading `edgeAct[e.drive]`.
Node health/fault badges keyed by node name (unchanged).

### B4. Tests
- `test_topology` (backend): media nodes ordered, control block placed between neighbours,
  media edge `drive` = patch into the downstream block.

**Effort:** ~1 day (mostly frontend rework + careful edge mapping). **Risk:** medium (frontend).
After this: **adding a block = register it + put it in the scenario. Nothing else.**

---

## Phase C — Config-driven chains (scenarios)

**Outcome:** the chain is data, not code; multiple topologies without edits.

### C1. Block registry (`blocks/__init__.py` or `engine/registry.py`)
```python
BLOCK_REGISTRY = {"Dialer":DialerBlock,"AnalogLine":AnalogLineBlock,"DTMF":DtmfBlock,
                  "SIP":SipBlock,"CodecRTP":CodecRtpBlock,"Gateway":GatewayBlock}
```

### C2. Scenario JSON (`scenarios/full_chain.json`)
```json
{ "name":"Full FXS→VoIP chain",
  "blocks":[{"type":"Dialer"},{"type":"AnalogLine"},{"type":"DTMF"},
            {"type":"SIP"},{"type":"CodecRTP"},{"type":"Gateway"}] }
```
Other scenarios: `analog_only.json` (Dialer→AnalogLine→DTMF), `voip_only.json`, etc.

### C3. Runtime builds graph from a scenario
```python
def build_graph(scenario):
    blocks = []
    for spec in scenario["blocks"]:
        cls = BLOCK_REGISTRY[spec["type"]]
        blk = cls(sip=self.sip) if spec["type"]=="SIP" else cls()
        for k,v in spec.get("params",{}).items(): setattr(blk,k,v)
        blocks.append(blk)
    return Graph(blocks, self.bus)
```
`GET /scenarios` lists them; `set_scenario {name}` rebuilds the graph live (re-emit hello/topology).

### C4. Tests
- Build each scenario from JSON; assert block order + topology.

**Effort:** ~half a day. **Risk:** low. Depends on A+B.

---

## Phase D — Real calls through Asterisk (the big one)

**Outcome:** dialed digits place a **real** SIP call to Asterisk; the ladder shows **real
codes**; real RTP flows; Wireshark sees the same packets. Fault injection overlays on top.

```
[CallScope analog side: Dialer→FXS→DTMF (sim/mic)]
        │ dialed number
        ▼
[SipAdapter]  ⇄  SIP/RTP  ⇄  [Asterisk @ Docker]  →  dialplan → echo / PSTN / Twilio
```

### D1. Adapter interface (drop-in for `SipSession`)
`SipAdapter` exposes the SAME methods so `SipBlock` and the Runtime are unchanged:
`start(t,number)`, `tick(t)->list[msg]`, `hangup(t)`, `.state`, `.media`, `conditions(t)`, `.force_code`.
Internally it bridges a real SIP stack thread → a thread-safe queue drained by `tick()`.

### D2. Stack choice
- **baresip** as a subprocess (recommended PoC): no pjsip build; control via its `ctrl_tcp`
  module (send `{"command":"dial","params":"112"}`, read JSON events). Map call states
  (`CALL_ESTABLISHED`, `CALL_CLOSED`, `CALL_RINGING`) → our `INVITE/180/200/BYE`.
- **pjsua2** (fuller, Python): real per-call callbacks + a SIP log callback to capture actual
  INVITE/100/180/200 lines. Heavier build.
- For the **authentic per-message ladder**, add a **tshark/sngrep tap** on the loopback
  interface parsing real SIP → exact codes. (Optional; baresip states are enough for a PoC.)

### D3. Asterisk in Docker (`asterisk/docker-compose.yml`, `pjsip.conf`, `extensions.conf`)
- One PJSIP endpoint `callscope` (auth, aor) that CallScope registers to.
- Dialplan:
  - `112` → Answer + Playback/echo (success path),
  - `5031` → `Hangup(503)` style (returns 503 — demo SIP failure with a REAL code),
  - `486x` → busy.
- Codecs: ulaw/alaw.

### D4. Real RTP + media
- baresip/pjsua2 carry RTP. Feed audio: baresip `ausrc` = a WAV (the dialed tones or a voice
  sample); pjsua2 = WAV player port. Tap stats: baresip `callstat` / pjsua2 `call.getInfo()`
  RTP stats → our `rtp` events.

### D5. Real fault injection
- **packet loss / jitter** become REAL via `tc netem` on the container/loopback:
  `tc qdisc add dev lo root netem loss 10% delay 40ms` — authentic impairment Wireshark sees.
- **503** = dial the dialplan extension that returns 503.
- This replaces the simulated faults with genuine network/SIP faults on the real leg.

### D6. Mode switch
A `mode` flag: `sim` (current `SipSession`) vs `live` (`SipAdapter`). Same UI, same event bus.
`hello.sip_mode` shows which. The chain's analog/DTMF side is unchanged in both.

### D7. Effort & risk
- PoC (baresip + Asterisk docker, one real call, ladder from call states): **~1 day**.
- Solid (registration, RTP stats, fault mapping, per-message ladder via tshark): **~2–4 days**.
- Risk: baresip/pjsip build, codec/NAT/dialplan negotiation, thread↔asyncio bridging,
  audio device contention (use null/file devices, not the speaker we already use).

---

## Phase E — Record / replay / export

- **Record:** subscribe a logger to the bus → one JSONL per session in `recordings/`.
- **Replay:** read JSONL, re-emit on the bus by timestamp → UI replays a past call; the UI
  cannot tell live from replay (same bus).
- **Export report:** filter `severity≥warn` + `rootcause` + SIP/Q.850 codes → a Markdown/PDF
  diagnostic report. Directly realises the offer's *"Log- und Protokollanalyse"*.

**Effort:** ~1 day. **Risk:** low.

---

## Phase F — Scriptable scenarios (test automation / CI)

- A scenario script (JSON): timed actions `[{t:0,cmd:start_call,number:112},{t:3,cmd:inject_fault,block:CodecRTP,type:packet_loss},…]`.
- A headless runner drives the Runtime, asserts the expected `rootcause` → a **regression test**.
- Recorded JSONL sessions become **fixtures**. Ties to the offer's *"Testautomatisierung / CI"*.

**Effort:** ~half a day. **Risk:** low.

---

## Recommended order & dependency

```
A (generalize contract)  ──┐
                           ├─→ B (backend topology) ──→ C (scenarios)
                           │
                           └─────────────────────────→ D (Asterisk)   [independent of B/C]
E (replay/export) and F (scripts) can land any time after A.
```

1. **A** — foundation (~½ day), unlocks everything, removes the 5-place hard-wiring.
2. **B** — generic frontend (~1 day).
3. **D** PoC — real Asterisk call (~1 day), the biggest "wow".
4. **C, E, F** — as time allows.

Each phase is independently shippable and test-first. Backend stays event-bus-driven, so the
UI never needs to know whether SIP is simulated or real (Phase D), or whether events are live
or replayed (Phase E).
