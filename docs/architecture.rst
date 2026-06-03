Architecture diagrams
======================

Block graph (two planes)
------------------------

CallScope models a call as a graph of blocks connected by **patches**. Audio/RTP travels
along the **media plane**; **SIP** lives on the **control plane** and *gates* the media —
RTP only flows once signaling reaches *in-call*.

.. mermaid::

   flowchart LR
       mic([🎤 mic]) --> Dialer
       Dialer -- patch --> AnalogLine["Analog Line / FXS"]
       AnalogLine -- patch --> DTMF["DTMF Detector"]
       DTMF -- patch --> CodecRTP["Codec / RTP"]
       CodecRTP -- patch --> Gateway["Gateway / Provider"]
       Gateway --> spk([🔊 speaker])

       SIP["SIP Signaling"]:::ctrl
       DTMF -. dialed number .-> SIP
       SIP -. gates media .-> CodecRTP

       classDef ctrl fill:#ede9fe,stroke:#7c3aed,color:#4c1d95;

Event-driven runtime
--------------------

Every block has a dedicated DSP path and a **tap**; it reports samples and diagnostics to a
shared **event bus**. The browser dashboard is a thin view over that bus, fed over a
WebSocket — agnostic to whether events are simulated or live.

.. mermaid::

   flowchart TB
       subgraph chain["Signal-chain blocks (each with its own DSP path + fault injector)"]
         direction LR
         Dialer --> AnalogLine --> DTMF --> CodecRTP --> Gateway
         SIP
       end
       chain -- "taps + diagnostics" --> Bus[("Event Bus")]
       Bus --> Corr["Root-cause correlator"]
       Corr -- "root cause + consequences" --> Bus
       Bus -- WebSocket --> UI["Browser dashboard"]
       UI -- "commands (dial, inject fault, cut link)" --> Bus

SIP call flow (native ↔ Asterisk)
---------------------------------

In native mode CallScope is the SIP user agent itself. This is the real exchange for a call
to Asterisk extension **600** (echo), including the digest-auth round trip:

.. mermaid::

   sequenceDiagram
       participant C as CallScope (native UAC)
       participant A as Asterisk
       C->>A: INVITE sip:600 (SDP offer, PCMU/8000)
       A-->>C: 401 Unauthorized (Digest challenge, realm="asterisk")
       C->>A: ACK
       C->>A: INVITE + Authorization: Digest (computed response)
       A-->>C: 100 Trying
       A-->>C: 200 OK (SDP answer, Asterisk RTP port)
       C->>A: ACK
       Note over C,A: RTP — G.711 µ-law, 50 pkt/s, two-way (echo returns)
       C->>A: BYE
       A-->>C: 200 OK

Native SIP/RTP stack (threads)
------------------------------

The native backend (:mod:`voip.sip_native`) runs three daemon threads. The transmitter is a
steady 20 ms clock, **decoupled** from receive timing so the outgoing audio never gets
choppy; the receiver drains RTP into stats and the speaker.

.. mermaid::

   flowchart LR
       mic["🎤 mic frame"] --> tx
       subgraph SipNativeBackend
         sip["_sip_loop<br/>SIP RX → state machine"]
         tx["_rtp_tx_loop<br/>paced 20 ms TX"]
         rx["_rtp_rx_loop<br/>RTP RX → stats"]
       end
       tx -- "G.711 RTP" --> net((UDP socket))
       net -- "RTP" --> rx
       rx --> spk["🔊 speaker"]
       net <-- "SIP (INVITE/ACK/BYE)" --> sip
       sip -- "digest auth, SDP" --> net
