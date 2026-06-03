# CallScope ↔ Asterisk (live SIP, Phase 5)

By default CallScope runs **simulated** SIP. In **live mode** it places **real** calls against
**Asterisk** over the wire (Wireshark / sngrep see real SIP/RTP). There are two live backends:

| Mode | `CALLSCOPE_SIP_MODE` | What it is |
|---|---|---|
| **Native** (recommended) | `native` | CallScope is the SIP user agent itself — pure-Python UAC + RTP, no external client. |
| baresip | `live` | CallScope drives a real `baresip` client (Dockerized) over `ctrl_tcp` — interop cross-check. |

```
[CallScope analog side: Dialer→FXS→DTMF]  →  native SIP UAC + RTP (backend/voip)  ⇄ SIP/RTP ⇄  Asterisk
        dialed number ───────────────────────────────────► real INVITE/ACK/BYE        dialplan (112/600/503/486)
```

The **native** stack (`backend/voip/`) is the honest answer to "can you handle SIP and RTP
yourself?": CallScope opens its own UDP sockets, builds INVITE/ACK/BYE, answers digest
challenges (RFC 2617), and streams G.711 RTP — no `baresip`/`pjsua`/`linphone` dependency.

## Prerequisites
- **Docker** (for Asterisk). The native mode needs **nothing else** — it is pure Python.
- (baresip backend: nothing extra — it runs in Docker too; a host apt `baresip` works as a fallback.)

## ⚠️ Port 5060 — the gotcha that wastes hours
Asterisk's default SIP port is **5060**. If you already have a **host** Asterisk/SIP service
(systemd `asterisk`, a softphone, etc.) bound to `0.0.0.0:5060`, a `network_mode: host`
container **cannot also bind 5060** — it silently loses, and every INVITE/REGISTER you send
hits the *host* service with *its* config (which has no `callscope` endpoint, so it challenges
and rejects everything). Symptom: auth "fails" no matter what you do, and the `Server:` header
in the 401 is a different Asterisk version than your container.

To avoid this entirely, the bundled container binds **5062**, not 5060 (`conf/pjsip.conf`
`transport-udp`). Check for a host SIP service with:
```bash
ss -lunp | grep :5060            # who owns 5060
systemctl is-active asterisk     # host Asterisk service?
```

