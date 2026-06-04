#!/usr/bin/env bash
# Live SIP call-flow ("ladder") in the console for CallScope <-> Asterisk.
#
#   ./tools/sip_ladder.sh [-t|--text] [-i IFACE] [port]
#       -t, --text    plain tcpdump text output (default: sngrep arrow ladder)
#       -i IFACE      capture interface (default: lo — CallScope's SIP is loopback)
#       port          SIP port (default 5062)
#
# Auto-elevates with sudo (raw capture needs root). The bundled setup talks SIP on
# 127.0.0.1:<port>, i.e. the loopback interface, so we capture on `lo` by default —
# NOT `any`, whose LINUX_SLL2 "cooked" link type sngrep fails to dissect (empty
# call list). For a remote Asterisk, pass `-i <lan-iface>`.
#
# sngrep is a TUI: when it opens, place a call — it shows up in the dialog list;
# arrow-select it and press Enter for the arrow ladder. Press q to quit.
set -uo pipefail

# raw capture needs root — re-exec under sudo if we are not already root
if [ "$(id -u)" -ne 0 ]; then
    exec sudo "$(realpath "$0")" "$@"
fi

TEXT=0
DEV=lo
while [ $# -gt 0 ]; do
    case "$1" in
        -t|--text) TEXT=1; shift ;;
        -i)        DEV="${2:?-i needs an interface}"; shift 2 ;;
        *)         break ;;
    esac
done
PORT="${1:-5062}"

if [ "$TEXT" -eq 0 ] && command -v sngrep >/dev/null 2>&1; then
    echo "sngrep on '$DEV': place a call in CallScope, then select it + Enter for the ladder (q quits)." >&2
    exec sngrep -d "$DEV" "port ${PORT}"
fi

echo "tcpdump on '$DEV': place a call in CallScope to see the flow (Ctrl-C to stop)." >&2
echo >&2
tcpdump -i "$DEV" -A -n -s0 -l "udp port ${PORT}" 2>/dev/null \
    | grep --line-buffered -oaE '(INVITE|ACK|BYE|CANCEL|REGISTER|OPTIONS) sip:.*|SIP/2\.0 [0-9]+ .*'
