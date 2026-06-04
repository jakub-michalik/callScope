#!/bin/sh
# Select baresip's audio backend at container start:
#   BARESIP_AUDIO=host -> real ALSA -> host PipeWire/PulseAudio (audible echo)
#   anything else      -> file-based (silent WAV in, file out), the default so
#                         headless/CI runs need no audio device on the host.
if [ "$BARESIP_AUDIO" = "host" ]; then
    cp /root/.baresip/config-alsa /root/.baresip/config
fi
exec baresip -f /root/.baresip
