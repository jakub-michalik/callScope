#!/usr/bin/env python3
"""Refresh docs/screenshots/*.png from the live dashboard via headless Chrome.

Self-contained: launches the app in `native` SIP mode (own SIP/RTP -> the bundled
Asterisk), opens the dashboard in headless Chrome over the DevTools Protocol,
captures the idle dashboard, then drives a real call to extension 600 (echo) over
the WebSocket API and captures the call-in-progress view (SIP ladder + live RTP).

Usage:  python tools/capture_screenshots.py
Requires: google-chrome / chromium on PATH, the Asterisk container running, and a
working host audio stack (the call uses the native RTP path). Mute your speaker to
avoid echo feedback, e.g. `pactl set-sink-mute @DEFAULT_SINK@ 1`.
"""
from __future__ import annotations
import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

import websockets

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "screenshots")
PORT = int(os.environ.get("CS_PORT", "8021"))
CDP_PORT = int(os.environ.get("CS_CDP_PORT", "9333"))
HTTP = f"http://127.0.0.1:{PORT}/"
WS = f"ws://127.0.0.1:{PORT}/ws"
W, H = 1680, 1050


def _chrome_bin() -> str:
    for c in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        if shutil.which(c):
            return c
    sys.exit("no chrome/chromium on PATH")


async def _cdp(ws, mid, method, params=None):
    """Send one CDP command and return its result (skipping async events)."""
    await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == mid:
            return msg.get("result", {})


async def _page_ws_url() -> str:
    """Find the dashboard page target's CDP WebSocket URL."""
    for _ in range(50):
        try:
            targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json"))
            for t in targets:
                if t.get("type") == "page" and str(t.get("url", "")).startswith(HTTP):
                    return t["webSocketDebuggerUrl"]
        except Exception:
            pass
        await asyncio.sleep(0.2)
    sys.exit("dashboard page target not found")


async def _shot(ws, path: str):
    """Capture a full-page PNG to `path`."""
    m = await _cdp(ws, 100, "Page.getLayoutMetrics")
    size = m.get("cssContentSize") or m.get("contentSize")
    clip = {"x": 0, "y": 0, "width": size["width"],
            "height": size["height"], "scale": 1}
    res = await _cdp(ws, 101, "Page.captureScreenshot",
                     {"format": "png", "captureBeyondViewport": True, "clip": clip})
    with open(path, "wb") as f:
        f.write(base64.b64decode(res["data"]))
    print(f"  wrote {os.path.relpath(path, ROOT)}  ({size['width']:.0f}x{size['height']:.0f})")


async def _dial(number: str):
    """Place a call over the dashboard WS API (server-side state, outlives this client)."""
    async with websockets.connect(WS) as ws:
        await ws.recv()                                   # hello
        await ws.send(json.dumps({"cmd": "set_source", "args": {"mode": "gen"}}))
        await ws.send(json.dumps({"cmd": "start_call", "args": {"number": number}}))
        await asyncio.sleep(0.3)


async def main():
    os.makedirs(OUT, exist_ok=True)
    env = {**os.environ, "PYTHONPATH": os.path.join(ROOT, "backend"),
           "CALLSCOPE_SIP_MODE": "native", "CALLSCOPE_SIP_PORT": "5062",
           # use non-default local ports so capture coexists with a running instance
           "CALLSCOPE_SIP_LOCAL_PORT": "5074", "CALLSCOPE_RTP_LOCAL_PORT": "40040"}
    app = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app",
                            "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
                           env=env, cwd=ROOT)
    chrome = subprocess.Popen([_chrome_bin(), "--headless=new",
                               f"--remote-debugging-port={CDP_PORT}",
                               f"--window-size={W},{H}", "--hide-scrollbars",
                               "--force-device-scale-factor=1", "--no-sandbox",
                               "--no-first-run", "--no-default-browser-check", HTTP],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(4)                                     # app + chrome warm up
        url = await _page_ws_url()
        async with websockets.connect(url, max_size=None) as ws:
            await _cdp(ws, 1, "Page.enable")
            await asyncio.sleep(2.5)                       # dashboard connects + renders hello
            print("capturing idle dashboard...")
            await _shot(ws, os.path.join(OUT, "dashboard.png"))
            print("placing call to 600 (echo)...")
            await _dial("600")
            await asyncio.sleep(7)                          # DTMF dial + INVITE/digest/200/ACK + RTP
            print("capturing call in progress...")
            await _shot(ws, os.path.join(OUT, "call-in-progress.png"))
    finally:
        for p in (chrome, app):
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    asyncio.run(main())
