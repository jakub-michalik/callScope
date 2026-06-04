FROM debian:bookworm-slim
# libasound2-plugins provides the ALSA->PulseAudio plugin used when BARESIP_AUDIO=host
RUN apt-get update && apt-get install -y --no-install-recommends \
        baresip baresip-core libasound2-plugins ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY config config-alsa accounts audio8k.wav /root/.baresip/
COPY asound.conf /etc/asound.conf
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