## 1. Start Asterisk
```bash
cd callscope/asterisk
docker compose up -d          # andrius/asterisk (host networking), binds 127.0.0.1:5062
docker compose logs -f        # optional: watch it boot
```
Endpoint `callscope` (user `callscope`, pass `callscope`) is in `conf/pjsip.conf`; the dialplan
is in `conf/extensions.conf`. Confirm the container — not a host Asterisk — answers on 5062:
```bash
docker exec callscope-asterisk asterisk -rx "pjsip show transports"   # 0.0.0.0:5062
```
> Single-file bind-mounts are inode-pinned: if you edit `conf/pjsip.conf` on the host, the
> container keeps seeing the *old* file until you `docker compose restart` (a plain
> `pjsip reload` won't pick it up).

## 2. Run CallScope and pick the backend
```bash
cd callscope && . .venv/bin/activate
python backend/run.py          # then choose "Native (own SIP/RTP)" in the SIP backend dropdown
```
In the **Controls** card: the **SIP backend** dropdown selects `sim` / `native` / `live`, and
the **Asterisk** `host:port` field next to it sets where the live backends dial — pre-filled
with `127.0.0.1:5062` (the bundled container). Change it to `…:5060` for a stock Asterisk, or a
remote host. Switching is hot; no restart needed.

You can also preselect at launch via env: `CALLSCOPE_SIP_MODE` (`native`/`live`),
`CALLSCOPE_ASTERISK`, `CALLSCOPE_SIP_PORT` (default **5062**),
`CALLSCOPE_SIP_USER`/`CALLSCOPE_SIP_PASS` (default `callscope`/`callscope`). The native UAC
binds local SIP `5070` and RTP `40000`.

The dashboard header shows **`SIP: 🟢 NATIVE (own SIP/RTP → Asterisk)`**. If it shows
`sim (…error…)`, the stack couldn't bind/reach Asterisk and fell back — CallScope still works,
just simulated.

> **Getting `ROOT CAUSE — SIP: SIP_401` on native?** You're dialing the wrong Asterisk — almost
> always a **host** Asterisk on 5060 instead of the container on 5062. Set the **Asterisk** port
> field to `5062` (or whatever owns your `callscope` endpoint). See the port gotcha above.

## 3. Make real calls (dial as usual)
The numbers map to the dialplan:
| Dial | Asterisk does | You see |
|---|---|---|
| **112** | Answer + Playback + Wait(6) + Hangup | real INVITE→200→ACK→(BYE), real RTP |
| **600** | Echo() | comfort-noise RTP echoed back → real loss/jitter on the RTP panel |
| **503** | Congestion() | **real 5xx** failure → `SIP_503` root cause |
| **486** | Busy() | **real 486** → `SIP_486` |

> In live mode the **Force 503 / Force 486** buttons do nothing (the dialplan decides the
> outcome). To demo a failure, **dial 503 or 486**.

## 4. Verify the real signaling
```bash
sudo apt install sngrep
sudo sngrep                   # live SIP ladder between CallScope and Asterisk
# or
sudo tcpdump -i lo -n port 5060 -w /tmp/sip.pcap   # Wireshark: Telephony > VoIP Calls
```
In **native** mode the ladder CallScope shows is the **real** per-message exchange (it sends
and parses the SIP itself), so it matches sngrep/Wireshark message-for-message.

## How the native stack is verified
- **`tests/test_voip.py`** — digest passes the RFC 2617 §3.5 worked example
  (`6629fae49393a05397450978507c4ef1`); RTP pack/unpack + G.711 μ-law roundtrip; loss/jitter.
- **`tests/test_sip_native.py`** — the UAC drives a **full authenticated call** end-to-end
  (INVITE → 401 → digest re-INVITE → 200+SDP → ACK → BYE) against a local reference UAS that
  verifies the digest independently; a wrong password is correctly rejected (`FAILED 401`).
  This proves the stack against a known-correct peer **without** depending on any one Asterisk
  image.

**Verified live against the bundled Asterisk** (container on 5062, digest auth on):
- **`600` (Echo):** `INVITE → 200 → ACK` → INCALL; RTP negotiated to the Asterisk port from SDP,
  comfort-noise sent, echo received (real packets, measured jitter), clean BYE → TERMINATED.
- **`503` (Congestion):** `INVITE → 503` → FAILED, `fail_code=503` → `SIP_503` root cause.

> Earlier this doc blamed the `andrius` image for "rejecting auth". That was a **misdiagnosis**:
> a **host** Asterisk service was bound to 5060 and intercepting every request with its own
> config. Moving the container to 5062 (see the port gotcha above) fixed it — the digest stack
> was correct all along.

## baresip backend (`live`) — a real SIP client, in Docker

`CALLSCOPE_SIP_MODE=live` (or picking **baresip** in the UI) drives an off-the-shelf **baresip**
client over its `ctrl_tcp` interface (`127.0.0.1:4444`) — an interop cross-check that the same
Asterisk endpoint also works with a third-party client. The native stack supersedes it, but it's
fully Dockerized so you need nothing on the host.

### Run it from Docker (recommended)
The bundled `docker-compose.yml` has a `baresip` service (build: [`baresip/`](asterisk/baresip/))
behind the `live` profile. It registers to Asterisk and exposes `ctrl_tcp` on `0.0.0.0:4444`:
```bash
cd asterisk
docker compose --profile live up -d              # starts asterisk + baresip
docker logs callscope-baresip | grep "200 OK"    # baresip registered: 200 OK (Asterisk PBX …)
nc -z 127.0.0.1 4444 && echo "ctrl_tcp is up"
```
The baresip container ([`asterisk/baresip/`](asterisk/baresip/)) is `debian:bookworm-slim` + apt
`baresip`, configured headless with `ctrl_tcp`, `g711` and an account pointing at Asterisk on 5062.

### How CallScope uses it
The `SipAdapter` **auto-detects** a running ctrl_tcp:
- if `127.0.0.1:4444` is already listening (the Docker `baresip` service) → it just **connects** to it;
- otherwise it falls back to **spawning a host-installed `baresip`** (apt `baresip`, writing a
  config to `~/.callscope-baresip/`).

So `live` mode works either fully from Docker, or against a host baresip — no code change needed.
The adapter maps baresip's call-state events to the SIP ladder (baresip hides the per-message SIP,
so the 401/digest round trip happens internally — unlike the native backend, which shows every message).

## What's simulated vs real here
- **Real (native):** SIP signaling (INVITE/ACK/BYE + digest), response codes (200/486/503),
  RTP packets on the wire (Wireshark-visible), measured loss/jitter on the RTP panel.
- **Still CallScope's:** the analog/DTMF side, fault injection on the analog/RTP blocks, and the
  root-cause correlator overlaid on the real call.
