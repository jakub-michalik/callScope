"""Global simulation constants (DESIGN.md §1)."""
import numpy as np

FS = 8000           # Hz, sample rate (telephony standard)
FRAME_MS = 20       # ms per frame
FRAME_N = 160       # samples = FS * FRAME_MS / 1000
DTMF_WIN_N = 205    # Goertzel detection window (~25.6 ms, ITU class)
TICK_S = 0.020      # 20 ms
RT_FACTOR = 1.0     # 1.0 = real-time; <1 = slower (demo)

SCOPE_FPS = 30
SCOPE_POINTS = FRAME_N
SPECTRUM_FPS = 20

# Signal presence threshold (RMS) — shared by blocks and graph
PRESENCE_RMS = 5e-4

# DTMF frequencies [Hz]
DTMF_LOW = (697, 770, 852, 941)        # rows
DTMF_HIGH = (1209, 1336, 1477, 1633)   # columns
DTMF_FREQS = DTMF_LOW + DTMF_HIGH

# Map (row, column) -> character
DTMF_KEYS = (
    ("1", "2", "3", "A"),
    ("4", "5", "6", "B"),
    ("7", "8", "9", "C"),
    ("*", "0", "#", "D"),
)

# Reverse map: character -> (f_low, f_high)
DTMF_TONE = {
    DTMF_KEYS[r][c]: (DTMF_LOW[r], DTMF_HIGH[c])
    for r in range(4) for c in range(4)
}


def dtmf_k(f: float, n: int = DTMF_WIN_N) -> int:
    """Goertzel bin number for frequency f."""
    return int(round(n * f / FS))
