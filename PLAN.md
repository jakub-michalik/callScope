# CallScope — Project Plan

**Virtual test bench for the FXS → VoIP chain, with a dedicated DSP path per block, fault injection, signal disconnection, and problem detection and logging.**

Goal: live-demonstrate *end-to-end* analysis of the signal chain (analog → Linux box → VoIP) and methodical fault analysis across multiple system levels.

Target: laptop/PC for the demo (microphone + speakers as real audio I/O), the same code on **Raspberry Pi 4** (USB audio).

---

## 1. Core concept — a block graph with an isolated DSP path

We model the signal chain as a **block graph** (like a flowgraph in GNU Radio, but with a dashboard like sngrep). Each block has its **own, dedicated DSP path** — an independent processing unit with its own input, output, preview, fault injector, disconnect switch, and problem detector.

```
  🎤 mic                                                              🔊 speaker
   │                                                                      ▲
   ▼                                                                      │
┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐  ┌──────────┐
│ DIALER   │──▶│ ANALOG   │──▶│ DTMF     │──▶│ CODEC /  │──▶│ SIP      │─▶│ GATEWAY  │
│ (source) │ p │ LINE/FXS │ p │ DETECTOR │ p │ RTP      │ p │ SIGNALING│p │ /PROVIDER│
└──────────┘   └──────────┘   └──────────┘   └──────────┘   └──────────┘  └──────────┘
  DSP path       DSP path       DSP path       DSP path       ctrl path      DSP path
     │ tap          │ tap          │ tap          │ tap          │ tap          │ tap
     ▼              ▼              ▼              ▼              ▼              ▼
  ═══════════════════════ EVENT BUS (signal + diagnostics + log) ════════════════════
                                       │
                                       ▼
                              DASHBOARD (React)
```

`p` = **patch point** (the connection between blocks): it can be **cut (signal disconnected)** or **disturbed** (delay, attenuation, noise, packet loss).

Why this way: isolating the paths means a fault can be **localized to a specific block or link** — analyzing complex fault patterns across multiple system stages.

---

## 2. Anatomy of a block (the contract of every block)

Every block implements the same interface — hence the uniformity of UI and logic:

```
Block:
  name            # e.g. "DTMF_DETECTOR"
  inputs[]        # input ports (audio samples / packets / events)
  outputs[]       # output ports
  enabled: bool   # DISCONNECT — bypass/cut, the block stops passing the signal
  process(frame)  # DEDICATED DSP PATH — the actual processing of this block
  fault: FaultSpec# FAULT INJECTION — configurable disturbances
  detector        # DETECTION — checks input/output, emits Diagnostic[]
  tap()           # PREVIEW — what the dashboard draws (waveform / spectrum / metrics)
  log             # structured log of the block's events (JSONL)
```

Each block is therefore a self-contained "path": **input → (optional fault) → DSP → detector → tap/log → output**, and the `enabled` switch lets you unplug it at any moment.

**The link (Patch) between blocks** is also a stateful object:
```
Patch:  connected: bool   # cutting the signal
        impairment: {delay_ms, attenuation_dB, noise, loss_pct, reorder}
```

---

## 3. Definition of the chain's blocks

| Block | DSP path (what it does) | Injected faults | Disconnect | What the detector detects |
|---|---|---|---|---|
| **Dialer** (source) | DTMF synthesis (sums of sines), generation of off-hook/digit seq; source = UI keypad **or microphone** | weak tone, wrong frequency, too-short tone, missing digit, frequency jitter | no signal (silence) | signal level, sequence completeness |
| **Analog Line / FXS** | line simulation: voltage, loop current, 2↔4-wire hybrid, on/off-hook | line noise, attenuation, no loop current, reversed polarity, 50 Hz hum | open line (disconnection) | no loop current, voltage out of range, line SNR |
| **DTMF Detector** | **Goertzel bank** (8 frequencies), twist / level / timing / pause validation | detuned thresholds, too-narrow window | detector bypass | digit detected/rejected, **twist out-of-spec**, too-short tone, tone collision |
| **Codec / RTP** | PCM → G.711 (µ/A-law) → RTP packetization, jitter buffer, RFC 4733 telephone-event | packet loss, jitter, reorder, codec mismatch, wrong frame size | no RTP transmission | loss %, jitter ms, out-of-order, no audio |
| **SIP Signaling** (control path) | callflow state machine: INVITE/100/180/200/ACK/BYE | timeout, 4xx/5xx response, no ACK, retransmissions | no signaling | incomplete handshake, timeout, unexpected state |
| **Gateway / Provider** (sink) | RTP egress to backend / Twilio; sink = backend-sink **or speaker** | high latency, NAT/asymmetric path | no sink | end-to-end delay, estimated **MOS**, no audio at the end |

