# CallScope — Detailed design (implementation-ready)

Complements `PLAN.md` with implementation details: data types, constants, block algorithms, detection thresholds, transport protocol, UI component tree. Goal: a document from which code can be written directly.

---

## 0. Decisions adopted (defaults — changeable with a single word)

| Decision | Choice | Rationale |
|---|---|---|
| Name | **CallScope** | working |
| Audio rate (telephony) | **8 000 Hz**, mono, float32 | telephony standard; DTMF/G.711 natively 8k |
| Frame | **20 ms = 160 samples** | = RTP ptime (1 audio frame ↔ 1 RTP packet) |
| Clock tick | **20 ms**, with adjustable real-time factor (0.1×–1×) | slowdown for demo |
| Telephony (Phase 1–2) | **simulation in Python**, abstraction for real Asterisk (Phase 3) | hybrid |
| Display | **browser on PC** (`localhost:8000`) | presentation on a laptop |
| Transport | WebSocket: **JSON** (events) + **binary Float32** (waveform/spectrum) | performance on the Pi |
| Detection codes | **real protocols**: SIP response, Q.850, RFC 4733, RTP metrics | real-world telecom protocol language |

---

## 1. Global parameters (`engine/const.py`)

```
FS            = 8000          # Hz, sample rate
FRAME_MS      = 20            # ms
FRAME_N       = 160           # samples = FS * FRAME_MS/1000
DTMF_WIN_N    = 205           # Goertzel detection window (~25.6 ms, ITU-class)
TICK_S        = 0.020         # 20 ms
RT_FACTOR     = 1.0           # 1.0 = real-time; 0.3 = slower (demo)
SCOPE_FPS     = 30            # oscilloscope frames to UI
SCOPE_POINTS  = 160           # points per scope frame (= 1 frame)
SPECTRUM_FPS  = 20
```

DTMF frequencies and Goertzel bins `k = round(DTMF_WIN_N * f / FS)`:

```
low group:    697→k=18 · 770→k=20 · 852→k=22 · 941→k=24
high group:  1209→k=31 · 1336→k=34 · 1477→k=38 · 1633→k=42
coeff_i = 2*cos(2π*k_i/DTMF_WIN_N)
```

Digit map `(row, column) → character`:
```
        1209 1336 1477 1633
697      1    2    3    A
770      4    5    6    B
852      7    8    9    C
941      *    0    #    D
```

---

## 2. Data model (`schemas/`, pydantic)

### 2.1 Frame (flows through the graph — an in-memory object, NOT over WS)
```
Frame:
  seq:      int            # frame number since session start
  t:        float          # simulation time [s]
  samples:  np.float32[160]# audio (or buffer after DSP)
  meta:     dict           # e.g. {"line_voltage": 48.0, "loop_mA": 23.0}
```

### 2.2 WS envelope (every JSON message)
```
Envelope:
  ch:   str    # channel: "flow" | "state" | "diag" | "rootcause" | "sip" | "rtp" | "log" | "control"
  t:    float  # simulation time
  data: object # payload depends on ch
```

### 2.3 FlowEvent  (ch="flow") — flow animation
```
{ edge: "line→dtmf", active: bool, level: float(0..1), kind: "analog"|"dtmf"|"rtp" }
```

### 2.4 StateEvent (ch="state") — block/patch state
```
{ node: "DTMF", state: "idle"|"active"|"error"|"done", health: "green"|"amber"|"red" }
```

### 2.5 Diagnostic (ch="diag") — detected problem
```
{ code: "SIP_503", block: "SIP", severity: "info"|"warn"|"error",
  protocol_code: "503 Service Unavailable",   # real code if applicable
  message: "Provider unavailable",
  measured: { ... }, session_id: "s-12" }
```

### 2.6 RootCause (ch="rootcause") — correlator result
```
{ session_id, root_code, root_block, summary: "media cut, signaling OK",
  consequences: ["RTP_NO_AUDIO","MOS_LOW","SINK_NO_AUDIO"] }
```

