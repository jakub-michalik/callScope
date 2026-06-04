#!/usr/bin/env bash
# Live SIP call-flow ("ladder") in the console for CallScope <-> Asterisk.
#
#   sudo ./tools/sip_ladder.sh [port]      # default port 5062
#
# Prefers sngrep (an arrow ladder with timestamps + RTP). If sngrep is not
# installed, falls back to tcpdump + grep, printing the clean SIP start-lines
# (request lines and response status lines) of every message as it flies past.
#
# Needs root: raw packet capture requires it. SIP rides loopback (127.0.0.1:port),
# so `-i any` catches it. Start this first, THEN place a call in CallScope.
set -uo pipefail
PORT="${1:-5062}"

if command -v sngrep >/dev/null 2>&1; then
    exec sngrep -d any "port ${PORT}"
fi

echo "sngrep not found (install: sudo apt install sngrep) — using tcpdump fallback." >&2
echo "Place a call in CallScope to see the flow. Ctrl-C to stop." >&2
echo >&2

tcpdump -i any -A -n -s0 -l "udp port ${PORT}" 2>/dev/null \
    | grep --line-buffered -oaE '(INVITE|ACK|BYE|CANCEL|REGISTER|OPTIONS) sip:.*|SIP/2\.0 [0-9]+ .*'
