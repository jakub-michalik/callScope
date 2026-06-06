# Changelog

All notable changes to CallScope. Hosted docs: <https://jakub-michalik.github.io/callScope/> ·
Releases: <https://github.com/jakub-michalik/callScope/releases>

## [0.7.9] — 2026-06-06
### Changed
- **Dashboard top row** now holds three windows — patchbay, the oscilloscope/spectrum stack,
  and the **DTMF detection-parameters** panel (moved up from a full-width row below).
- Refreshed docs screenshots to the new layout.
### Added
- `CALLSCOPE_SIP_LOCAL_PORT` / `CALLSCOPE_RTP_LOCAL_PORT` env vars to override the native
  backend's local SIP/RTP ports, so a second instance (e.g. screenshot capture) can run
  alongside a running one.

## [0.7.8] — 2026-06-06
### Fixed
- **Native SIP 4xx retransmission flood.** A failure response (404/484/486/503) was ACKed
  without the response's To-tag, so the server transaction never matched the ACK and Asterisk
  kept retransmitting the failure — a flood of 404/484 that didn't clear on hang-up. ACK
  non-2xx responses with their own To-tag (RFC 3261 §17.1.1.3).
### Changed
- **Dashboard layout**: the oscilloscope and spectrum now sit beside a narrower patchbay
  (instead of a separate row below), saving vertical space.
### Added
- **Docs landing page**: repository link + status badges (CI / Docs / release / license / python).

## [0.7.7] — 2026-06-04
### Fixed
- **Docs sidebar logo not rendering.** The `max-width`-only rule in `custom.css` collapsed the
  logo to zero because the SVG had no intrinsic dimensions. Give the SVG explicit `width`/
  `height` and set an explicit `width` in CSS. Verified by rendering the built page.

## [0.7.6] — 2026-06-04
### Changed
- **Refined logo** — cleaner rounded badge with a smooth single-cycle oscilloscope trace
  (replacing the sharper zig-zag) and a softer green live dot.
- **Smaller docs sidebar logo** via `_static/custom.css` (the default furo size was too large).
### Added
- `tools/sip_ladder.sh` — watch the SIP call-flow in the console (sngrep arrow ladder, or a
  tcpdump text fallback). Captures on `lo` by default (CallScope's SIP is loopback; `-d any`
  yields LINUX_SLL2 which sngrep can't dissect).

## [0.7.5] — 2026-06-04
### Added
- **Project logo** (`docs/_static/logo.svg`) — an oscilloscope tone-burst mark; shown next to
  the title in the README and as the docs sidebar logo + favicon.

## [0.7.4] — 2026-06-04
### Fixed
- **Live baresip backend stuck at INVITE.** The `ctrl_tcp` socket kept the 2 s connect
  timeout, so the reader thread's `recv()` raised `socket.timeout` after 2 s of silence and
  died before the call event arrived — the ladder froze at INVITE. Connect with a timeout,
  then block in the read loop.
- **Hang-up is now honoured in any non-idle state** (incl. `CALLING`); it was a no-op while a
  call was still ringing out, leaving a live mic call running.
- **Periodic re-REGISTER no longer pollutes the call-flow ladder** and no longer rewrites the
  call state — a `regint` refresh landing mid-call could knock an active call to `IDLE`.
### Added
- **Opt-in host audio for the baresip container** (`docker-compose.audio.yml`): routes
  baresip's ALSA `default` through the ALSA→PulseAudio plugin to the host's PipeWire/Pulse
  over loopback TCP, so dialling echo (600) on the `live` backend is audible.
- Reusable `tools/capture_screenshots.py` to refresh the docs screenshots from the live UI.
### Changed
- **Cleaner container-log overlay**: silence baresip's `audio=N/N (bit/s)` status spam at the
  source (`statmode_default off`), strip ANSI colour codes, and drop Asterisk's healthcheck
  `Remote UNIX connection` noise — so REGISTER/INVITE/Answer/Echo are actually visible.
- Refreshed dashboard screenshots (native mode, real call to Asterisk echo).

## [0.7.3] — 2026-06-04
### Fixed
- **baresip live media**: replaced the 48 kHz-only `ausine` audio source with an `aufile`
  reading an 8 kHz WAV, so PCMU/8000 calls actually establish (were failing with
  "session closed: Operation not supported").
### Added
- **Timestamps** on the dashboard's container-log overlay (`docker logs --timestamps`).

## [0.7.2] — 2026-06-03
### Added
- **Investigating problems** guide in the docs — the fault-analysis workflow (dashboard → SIP
  ladder → `tcpdump`/Wireshark → app-level trace) plus a common-problems table.
- This changelog.

## [0.7.1] — 2026-06-03
### Added
- **Dockerized baresip backend** — the `live` backend runs a real `baresip` client from Docker
  (a `baresip` service in `asterisk/docker-compose.yml`, behind the `live` profile). The adapter
  **auto-connects** to the container's `ctrl_tcp` on `127.0.0.1:4444` (no host install), falling
  back to a host `baresip` if present.
### Changed
- Patchbay relabels the gateway block as **Asterisk** in live (native/baresip) mode.
- README **Requirements** + **Quick start** with both-container run / verify / stop instructions.

## [0.7] — 2026-06-03
### Changed
- Documentation grouped into **Architecture** and **Reference** sections with nested contents.

## [0.6.1 / 0.6.2] — 2026-06-03
### Added
- **Hosted documentation** (Sphinx + furo): autodoc API reference, **architecture diagrams
  (Mermaid)**, screenshots, and a **per-release version switcher**.
- **GitHub Actions**: CI (pytest) and docs build/deploy to GitHub Pages, with status badges.

## [0.2] — 2026-06-01
### Added
- First tagged release. **Native SIP/RTP stack** (UAC + UAS): real INVITE/ACK/BYE, digest auth
  (RFC 2617), SDP negotiation, G.711 RTP against Asterisk — no external softphone.
- Three swappable SIP backends (sim / native / baresip), live-switchable in the UI.
- Real audio (mic ↔ RTP ↔ speaker), Goertzel DTMF, root-cause correlator, fault injection.
- 83 tests.