### 2.7 ControlMsg (ch="control", UI→backend)
```
{ cmd: "start_call"|"hangup"|"press_digit"|"inject_fault"|"clear_fault"|"cut_patch"|"set_rt_factor"|"record"|"replay",
  args: { ... } }
```

### 2.8 Binary waveform frame (separate WS, binary)
```
[ uint8 stream_id ][ uint8 node_id ][ Float32LE × N ]   # scope/spectrum, bez JSON-overhead
```

---

## 3. Clock and scheduler (`engine/clock.py`)

```
asyncio loop:
  while running:
    t0 = perf_counter()
    graph.tick(t_sim)              # 1 frame through the whole graph (section 4)
    t_sim += FRAME_MS/1000
    throttle to (TICK_S / RT_FACTOR)   # RT_FACTOR<1 → slower
```
The clock is **independent of audio I/O**: audio (sounddevice) has its own callback, and the `audio↔engine` bridge is a lock-free ring buffer (section 9). Thanks to this, the demo slowdown (RT_FACTOR) does not break the sound — at RT_FACTOR<1 the audio plays normally while the visualization slows down ("inspect" mode).

---

## 4. Block and Patch contract (`engine/block.py`, `engine/patch.py`)

```python
class Block:
    name: str
    enabled: bool = True           # DISCONNECT (bypass = pass through unchanged / cut = silence)
    fault: FaultSpec = None        # INJECTED FAULT
    detector: Detector             # DETECTION → Diagnostic[]
    def process(self, frame: Frame) -> Frame: ...   # DEDICATED DSP PATH
    def tap(self) -> TapData: ...                    # PREVIEW (scope/spectrum/metrics)

class Patch:                       # link between blocks
    src, dst: str
    connected: bool = True         # cut
    impairment: Impairment = None  # delay_ms, atten_dB, noise, loss_pct, reorder

class Graph:
    blocks: list[Block]; patches: list[Patch]
    def tick(self, t):
        frame = SOURCE_FRAME(t)              # mic or generator
        for blk, patch in zip(blocks, patches):
            if not blk.enabled: frame = passthrough_or_silence(blk, frame)
            else:
                if blk.fault: frame = blk.fault.apply(frame)   # BEFORE DSP
                frame = blk.process(frame)
            for d in blk.detector.check(frame): bus.emit("diag", d)
            bus.emit_binary(blk.id, blk.tap())                 # scope/spectrum
            if patch and not patch.connected:
                bus.emit("flow", edge=patch.id, active=False); break  # STOP — token halts
            if patch and patch.impairment: frame = patch.impairment.apply(frame)
            bus.emit("flow", edge=patch.id, active=is_active(frame), level=rms(frame))
        correlator.feed(...)                  # LEVEL 3
```

The order within a block is fixed: **fault → DSP → detector → tap → (patch: cut/impair) → flow-event**. This guarantees that the detector sees the signal *after* the fault has been injected (i.e. it detects the consequence of the fault).

---

## 5. Blocks — details

### 5.1 Dialer (`blocks/dialer.py`)
- **State machine:** `ONHOOK → OFFHOOK → DIAL(seq) → DONE`.
- **DTMF synthesis:** for digit `d`: `x[n] = 0.5*(sin(2π f_low n/FS) + sin(2π f_high n/FS))`; envelope with a 5 ms ramp (anti-click). Tone duration 100 ms, pause 100 ms (configurable).
- **Source:** internal generator **or** mic (switch `source: gen|mic`).
- **tap:** waveform + current digit.

### 5.2 AnalogLine / FXS (`blocks/analog_line.py`)
- **Electrical model (not HiFi audio):** `line_voltage` (48 V on-hook / ~7 V off-hook), `loop_mA` (0 on-hook / 20–25 off-hook), `ring` (75 V AC, cadence 1 s on / 4 s off).
- **Audio DSP:** hybrid (passes the tone through), optional addition of noise/hum when a fault is present.
- **tap:** voltage + current gauge, hook/ring LED, line mini-scope.

