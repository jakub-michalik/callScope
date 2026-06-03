# Changelog

All notable changes to CallScope. Hosted docs: <https://jakub-michalik.github.io/callScope/> ·
Releases: <https://github.com/jakub-michalik/callScope/releases>

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
