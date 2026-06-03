Investigating problems
======================

CallScope is built for *finding the fault*, not just placing a call. This page is the
workflow for diagnosing a broken call — from the dashboard, down to the packets on the wire.

The workflow
------------

Work from the cheapest signal to the most detailed:

#. **Read the dashboard** — the root-cause banner and the per-block logs already localize most
   faults.
#. **Read the SIP ladder** — every message with its detail (codec, RTP endpoint, digest realm,
   reason + Q.850 hint).
#. **Capture the wire** — ``tcpdump`` / Wireshark to confirm what actually left the machine.
#. **Replay the app-level trace** — ``tools/sip_trace.py`` taps the native stack directly.

1 — Reading the dashboard
-------------------------

* **Root-cause banner** — when a call breaks, the correlator pins the *most-upstream* cause to a
  block and shows the downstream consequences. ``SIP_503`` → ``congestion / no circuit (Q.850 #34)``,
  ``SIP_484`` → ``address incomplete``, etc.
* **Per-block logs** — one timestamped panel per block (Dialer, AnalogLine, DTMF, SIP, CodecRTP,
  Gateway). Each event is its own line with a wall-clock timestamp — no collapsing, so two
  detections 20 ms apart are visible as two lines.
* **SIP ladder** — the live ``INVITE → 401 → digest → 200 OK → ACK`` exchange; each line carries
  the negotiated codec, the RTP endpoint and the server banner.
* **RTP panel** — MOS, loss, jitter, and the **RX / TX** packet counters. ``TX`` rising while
  ``RX`` stays at 0 means **one-way audio** (your media isn't coming back).

2 — Capturing the wire
----------------------

The live path is real SIP/RTP on loopback — SIP on ``5062``, RTP ``10000–20000`` (Asterisk) /
``40000`` (CallScope):

.. code-block:: bash

   # raw SIP in the console while you place a call
   sudo tcpdump -i lo -n -A 'udp port 5062'

   # capture SIP + RTP to a pcap, open in Wireshark
   sudo tcpdump -i lo -n -s0 'udp and (port 5062 or portrange 10000-20000 or portrange 40000-40010)' -w /tmp/callscope.pcap
   wireshark /tmp/callscope.pcap

In Wireshark: **Telephony ▸ VoIP Calls ▸ Flow Sequence** is the same ladder as the dashboard,
straight off the wire; **RTP ▸ Play Streams** plays back the captured audio.

The app-level trace dumps every SIP message from the *same* native stack the dashboard uses:

.. code-block:: bash

   python tools/sip_trace.py 600 127.0.0.1 5062

Common problems
---------------

.. list-table::
   :header-rows: 1
   :widths: 34 66

   * - Symptom
     - Cause & fix
   * - Header shows ``sim``, or you get ``SIP_401``
     - You're dialing the **wrong Asterisk** — a host service on ``5060`` instead of the container
       on ``5062``. The ``Server:`` header in the 401 gives away a different Asterisk version. Set
       the **Asterisk** port field in the top bar to ``5062`` (or relaunch the container; a host
       Asterisk on 5060 silently steals the traffic).
   * - ``SIP_401`` persists with the right port
     - Wrong credentials. Default is ``callscope`` / ``callscope`` — check ``CALLSCOPE_SIP_USER`` /
       ``CALLSCOPE_SIP_PASS``.
   * - No echo / silence on **600**
     - Use **headphones** (mic↔speaker feedback otherwise); confirm the header shows ``🎤 in``
       (mic detected) — if not, ``sounddevice`` has no input device and TX sends silence; and make
       sure the app was **restarted** to load the audio path.
   * - ``SIP_503`` / ``SIP_486``
     - The dialplan is rejecting on purpose (``503`` congestion, ``486`` busy). Dial ``600`` / ``112``
       for a successful call.
   * - ``SIP_484`` (address incomplete)
     - The number isn't a complete extension — you dialed a prefix. Dial a full number
       (``112/600/503/486/500/700``).
   * - ``baresip`` (``live``) falls back to ``native``/``sim``
     - baresip's ``ctrl_tcp`` isn't reachable. Start the container —
       ``docker compose --profile live up -d`` — and check ``nc -z 127.0.0.1 4444``.
   * - RTP ``RX = 0`` during a call
     - One-way audio — packets leave but none return (firewall, or Asterisk can't reach your RTP
       port). On loopback this should not happen; check the RTP range is open.
   * - DTMF detected twice per key press
     - A momentary tone dropout unlocks and re-locks the detector. Raise the **hangover** /
       **gap** sliders in the DTMF panel.

Deeper debugging
----------------

* **Native SIP I/O trace** — set ``SIP_DEBUG=1`` before launching to print every SIP message
  received by the native stack to the console.
* **Asterisk side** — ``docker exec callscope-asterisk asterisk -rx "pjsip set logger on"`` and
  watch ``docker logs -f callscope-asterisk`` for the server's view of the exchange;
  ``pjsip show endpoints`` / ``pjsip show contacts`` to confirm registration and auth.