### 5.3 DTMFDetector (`blocks/dtmf.py`)
- **Buffer:** ring buffer of 205 samples, fed by frames of 160.
- **8× Goertzel** (section 1) → 8 energies → `E_low_max`, `E_high_max`.
- **Validation (configurable thresholds):**
  ```
  ABS_THRESH   : E_low_max,E_high_max > -36 dBm0 (level)
  TWIST        : |20log10(E_low_max/E_high_max)| ≤ 8 dB
  DOMINANCE    : selected tone ≥ 6 dB above 2nd strongest in its group
  OOB_ENERGY   : energy outside the 8 bins < 50% of total (anti-speech)
  T_MIN_TONE   : tone ≥ 40 ms; T_MIN_PAUSE: pause ≥ 40 ms
  DEBOUNCE     : digit counted once per pause→tone edge
  ```
- **Output:** `dtmf_detected{digit, level, twist_dB, dur_ms}` or a Diagnostic (section 7).
- **tap:** 8-bin spectrum + digit readout.

### 5.4 CodecRTP (`blocks/codec_rtp.py`)
- **Codec:** PCM16 → **G.711 µ-law (PT=0)** or A-law (PT=8); frame of 160 samples → 160 bytes.
- **RTP packet:** `V=2,P=0,X=0,CC=0,M,PT, seq+1, timestamp+160, SSRC`. Marker bit on the first frame of a talkspurt.
- **RFC 4733 (DTMF out-of-band, PT=101):** `event(0–15), E-bit, volume(0–63), duration(16b)`; redundancy 3× for the first packets.
- **Jitter buffer:** target delay 60 ms, adaptive; computes `loss`, `jitter (RFC3550)`, `reorder`.
- **tap:** packet counter, loss%, jitter ms, payload type.

### 5.5 SIPSignaling (`blocks/sip_sim.py`) — control path (not audio)
- **States (UAC):** `IDLE → CALLING(INVITE) → PROCEEDING(100/180/183) → COMPLETED(200) → CONFIRMED(ACK) → INCALL → TERMINATED(BYE)`.
- **Timers:** T1=500 ms, Timer A (INVITE retransmission, doubles), Timer B (timeout = 64·T1 = 32 s).
- **Response codes:** real codes generated (section 7) — in normal mode `100,180,200`; with faults `408/486/503/...` + `Reason: Q.850;cause=...`.
- **tap:** ladder diagram (list of messages with codes).

### 5.6 Gateway/Provider (`blocks/gateway.py`) — sink
- **Model:** adds `e2e_delay` (sum of block + patch latencies), computes **MOS** (section 7), detects no audio / one-way.
- **Sink:** backend-sink **or speaker** (sounddevice out).
- **tap:** delay gauge, MOS, audio meter on the output.

---

## 6. Fault injection — catalog (`engine/faults.py`)

Each fault is a `FaultSpec.apply(frame)` (blocks) or `Impairment.apply(frame)` (patches), with parameters:

| Block/patch | Fault | Parameters | Effect |
|---|---|---|---|
| Dialer | `weak_tone` | gain_dB (−20) | level below threshold |
| Dialer | `wrong_freq` | offset_Hz | twist/rejection |
| AnalogLine | `no_loop_current` | — | no off-hook |
| AnalogLine | `line_noise` | snr_dB | SNR drops |
| AnalogLine | `hum_50hz` | level | hum |
| DTMF | `bad_threshold` | thr_dB | false rejections |
| CodecRTP | `packet_loss` | pct | sequence gaps |
| CodecRTP | `jitter` | ms | timestamp divergence |
| CodecRTP | `codec_mismatch` | pt | no audio |
| SIP | `force_response` | code (503/486/408) | call fails with code |
| SIP | `no_ack` | — | handshake incomplete |
| Gateway | `high_latency` | ms | E2E_DELAY_HIGH |
| patch * | `cut` | — | token halted |
| patch * | `impair` | delay/atten/loss | degradation |

