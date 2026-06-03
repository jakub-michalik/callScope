Quick start
===========

Requirements
------------

.. list-table::
   :header-rows: 1
   :widths: 22 28 50

   * - Needed for
     - What
     - Notes
   * - **Always**
     - **Python 3.12+** & ``pip``
     - runs the dashboard + the SIP/RTP stack
   * - **Live Asterisk**
     - **Docker** + Compose
     - only for the live-call demo; simulated mode needs nothing else
   * - *Mic/speaker audio*
     - **PortAudio** (``libportaudio2``)
     - real mic → DTMF and echo; without it, falls back to a tone generator
   * - *Wire-level proof*
     - **tcpdump / Wireshark**
     - to inspect the real SIP/RTP packets

Pure-Python deps (``numpy``, ``fastapi``, ``uvicorn``, ``sounddevice``) — **no external
softphone** (baresip/pjsua/linphone) is needed; the native SIP stack is built in.

1. Install
----------

.. code-block:: bash

   git clone https://github.com/jakub-michalik/callScope.git
   cd callScope
   python3 -m venv .venv && . .venv/bin/activate
   pip install -r requirements.txt

2. Run — simulated (no infrastructure)
--------------------------------------

.. code-block:: bash

   python backend/run.py            # → http://localhost:8000

**No Docker, no Asterisk.** The dashboard comes up fully simulated: dial from the keypad or the
quick-dial chips, watch the token flow through the chain, the Goertzel spectrum light up, the SIP
ladder play out, and inject faults / cut links to see the root-cause correlator localize them.

3. Run — live against Asterisk (real SIP + RTP)
-----------------------------------------------

**a) Start Asterisk in Docker** (binds ``5062``, so it coexists with any host Asterisk on 5060):

.. code-block:: bash

   cd asterisk
   docker compose up -d
   docker exec callscope-asterisk asterisk -rx "pjsip show transports"   # expect 0.0.0.0:5062
   cd ..

**b) Run CallScope in native mode:**

.. code-block:: bash

   CALLSCOPE_SIP_MODE=native python backend/run.py     # → http://localhost:8000

The header should read ``SIP: 🟢 NATIVE (own SIP/RTP → Asterisk)``. You can also switch the
backend and set the Asterisk ``host:port`` live in the top bar — point it at ``127.0.0.1:5062``.

**(optional) baresip backend — a real SIP client in a second container:**

.. code-block:: bash

   cd asterisk
   docker compose --profile live up -d              # starts BOTH containers: asterisk + baresip
   docker logs callscope-baresip | grep "200 OK"    # baresip registered to Asterisk
   nc -z 127.0.0.1 4444 && echo "ctrl_tcp up"
   cd ..

The live adapter auto-connects to the container's ``ctrl_tcp`` on ``127.0.0.1:4444`` — no host
baresip needed. Native is still the recommended path.

**c) Make real calls** — pick up the line, then dial:

.. list-table::
   :header-rows: 1
   :widths: 14 86

   * - Dial
     - Asterisk does
   * - **600**
     - Echo — talk into the mic (use **headphones**), hear yourself back over real RTP
   * - **112**
     - Emergency: Answer + playback (real INVITE → 200 → ACK → RTP)
   * - **503** / **486**
     - Congestion / Busy → real ``SIP_503`` / ``SIP_486`` root cause
   * - **700**
     - Ring a softphone (Linphone) registered to the bundled ``phone`` endpoint
   * - **500**
     - Ring CallScope itself (the native UAS auto-answers)

4. Prove it's real
------------------

.. code-block:: bash

   sudo tcpdump -i lo -n -A 'udp port 5062'        # raw SIP in the console while you call
   python tools/sip_trace.py 600 127.0.0.1 5062    # app-level SIP/RTP trace of the same call

5. Stop
-------

.. code-block:: bash

   # Ctrl-C the app, then stop the containers:
   cd asterisk
   docker compose --profile live down              # stops asterisk + baresip
   cd ..

.. note::

   Header stuck on ``sim`` or getting ``SIP_401``? You're dialing the wrong Asterisk — a host
   service on 5060 instead of the container on 5062. Set the **Asterisk** port field to ``5062``.
   See :doc:`troubleshooting` for the full debugging workflow, and ``ASTERISK.md`` in the repo for
   calling from a phone.
