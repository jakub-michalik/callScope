Screenshots
===========

Dashboard
---------

The full signal-chain dashboard: a two-plane **patchbay** (media row + SIP control plane),
per-block DSP panels (FXS line, DTMF keypad), the live **SIP call-flow ladder**, **RTP**
media metrics, an oscilloscope, the DTMF **Goertzel spectrum**, and per-block event logs —
all driven live over a WebSocket. The header shows it connected in **native** mode to
Asterisk.

.. image:: screenshots/dashboard.png
   :alt: CallScope dashboard
   :width: 100%

Live call to Asterisk
---------------------

A live call to Asterisk extension **600 (echo)**: the SIP ladder fills with the real
``INVITE → 401 → digest → 200 OK → ACK`` exchange, the patchbay token flows along the chain,
and the per-block logs stream timestamped events.

.. image:: screenshots/call-in-progress.png
   :alt: CallScope during a live call to Asterisk
   :width: 100%