---

## 7. Detection layer — real codes + correlator (`diag/`)

### 7.1 Detector time regime
- **instantaneous** (per frame): threshold on the current frame
- **windowed** (1 s): loss/jitter ratios in a sliding window
- **stateful** (machine): DTMF timing, SIP handshake

### 7.2 Code catalog (internal `code` → real `protocol_code`)

| code | layer | real code / standard | condition | sev |
|---|---|---|---|---|
| `FXS_NO_LOOP_CURRENT` | analog | loop signaling | loop<18 mA @ offhook | error |
| `LINE_LOW_SNR` | analog | — | SNR<20 dB | warn |
| `DTMF_TWIST_OOR` | DTMF | ITU-T Q.24 twist | \|twist\|>8 dB | warn |
| `DTMF_TOO_SHORT` | DTMF | Q.24 timing | tone<40 ms | error |
| `DTMF_REJECTED` | DTMF | — | level/dominance/OOB | warn |
| `DTMF_EVENT_LOST` | RTP | RFC 4733 | event ≠ in-band audio | error |
| `RTP_LOSS_SPIKE` | RTP | RFC 3550 | loss>1% / 1 s | error |
| `RTP_JITTER_HIGH` | RTP | RFC 3550 | jitter>30 ms | warn |
| `CODEC_MISMATCH` | RTP | PT mismatch | PT unsupported | error |
| `SIP_408` | SIP | **408 Request Timeout** | Timer B expiry | error |
| `SIP_486` | SIP | **486 Busy Here** | 486 response | info |
| `SIP_503` | SIP | **503 Service Unavailable** | 5xx response | error |
| `SIP_NO_ACK` | SIP | RFC 3261 | no ACK after 200 | error |
| `Q850_CAUSE` | SIP | **Q.850 cause=X** | from Reason header | warn |
| `E2E_DELAY_HIGH` | gateway | ITU G.114 | delay>150 ms | warn |
| `MOS_LOW` | gateway | ITU P.800 | MOS<3.5 | warn |
| `ONE_WAY_AUDIO` | e2e | — | SIP 200 OK + RTP_NO_AUDIO | error |
| `SIGNAL_CUT` | patch | — | patch.connected=false | info |

### 7.3 Correlator (`diag/correlator.py`)
- Chain index: `Dialer=0, Line=1, DTMF=2, CodecRTP=3, Gateway=5`; `SIP=4` treated as the **control plane** (parallel).
- Correlation window: **500 ms** from the first diagnostic of a given session.
- Algorithm:
  1. collect the session's diagnostics within the window, sev ≥ warn
  2. **root = smallest chain index, on a tie the earliest `t`**
  3. special rule: if `SIP=200 OK` and there is a media diagnostic (RTP/gateway) → `summary="media issue, signaling OK"`, root = media
  4. special rule: `SIP_5xx/4xx` from a provider fault → root = SIP, even though it is the "end" of the chain
  5. emit `RootCause{root_code, root_block, summary, consequences=rest}`
- Block health hysteresis: red after 1 error, return to green after 1 s clean.

### 7.4 MOS (simplified E-model)
```
R = 93.2 − Id(delay) − Ie(loss, codec)
Id ≈ 0.024*delay + 0.11*(delay−177.3)*H(delay−177.3)
Ie ≈ Ie0(codec) + B*ln(1+C*loss)
MOS = 1 + 0.035R + R(R−60)(100−R)*7e-6   # clamp 1..4.5
```

---

## 8. Event bus + WebSocket (`app/ws.py`)

- **Two WS connections:** `/ws/events` (JSON, envelope section 2.2) and `/ws/scope` (binary, section 2.8).
- **Rate limiting on the backend side:**
  - `flow`/`state`/`diag`/`sip`/`rtp` — event-driven (when they occur)
  - scope — 30 fps (decimation if RT_FACTOR≠1)
  - spectrum — 20 fps
