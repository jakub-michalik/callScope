FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends baresip baresip-core ca-certificates && rm -rf /var/lib/apt/lists/*
COPY config accounts audio8k.wav /root/.baresip/
ENTRYPOINT ["baresip","-f","/root/.baresip"]
