# syntax=docker/dockerfile:1
# PostgreSQL with WAL-G beside it. PostgreSQL archives each finished write-ahead log segment
# with `wal-g wal-push` (archive_command), and walg-loop, run as the pod's second container,
# takes a base backup each night and says how the archive is doing. The README shows the pod.
# WAL-G is the release's own build for glibc 2.39, checked against its published SHA-256; the
# image is Debian 13's PostgreSQL, glibc 2.41. One image per PostgreSQL major: POSTGRES picks it.
ARG POSTGRES=16.14
FROM docker.io/library/postgres:${POSTGRES}
# The official image has no CA certificates, and WAL-G talks to Google over HTTPS.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ADD --chmod=0755 --checksum=sha256:8015836f246a978f27b31a2462eac7765312558ae5b11ac033322b555abf9e22 \
    https://github.com/wal-g/wal-g/releases/download/v3.0.9/wal-g-pg-24.04-amd64 /usr/local/bin/wal-g
COPY --chmod=0755 walg-loop.sh /usr/local/bin/walg-loop
# Uploads compress with brotli: pg-backup's restore reads segments by that name.
ENV WALG_COMPRESSION_METHOD=brotli
# PostgreSQL's own user, which owns the data directory and /var/run/postgresql: the image's
# entrypoint runs as it without needing root (the official image's "arbitrary --user" notes).
USER 999:999
