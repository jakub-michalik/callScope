"""Root-cause correlator (DESIGN.md §7.3, LEVEL 3).

The banner is driven by a per-tick SNAPSHOT of the conditions that are currently
active on REACHABLE blocks (plus active link cuts). Because the snapshot is rebuilt
every tick from current state, a condition on a block that stopped running (e.g. an
upstream link was cut) simply disappears — no orphaned sticky state.

Momentary EVENTS (DTMF rejects, etc.) are fed separately and kept for a short window
so they can appear as downstream consequences.
"""
from __future__ import annotations


class Correlator:
    """A link "A→B" ranks just after its upstream block A. Chain order comes from the graph."""

    def __init__(self, bus, window: float = 0.6, chain: list | None = None):
        self.bus = bus
        self.window = window
        self.chain = {name: i for i, name in enumerate(chain or [])}
        self.conditions: list[dict] = []   # current snapshot (reachable blocks + cuts)
        self.recent: list[dict] = []       # momentary events, pruned by window
        self.last_key = None

    def update(self, conditions: list[dict]) -> None:
        """Replace the active-condition snapshot (called once per tick)."""
        self.conditions = conditions

    def feed(self, data: dict) -> None:
        """Record a momentary event (sticky conditions are handled by update())."""
        if data.get("severity") == "info" or data.get("sticky"):
            return
        self.recent.append(data)

    def _index(self, block: str) -> float:
        if block in self.chain:
            return float(self.chain[block])
        if "→" in block:                  # a link -> just after its source block
            src = block.split("→", 1)[0]
            return self.chain.get(src, 99) + 0.5
        return 99.0

    def tick(self, t: float) -> None:
        self.recent = [d for d in self.recent if t - d.get("t", t) <= self.window]
        items = self.conditions + self.recent
        root = self._root(items)
        key = ((root["root_code"], root["root_block"], tuple(root["consequences"]))
               if root else None)
        if key != self.last_key:
            self.last_key = key
            if root:
                self.bus.rootcause(t, root)
            else:
                self.bus.emit("rootcause", t, {"cleared": True})

    def _root(self, items: list) -> dict | None:
        if not items:
            return None
        ranked = sorted(items, key=lambda d: (self._index(d["block"]), d.get("t", 0)))
        r = ranked[0]
        seen, cons = set(), []
        for d in ranked[1:]:
            if d["code"] not in seen:
                seen.add(d["code"])
                cons.append(d["code"])
        return {"root_code": r["code"], "root_block": r["block"],
                "summary": self._summary(r), "consequences": cons}

    @staticmethod
    def _summary(r: dict) -> str:
        block, code = r["block"], r["code"]
        table = {
            "SIGNAL_CUT": f"signal cut on link {block}",
            "FXS_NO_LOOP_CURRENT": "no loop current — line not seized at the FXS port",
            "LINE_LOW_SNR": "noisy analog line degrading the tones",
            "DTMF_TWIST_OOR": "tone level imbalance (twist) out of range",
            "DTMF_REJECTED": "tone present but not decodable (low SNR / collision)",
            "DTMF_TOO_SHORT": "tone too short to be accepted",
            "RTP_LOSS_SPIKE": "RTP packet loss degrading the media stream",
            "RTP_JITTER_HIGH": "high RTP jitter",
            "MOS_LOW": "poor call quality (low MOS) at the gateway",
            "SIP_503": "provider rejected the call — 503 Service Unavailable",
            "SIP_486": "callee busy — 486 Busy Here",
            "SIP_484": "address incomplete — number dialed isn't a complete extension (484)",
            "SIP_404": "number not found in the dialplan — 404 Not Found",
            "SIP_401": "authentication rejected (401) — wrong credentials or wrong Asterisk/port",
            "SIP_408": "no answer / request timeout — 408",
            "SIP_603": "call declined / endpoint unreachable — 603",
            "ONE_WAY_AUDIO": "signaling is up (200 OK) but media never reaches the far end",
        }
        if code in table:
            return table[code]
        if code.startswith("SIP_"):                 # any other SIP code
            return f"provider returned {code.split('_', 1)[1]}"
        return code                                  # banner already prepends the block
