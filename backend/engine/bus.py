"""Event bus — collects/dispatches event envelopes (DESIGN.md §2.2, §8).

Phase 0: synchronous collector + subscribers. WebSocket will hook in at Phase 3.
"""
from __future__ import annotations
import time
from typing import Callable


class EventBus:
    def __init__(self, collect: bool = True):
        self.collect = collect
        self.events: list[dict] = []
        self._subs: list[Callable[[dict], None]] = []

    def subscribe(self, fn: Callable[[dict], None]) -> None:
        self._subs.append(fn)

    def emit(self, ch: str, t: float, data: dict) -> None:
        # wall: real wall-clock time (epoch seconds) the event occurred — the log
        # timestamps each entry with this; t stays the engine clock (seconds since start).
        env = {"ch": ch, "t": t, "wall": time.time(), "data": data}
        if self.collect:
            self.events.append(env)
        for fn in self._subs:
            fn(env)

    # --- channel shortcuts ---
    def flow(self, t, edge, active, level=0.0, kind="analog"):
        self.emit("flow", t, {"edge": edge, "active": bool(active),
                              "level": float(level), "kind": kind})

    def state(self, t, node, state, health):
        self.emit("state", t, {"node": node, "state": state, "health": health})

    def diag(self, diagnostic):
        self.emit("diag", diagnostic.t, diagnostic.to_envelope()["data"])

    def rootcause(self, t, data):
        self.emit("rootcause", t, data)

    def clear(self):
        self.events.clear()

    def by_ch(self, ch: str) -> list[dict]:
        return [e for e in self.events if e["ch"] == ch]