The microphone and speaker are the physical I/O: **mic → Dialer/source**, **Gateway/sink → speaker**. This lets you play a tone on the PC and hear it / see it decoded.

---

## 4. Detection and logging subsystem (the core of the project)

This is the heart of the project. Every block and every link continuously reports diagnostics to a shared bus.

**Diagnostic event model:**
```
Diagnostic:
  ts            # timestamp (monotonic)
  block / patch # where it was detected
  severity      # info | warn | error
  code          # e.g. "DTMF_TWIST_OOR", "RTP_LOSS_SPIKE", "FXS_NO_LOOP_CURRENT"
  message       # human-readable description
  measured      # measured values {twist_dB: 9.2, limit: 8}
  session_id    # correlation with the current call
```

**Central Diagnostics Logger:**
- collects diagnostics from all blocks/links, correlates per session (call)
- writes a **structured JSONL log** (each line = one event) → replayable and exportable like a report/pcap
- feeds: (a) **per-block health indicators** on the dashboard (green/yellow/red), (b) a **scrolling event log** with filtering by block/severity/code, (c) **highlighting of the fault location** in the graph
- **Replay**: a recorded session (JSONL + audio frames) can be replayed and analyzed after the fact — this realizes "Log- und Protokollanalysen" from the offer

**Sample detection catalog (threshold → severity):**

| Code | Condition | Severity |
|---|---|---|
| `FXS_NO_LOOP_CURRENT` | loop current < 18 mA at off-hook | error |
| `LINE_LOW_SNR` | line SNR < 20 dB | warn |
| `DTMF_TWIST_OOR` | \|twist\| > 8 dB (US) / 6 dB (EU) | warn |
| `DTMF_TOO_SHORT` | tone duration < 40 ms | error |
| `DTMF_REJECTED` | level < threshold / collision / pause too short | warn |
| `RTP_LOSS_SPIKE` | packet loss > 1% in a 1 s window | error |
| `RTP_JITTER_HIGH` | jitter > 30 ms | warn |
| `SIP_TIMEOUT` | no response within 32 s (Timer B) | error |
| `E2E_DELAY_HIGH` | end-to-end delay > 150 ms | warn |
| `SIGNAL_CUT` | patch is cut while there is an active signal upstream | info |

Detection is **local to the block** (each knows its own norms), but logging and correlation are **central** — so the cascade is visible ("weak tone → rejected DTMF → incomplete number → SIP does not start").

---

## 5. Architecture and stack

**Backend (PC / Pi4):**
- Python 3.11, **asyncio** as the processing clock
- **numpy** — DTMF synthesis + Goertzel bank + signal metrics
- **sounddevice** (PortAudio) — real mic/speaker audio, the same code on PC and Pi4 (USB audio)
- **FastAPI + uvicorn + WebSocket** — event transport to the UI
- **pydantic** — event schemas (signal, diagnostics, log)
- block-graph engine + Diagnostics Logger (JSONL)

**Frontend (browser on PC):**
- **React + Vite + TypeScript + TailwindCSS + shadcn/ui** — a "beautiful app" at low cost
- **uPlot** — oscilloscope and spectrum (fast, lightweight, also works in kiosk mode on the Pi)
- **framer-motion** — signal flow animations in the graph
- custom **SVG** — SIP ladder diagram + "patchbay" (block graph with switches)

**Performance on Pi4:** DSP is computed on the backend; only ~60 fps frames for the oscilloscope (binary Float32), magnitudes of the 8 DTMF bins, and JSON events go to the UI. Rendering stays cheap.

---

## 6. Visualization — dashboard layout

