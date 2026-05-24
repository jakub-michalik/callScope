"""Real audio I/O via sounddevice (DESIGN.md §9, Phase 1).

Bridge between the sound card and the engine (8 kHz, 160-sample frames):
 - microphone -> ring buffer IN  -> read_frame()  (source for the graph)
 - write_frame() -> ring buffer OUT -> speaker

We request streams directly at 8 kHz (the OS does the resampling), so no scipy.
When the library/device is missing -> disabled mode (read=zeros, write=no-op),
the chain works as in Phase 0 (generator).
"""
from __future__ import annotations
from collections import deque
import numpy as np

from engine.const import FS, FRAME_N

try:
    import sounddevice as sd
    _HAVE_SD = True
except Exception:                      # missing portaudio / library
    sd = None
    _HAVE_SD = False


class AudioIO:
    def __init__(self, fs: int = FS, blocksize: int = FRAME_N,
                 in_device=None, out_device=None, ring_frames: int = 50):
        self.fs = fs
        self.blocksize = blocksize
        self.in_device = in_device
        self.out_device = out_device
        self._in = deque(maxlen=ring_frames)      # producer: mic callback -> DSP chain
        self._call_in = deque(maxlen=ring_frames) # producer: mic callback -> live SIP call (TX)
        self._out = deque(maxlen=ring_frames)     # producer: engine/call -> speaker
        self._in_stream = None
        self._out_stream = None
        self.enabled = False
        self.error: str | None = None
        self.has_input = False
        self.has_output = False

    # --- lifecycle ---
    def start(self) -> "AudioIO":
        if not _HAVE_SD:
            self.error = "sounddevice/portaudio unavailable"
            return self
        try:
            self._out_stream = sd.OutputStream(
                samplerate=self.fs, channels=1, blocksize=self.blocksize,
                dtype="float32", device=self.out_device, callback=self._out_cb)
            self._out_stream.start()
            self.has_output = True
        except Exception as e:                  # no output -> too bad, run without it
            self.error = f"output: {e}"
        try:
            self._in_stream = sd.InputStream(
                samplerate=self.fs, channels=1, blocksize=self.blocksize,
                dtype="float32", device=self.in_device, callback=self._in_cb)
            self._in_stream.start()
            self.has_input = True
        except Exception as e:
            self.error = (self.error + " | " if self.error else "") + f"input: {e}"
        self.enabled = self.has_input or self.has_output
        return self

    def stop(self):
        for s in (self._in_stream, self._out_stream):
            try:
                if s is not None:
                    s.stop(); s.close()
            except Exception:
                pass
        self._in_stream = self._out_stream = None
        self.enabled = False

    # --- audio thread callbacks ---
    def _in_cb(self, indata, frames, time_info, status):
        frame = np.asarray(indata[:, 0], dtype=np.float32).copy()
        self._in.append(frame)
        self._call_in.append(frame)            # second tap: independent consumer for the call

    def _out_cb(self, outdata, frames, time_info, status):
        if self._out:
            buf = self._out.popleft()
            n = min(len(buf), frames)
            outdata[:n, 0] = buf[:n]
            if n < frames:
                outdata[n:, 0] = 0.0
        else:
            outdata[:, 0] = 0.0

    # --- engine API (asyncio thread) ---
    def read_frame(self) -> np.ndarray:
        """160-sample frame from the microphone (or silence on under-run)."""
        if self._in:
            buf = self._in.popleft()
            if len(buf) == self.blocksize:
                return buf
            out = np.zeros(self.blocksize, np.float32)
            out[:min(len(buf), self.blocksize)] = buf[:self.blocksize]
            return out
        return np.zeros(self.blocksize, np.float32)

    def read_call_frame(self) -> np.ndarray:
        """160-sample mic frame for the live SIP call (independent of the DSP-chain tap)."""
        if self._call_in:
            buf = self._call_in.popleft()
            if len(buf) == self.blocksize:
                return buf
            out = np.zeros(self.blocksize, np.float32)
            out[:min(len(buf), self.blocksize)] = buf[:self.blocksize]
            return out
        return np.zeros(self.blocksize, np.float32)

    def write_frame(self, samples: np.ndarray) -> None:
        """Insert a frame into the speaker buffer."""
        if not self.has_output:
            return
        s = np.asarray(samples, np.float32)
        if len(s) != self.blocksize:
            buf = np.zeros(self.blocksize, np.float32)
            buf[:min(len(s), self.blocksize)] = s[:self.blocksize]
            s = buf
        self._out.append(s)

    @property
    def status(self) -> dict:
        return {"enabled": self.enabled, "input": self.has_input,
                "output": self.has_output, "error": self.error,
                "have_lib": _HAVE_SD}