- **Back-pressure:** if the client cannot keep up, scope/spectrum are dropped (the freshest frame wins); diag/sip/log events are **never** lost (queue).
- **Internal bus:** `asyncio.Queue` per channel; serialization in a single output task.

---

## 9. Audio I/O (`audio/io.py`, sounddevice)

```
InputStream(samplerate=mic_native, blocksize=...)  → resample to 8k → ring buffer IN
engine SOURCE_FRAME reads from ring buffer IN (when source=mic)
Gateway sink writes to ring buffer OUT → OutputStream(8k→native) → speaker
```
- Mic resampling (usually 44.1/48k) → 8k: `scipy.signal.resample_poly`.
- **Echo for the demo:** by default **half-duplex** (while a tone is playing, the mic source is muted) + an "echo demo" option to show feedback.
- Device selection: parameter `--in-device/--out-device`; fallback: no audio → pure-sim mode (source = generator).

---

## 10. Session, logging, replay (`diag/logger.py`)

- **Session** = one call, `session_id`. Start: `start_call`/off-hook. End: BYE/hangup.
- **JSONL log** (`recordings/<session>.jsonl`): each line = Envelope (diag/sip/rtp/rootcause/flow). Plus optionally audio `.wav`.
- **Replay:** reads JSONL, replays events by `t` through the same bus → the UI does not distinguish replay from live. Report export = filter by sev≥warn + RootCause.

---

## 11. Backend — module structure

```
backend/
  app/
    main.py          FastAPI, mounting frontend statics, /ws/events, /ws/scope, /control
    ws.py            bus → WS, rate limit, back-pressure
  engine/
    const.py         constants (section 1)
    clock.py         asyncio loop + RT_FACTOR
    block.py         Block base + passthrough/silence
    patch.py         Patch + Impairment
    graph.py         Graph.tick (section 4)
    faults.py        FaultSpec / Impairment.apply (section 6)
  blocks/
    dialer.py  analog_line.py  dtmf.py  codec_rtp.py  sip_sim.py  gateway.py
  dsp/
    tone_gen.py      DTMF synthesis + envelope
    goertzel.py      bank of 8 filters (numpy, vectorized)
    metrics.py       rms, snr, twist, jitter, mos
  audio/
    io.py            sounddevice in/out + resample + ring buffers
  diag/
    diagnostic.py    model + timing regimes
    detectors/       per-block detectors
    correlator.py    LEVEL 3
    logger.py        JSONL + replay
  schemas/
    events.py        pydantic: Envelope, Flow, State, Diagnostic, RootCause, Control
  run.py             bootstrap (graph + audio + ws)
```

---

## 12. Frontend — component tree (`frontend/src/`)

```
App
├─ WsProvider            (2 WS: events JSON + scope binary; reducer stanu)
├─ TopBar                title, RT-factor slider, Record/Replay
├─ Patchbay              block graph (SVG) — CONSUMES: flow, state
│   ├─ BlockNode×6       health (green/amber/red), click→FaultMenu
│   ├─ PatchEdge×5       token (framer-motion) sterowany flow.active/level/kind
│   └─ RootCauseBanner   CONSUMES: rootcause
├─ PanelGrid
│   ├─ FxsPanel          gauge V/mA, LED hook/ring     (state, scope[line])
│   ├─ DtmfKeypad        klawiatura + odczyt cyfry      (press_digit→control; dtmf events)
│   ├─ Scope             oscyloskop uPlot               (scope binary)
│   ├─ Spectrum          8 bins uPlot                   (scope binary[spectrum])
│   ├─ SipLadder         drabinka INVITE/180/200…       (sip)
│   └─ RtpStats          loss/jitter/MOS gauge          (rtp)
├─ EventLog              tabela filtrowalna blok·sev·code (diag, sip)  [export][replay]
└─ ControlBar            Start/Hangup/InjectFault/CutSignal             (→control)
```
- **State**: a single reducer; each Envelope updates a slice (`flow`, `nodes`, `diags`, `sip`, `rtp`, `rootcause`).
- **Scope/Spectrum**: a separate binary WS → directly to uPlot (bypasses the reducer, so as not to re-render the whole thing 30×/s).
- **Token animation**: on `flow{active:true}` the PatchEdge fires motion from src to dst; `kind` changes the icon (wave/tone/packet).