```
┌─ CallScope ──────────────────────────────────────────────────────────────┐
│ PATCHBAY:  Dialer ●─╫─▶ FXS ●──▶ DTMF ●──▶ RTP ●─╫─▶ SIP ●──▶ Gateway ●   │  ← graph;
│            (each block: green/yellow/red · click = fault/cut)              │    ╫ = cut
├───────────────┬───────────────┬──────────────────────────────────────────┤
│ FXS PANEL     │ DTMF KEYPAD   │ SIP LADDER (animated)                      │
│ Loop 23 mA ▕▊ │ [1][2][3]     │ DETECTIONS (live):                        │
│ Line 48 V  ▕▊ │ [4][5][6]     │  ⚠ DTMF_TWIST_OOR  twist 9.2dB            │
│ ◉ off-hook    │ [7]✦[8][9]    │  ✖ RTP_LOSS_SPIKE  1.8%                    │
├───────────────┴───────────────┼──────────────────────────────────────────┤
│ OSCILLOSCOPE  ╱╲╱╲╱╲           │ EVENT LOG / TIMELINE (filter: block·sev·code)│
│ SPECTRUM  ▁▁█▁▁▁█▁ (2 tones)   │ [export JSONL] [replay ▷]                  │
└───────────────────────────────┴──────────────────────────────────────────┘
 [ Start call │ Hang up │ Inject fault ▾ │ Cut signal ▾ │ Record ⏺ │ Replay ▷ ]
```

Each block has its own tile with a preview (tap) and a health indicator; clicking a block/link opens its fault-config and disconnect switch.

---

## 7. Phased plan (each phase = a working artifact)

- **Phase 0 — Graph skeleton (½–1 day):** `Block`/`Patch` contract, event bus, Diagnostics Logger (JSONL), WS, React shell with a patchbay on synthetic events. Proof that the bus and logging work.
- **Phase 1 — Real audio + DSP:** Dialer/Line/DTMF blocks with `sounddevice`; DTMF synthesis → speaker, microphone → Goertzel; oscilloscope + spectrum + keypad + FXS panel. Live "key/tone → sound → plot → detection" loop.
- **Phase 2 — Fault injection + disconnection + detection:** full fault catalog per block and per link, cut switches, complete set of detectors and cascading of diagnostics. This is the "meat" of the project.
- **Phase 3 — VoIP (sim):** Codec/RTP block + SIP ladder (simulation) over the same event bus.
- **Phase 4 — Versatility + live SIP:** config-driven scenarios, blocks declaring their own conditions/faults, backend-sent topology; first real calls against Asterisk.
- **Phase 5 — Native SIP/RTP stack:** CallScope becomes the SIP user agent itself (own UDP sockets, digest auth, RTP/G.711) — real calls with no external client.
- **Phase 6 — Polish (future):** session record/replay, diagnostic report export, "lab instrument" theme, kiosk mode on Pi4.

---

## 8. Directory structure (within the Randstad application)

```
callScope/
  PLAN.md                      ← this document
  backend/
    app/        fastapi, websocket, serialization
    engine/     graph.py, block.py, patch.py, clock.py
    blocks/     dialer.py, analog_line.py, dtmf.py (Goertzel), codec_rtp.py, sip_sim.py, gateway.py
    dsp/        tone_gen.py, goertzel.py, metrics.py
    audio/      io.py (sounddevice mic/speaker)
    diag/       diagnostic.py, logger.py (JSONL), detectors/
    schemas/    pydantic: signal, diagnostic, log, control
  frontend/
    src/        patchbay/, panels/ (fxs, dtmf, scope, spectrum, sip, rtp), log/, ws/
  recordings/   JSONL sessions + audio for replay
  docker-compose.yml
```

---

## 9. Mapping to the offer's requirements

| Telecom capability | Realization in CallScope |
|---|---|
| Analog telephony: FXS, DTMF, signaling, timing | Analog Line + DTMF blocks; twist/timing/loop-current detections |
| VoIP: SIP, RTP, call flows, audio debug | Codec/RTP block + SIP ladder; jitter/loss/MOS metrics |
| Linux low-level: log analysis, tcpdump/Wireshark | Diagnostics Logger (JSONL) + replay; real RTP visible in Wireshark |
| Meticulous analysis of analog & digital signal paths | dedicated DSP path per block + preview of each path |
| Fault analysis across multiple system stages | fault injection + cut per block/link + cascading diagnostics |
| Test/measurement environment, HIL | the whole thing = a virtual test bench; Pi4 + audio/ATA = physical HIL |
| Audio signal processing | real Goertzel on audio from the microphone/speaker |
| Test automation / CI | reproducible fault scenarios (replay) → regression tests in CI |
| Safety-critical / high-availability (emergency calls) | detection and logging of faults in the emergency chain |

---

## 10. Open decisions (to confirm before Phase 1)

- Telephony in Phase 3: simulation vs real Asterisk vs hybrid (default: **hybrid** — abstraction from the start, swap-in later).
- Display: browser on PC (default for the demo) vs kiosk on the Pi4 screen.
- Name: **CallScope** (working title) — pending approval.
