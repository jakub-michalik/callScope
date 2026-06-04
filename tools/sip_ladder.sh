#!/usr/bin/env bash
# Live SIP call-flow ("ladder") in the console for CallScope <-> Asterisk.
#
#   ./tools/sip_ladder.sh [-t|--text] [port]
#       -t, --text   force plain tcpdump text output (default: sngrep if available)
#       port         SIP port (default 5062)
#
# Auto-elevates with sudo (raw packet capture needs root). SIP rides loopback, so
# `-i any` catches it. Start this FIRST, then place a call in CallScope.
#
# sngrep is a TUI: when it opens, place a call — it appears in the dialog list;
# arrow-select it and press Enter to see the arrow ladder. Press q to quit.
set -uo pipefail

# raw capture needs root — re-exec under sudo if we are not already root
if [ "$(id -u)" -ne 0 ]; then
    exec sudo "$(realpath "$0")" "$@"
fi

TEXT=0
if [ "${1:-}" = "-t" ] || [ "${1:-}" = "--text" ]; then TEXT=1; shift; fi
PORT="${1:-5062}"

if [ "$TEXT" -eq 0 ] && command -v sngrep >/dev/null 2>&1; then
    echo "sngrep: place a call in CallScope, then arrow-select it + Enter for the ladder (q quits)." >&2
    exec sngrep -d any "port ${PORT}"
fi

echo "tcpdump: place a call in CallScope to see the flow (Ctrl-C to stop)." >&2
echo >&2
tcpdump -i any -A -n -s0 -l "udp port ${PORT}" 2>/dev/null \
    | grep --line-buffered -oaE '(INVITE|ACK|BYE|CANCEL|REGISTER|OPTIONS) sip:.*|SIP/2\.0 [0-9]+ .*'