---

## 13. Scope of Phase 0 (first build — exactly)

Goal: **a live flow through 3 blocks, without SIP/RTP/audio.**
- Backend: `const`, `clock` (RT_FACTOR), `block`, `patch`, `graph.tick`, bus + `/ws/events`, `/control`.
- Blocks: **Dialer (generator), AnalogLine, DTMF (real Goertzel)** + dsp `tone_gen`, `goertzel`.
- Detection: minimal set (`DTMF_REJECTED`, `SIGNAL_CUT`) + JSONL logger.
- Frontend: `WsProvider`, `Patchbay` with 3 blocks + flow token, `Scope`, `Spectrum`, `DtmfKeypad`, `EventLog`, `ControlBar` (Start, press digit, Cut patch).
- Phase 0 demo: click a digit → token flows Dialer→Line→DTMF → spectrum lights up 2 bins → digit readout; click "Cut" on a patch → token stops, edge turns red, `SIGNAL_CUT` in the log.

Phase 1 adds audio (sounddevice) and the FXS panel; Phase 2 — full faults + correlator; Phase 3 — CodecRTP+SIP (sim.→Asterisk); Phase 4 — replay/export/kiosk.

---

## 13a. Testing strategy — block isolation

Assumption: **a block is a unit testable in full isolation**. It communicates only through `Frame` (in/out) and `Diagnostic[]` (bus), so it can be run without the graph, WS, UI and audio. This also changes the build order: **first the contract + test harness, then blocks independently** (can be done in parallel), each one "ready" = passes its set of cases.

### Test pyramid
```
e2e (UI + audio, manual/Playwright)         ← few, demo
integration (graph + scripted scenario)     ← correlator, cascades
fault-cases (block + fault → diagnostics)   ← many, parametrized
unit (DSP/block, golden vectors)            ← most, fast
```

### Isolated block harness (`tests/harness.py`)
```python
def run_block(block, frames, fault=None, patch_cut=False):
    diags = []
    out = []
    for f in frames:
        if fault: f = fault.apply(f)
        g = block.process(f) if block.enabled else f
        diags += block.detector.check(g)
        out.append(g)
    return out, diags         # pure assertions, zero I/O
```
Plus a **standalone CLI per block** for manual tests and demos:
`python -m blocks.dtmf --in tone_7.wav` → prints the detected digit + diagnostics.

### Fixtures / golden vectors (`tests/signals.py`)
Signal generators: `dual_tone(digit, dur_ms, level, twist_dB)`, `pure_tone(f)`, `white_noise(snr)`, `silence()`, `speech_like()`. Deterministic (fixed seed) → repeatable.

### Cases per block (each row = 1 parametrized test)

