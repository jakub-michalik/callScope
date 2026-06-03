Overview
========

CallScope models a phone call as a **graph of blocks** connected by **patches**. Each
block owns a small, self-contained piece of the signal chain and reports diagnostics to a
shared **event bus**; the browser dashboard is a thin view over that bus.

Two planes
----------

The chain runs on two planes:

* **Media plane** — ``Dialer → AnalogLine (FXS) → DTMF → CodecRTP → Gateway``: the actual
  audio/RTP path. Each block has a dedicated DSP path and a set of fault conditions.
* **Control plane** — the ``SIP`` block: it gates the media path (RTP only flows once
  signaling reaches *in-call*) and renders the call-flow ladder.

Core ideas
----------

* **Block contract** (:mod:`engine.block`) — every block exposes ``dsp``/``detect``/
  ``tap``/``conditions``/``fault``; adding a block or a fault touches a single place.
* **Event bus** (:mod:`engine.bus`) — blocks emit timestamped envelopes; the UI is
  agnostic to whether events are simulated or live.
* **Goertzel DTMF** (:mod:`dsp.goertzel`, :mod:`blocks.dtmf`) — gain-independent,
  SNR-based dual-tone detection with a lock/gap validation FSM.
* **Root-cause correlator** (:mod:`diag.correlator`) — localizes the most-upstream cause
  across stages and reports the downstream consequences.
* **Native SIP/RTP** (:mod:`voip.sip_native`, :mod:`voip.digest`, :mod:`voip.rtp`) — a
  pure-Python SIP user agent: real INVITE/ACK/BYE, digest auth (RFC 2617), SDP negotiation
  and G.711 RTP streaming against Asterisk, with no external client.

SIP backends
------------

The SIP control plane is pluggable; all three backends expose the same interface, so the
dashboard, fault injection and correlator work identically on any of them:

* **sim** — the ladder is produced in-process (no network); the infrastructure-free default.
* **native** (:mod:`voip.sip_native`) — CallScope *is* the SIP user agent; real SIP/RTP on the
  wire against Asterisk. **Recommended.**
* **baresip** (:mod:`sip_adapter`) — drives a real off-the-shelf **baresip** client over its
  ``ctrl_tcp`` interface, as an interop cross-check. It runs in Docker (a ``baresip`` service in
  ``asterisk/docker-compose.yml``, behind the ``live`` profile); the adapter auto-connects to the
  container's ``ctrl_tcp`` on ``127.0.0.1:4444``, or falls back to spawning a host ``baresip``.

Both live backends talk to a bundled **Asterisk** container — see ``ASTERISK.md`` in the repo.

Phases
------

The project was built in phases:

* **Phase 0** — graph skeleton, event bus, WebSocket, patchbay dashboard.
* **Phase 1** — real audio (``sounddevice``) and DSP: dialer, FXS line, DTMF/Goertzel.
* **Phase 2** — fault injection + root-cause correlator.
* **Phase 3** — VoIP leg: Codec/RTP block + SIP ladder (simulated).
* **Phase 4** — versatility: config-driven scenarios + first live calls to Asterisk.
* **Phase 5** — native SIP/RTP stack (CallScope is the SIP user agent itself).
* **Phase 6** — polish: session replay, report export, kiosk mode (future).
