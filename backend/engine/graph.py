"""Graph.tick — frame flow through the chain of blocks (DESIGN.md §4)."""
from __future__ import annotations

from .frame import Frame
from .patch import Patch
from .bus import EventBus
from .const import FRAME_N, FS, PRESENCE_RMS
from dsp.metrics import rms


def _health(diags) -> str:
    if any(d.severity == "error" for d in diags):
        return "red"
    if any(d.severity == "warn" for d in diags):
        return "amber"
    return "green"


class Graph:
    """Chain of blocks + links. The first block is the source."""

    def __init__(self, blocks, bus: EventBus | None = None):
        self.blocks = blocks
        self.bus = bus or EventBus()
        self.patches: list[Patch] = []
        for a, b in zip(blocks, blocks[1:]):
            self.patches.append(Patch(a.name, b.name))
        self.seq = 0
        self.t = 0.0
        self.session_id = None
        self.reached: list[str] = []
        self.cut_at: str | None = None
        self.last_conditions: list[dict] = []

    def patch(self, src: str, dst: str) -> Patch:
        for p in self.patches:
            if p.src == src and p.dst == dst:
                return p
        raise KeyError(f"no link {src}→{dst}")

    def tick(self) -> None:
        frame = Frame.silence(self.seq, self.t)
        if self.session_id:
            frame.meta["session_id"] = self.session_id

        self.reached = []          # blocks actually processed this tick
        self.cut_at = None         # id of the first cut link reached (or None)
        self.last_conditions = []  # active conditions gathered from reached blocks
        for i, blk in enumerate(self.blocks):
            out = blk.process(frame)
            diags = blk.detect()
            for d in diags:
                self.bus.diag(d)
            self.reached.append(blk.name)
            self.last_conditions += blk.conditions(self.t)
            active = rms(out.samples) > PRESENCE_RMS
            self.bus.state(self.t, blk.name,
                           "active" if active else "idle", _health(diags))

            patch = self.patches[i] if i < len(self.patches) else None
            if patch is not None:
                if not patch.connected:
                    self.cut_at = patch.id
                    self.last_conditions.append(
                        {"code": "SIGNAL_CUT", "block": patch.id,
                         "severity": "warn", "t": self.t})
                    self.bus.flow(self.t, patch.id, active=False, kind=patch.kind)
                    break
                out = patch.apply(out)
                r = rms(out.samples)
                self.bus.flow(self.t, patch.id,
                              active=r > PRESENCE_RMS,
                              level=min(r, 1.0), kind=patch.kind)
            frame = out

        self.seq += 1
        self.t += FRAME_N / FS

    def run(self, n_ticks: int) -> None:
        for _ in range(n_ticks):
            self.tick()