| Block | Input | Expected output / diagnostic |
|---|---|---|
| **DTMF** | `dual_tone("7",100ms)` | `digit=="7"`, no errors |
| DTMF | `dual_tone("7",20ms)` | `DTMF_TOO_SHORT` |
| DTMF | `dual_tone("7",twist=12dB)` | `DTMF_TWIST_OOR` |
| DTMF | `dual_tone("7",level=-50dB)` | `DTMF_REJECTED` (level) |
| DTMF | `speech_like()` | no digit (anti-speech), 0 false-positive |
| DTMF | two tones at once | `DTMF_REJECTED` (dominance) |
| **AnalogLine** | offhook | `loop_mA ∈ [20,25]`, `voltage≈7V` |
| AnalogLine | fault `no_loop_current` | `FXS_NO_LOOP_CURRENT` |
| AnalogLine | fault `line_noise(snr=10)` | `LINE_LOW_SNR` |
| **CodecRTP** | 5 PCM frames | `seq` +1/packet, `ts` +160, PT=0 |
| CodecRTP | fault `packet_loss(5%)` | seq gaps → `RTP_LOSS_SPIKE` |
| CodecRTP | fault `codec_mismatch` | `CODEC_MISMATCH`, no audio |
| CodecRTP | DTMF in-band vs RFC4733 divergence | `DTMF_EVENT_LOST` |
| **SIP** | normal flow | `[100,180,200,ACK]`, state `INCALL` |
| SIP | fault `force_response(503)` | `SIP_503`, no `INCALL` |
| SIP | fault `no_ack` | `SIP_NO_ACK` |
| SIP | no response | after 32 s `SIP_408` (test with mock clock) |
| **Gateway** | fault `high_latency(200ms)` | `E2E_DELAY_HIGH` |
| Gateway | loss 3% on input | `MOS_LOW` (MOS<3.5) |
| **Patch** | `cut` | `flow.active==False`, `SIGNAL_CUT` |

Example (pytest):
```python
@pytest.mark.parametrize("digit", list("0123456789ABCD*#"))
def test_dtmf_roundtrip(digit):
    frames = framize(dual_tone(digit, 100))
    out, diags = run_block(DTMFDetector(), frames)
    assert detected_digit(diags) == digit
    assert not any(d.severity=="error" for d in diags)
```

### Correlator test — no signal (`tests/test_correlator.py`)
The correlator only consumes `Diagnostic[]`, so it is tested with **synthetic sequences**, without DSP:
```python
def test_weak_tone_cascade():
    rc = correlate([D("SRC_WEAK_LEVEL","Dialer",0.00),
                    D("DTMF_REJECTED","DTMF",0.13)])
    assert rc.root_code == "SRC_WEAK_LEVEL"
    assert "DTMF_REJECTED" in rc.consequences

def test_one_way_audio():
    rc = correlate([D("SIP_200OK","SIP",0.4,sev="info"),
                    D("RTP_NO_AUDIO","CodecRTP",0.45)])
    assert rc.root_block == "CodecRTP"
    assert rc.summary == "media issue, signaling OK"
```

### Integration test — scripted scenario (`tests/test_chain.py`)
Builds the graph, replays a scenario (script of events + faults), asserts the `RootCause` and the `flow` sequence. This verifies the **cascade across layers** without the UI.

### Contract test (shared by all blocks)
A single parametrized test checks that **every** block satisfies the contract: `process` returns a `Frame` of the same length, `tap()` returns a correct shape, a block with `enabled=False` passes through/silences according to its type. Thanks to this, a new block "plugs in" only once it passes the contract.

### CI
`pytest` (unit+fault+integration+correlator) on every push; golden vectors kept in the repo; **recorded JSONL sessions with replay serve as regression fixtures** (yesterday's failure → today's test) — CI pipelines and integration tests over the whole chain.

### Impact on build order
```
1. Contract (Block/Patch/Frame) + harness + signals      ← test foundation
2. DTMF (easiest to give golden vectors)                 ← independently
3. AnalogLine                                            ← independently
4. Dialer → links with 2-3 into a mini-graph (1st integration)
5. CodecRTP, SIP, Gateway                                ← independently, each with its own suite
6. Correlator (tested synthetically from the start)
```
Each block delivered with: implementation + detector + fixtures + tests + CLI. "Done" = green suite, independent of the rest.

## 14. Open points (confirm or leave the defaults from section 0)

- A-law vs µ-law as the default codec (default: µ-law/PT0).
- Twist split forward/reverse (default: symmetric 8 dB; easy to split later).
- Whether to wire up the mic immediately in Phase 0 (default: no — generator; mic in Phase 1).
