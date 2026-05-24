"""Checks the microphone and speaker. Run: python -m audio.selftest

Plays the DTMF tone '5' through the speaker (~1.5 s), then records ~1.5 s from
the microphone and prints the RMS level and the detected dominant frequency.
"""
from __future__ import annotations
import sys
import time
import numpy as np

sys.path.insert(0, __file__.rsplit("/audio/", 1)[0])  # backend/ on path

from engine.const import FS, FRAME_N
from dsp.tone_gen import dtmf_samples
from dsp.goertzel import GoertzelBank
from dsp.metrics import rms
from audio.io import AudioIO


def main():
    io = AudioIO().start()
    print("Status audio:", io.status)
    if not io.enabled:
        print("No working audio — the simulator still runs in generator mode.")
        return 1

    if io.has_output:
        print("► Playing DTMF tone '5' (770+1336 Hz)…")
        tone = dtmf_samples("5", dur_ms=1500, level=0.4)
        for i in range(0, len(tone), FRAME_N):
            io.write_frame(tone[i:i + FRAME_N])
            time.sleep(FRAME_N / FS)
        time.sleep(0.3)

    if io.has_input:
        print("► Recording 1.5 s from the microphone — say something / play a tone…")
        buf = []
        t_end = time.time() + 1.5
        while time.time() < t_end:
            buf.append(io.read_frame())
            time.sleep(FRAME_N / FS)
        x = np.concatenate(buf) if buf else np.zeros(FRAME_N, np.float32)
        level = rms(x)
        bank = GoertzelBank()
        mags = bank.magnitudes(x[-205:] if len(x) >= 205 else x)
        dom = max(mags, key=mags.get)
        print(f"  Microphone RMS: {level:.4f}  (>0.001 = signal present)")
        print(f"  Dominant DTMF-bin frequency: {dom} Hz")

    io.stop()
    print("OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
