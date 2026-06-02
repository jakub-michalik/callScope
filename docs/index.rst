CallScope
=========

**An oscilloscope for phone calls.** A live, in-browser test bench that walks a call
through the whole **FXS → VoIP** chain — analog dialer → FXS line → DTMF decode → SIP →
RTP/codec → gateway — visualizes every stage, lets you inject faults and cut links, and
**localizes the root cause** when a call breaks. It runs fully simulated, or becomes a real
SIP user agent that places actual calls against **Asterisk** with its own pure-Python
SIP + RTP stack (no external softphone).

.. image:: screenshots/dashboard.png
   :alt: CallScope dashboard
   :width: 100%

This site documents the internals: the block-graph engine, the DSP and protocol
primitives (Goertzel DTMF, SIP digest, RTP/G.711), the signal-chain blocks, and the
root-cause correlator.

.. toctree::
   :maxdepth: 2
   :caption: Contents

   overview
   api

Indices
-------

* :ref:`genindex`
* :ref:`modindex`
