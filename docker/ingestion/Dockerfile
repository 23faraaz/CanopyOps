# syntax=docker/dockerfile:1
FROM python:3.12-alpine@sha256:c5ba3deef00c7758561a0f03c26f44f10589263d40e732b8748e0d1f38f6dd89 AS base
SHELL ["/bin/ash", "-eo", "pipefail", "-c"]
RUN apk add --no-cache zlib=1.4-r0 krb5-libs=1.22.3-r0
# PostgreSQL-only image: retain other Python providers, exclude unused SQLite.
RUN apk --no-network info --depends .python-rundeps 2>/dev/null | sed -n '/^so:/p' | grep -v '^so:libsqlite3' > /tmp/providers \
    && xargs apk --no-network add --virtual .queue-rundeps < /tmp/providers \
    && apk --no-network del .python-rundeps sqlite-libs \
    && rm /tmp/providers
FROM base AS builder
WORKDIR /build
RUN python -m venv /opt/venv
COPY app/requirements-queue.txt ./
RUN /opt/venv/bin/pip install --require-hashes --only-binary=:all: -r requirements-queue.txt
FROM base AS runtime
ENV PATH="/opt/venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 METRICS_HOST=0.0.0.0 METRICS_PORT=9100
RUN addgroup -g 10001 appuser \
    && adduser -D -u 10001 -G appuser appuser \
    && mkdir /data && chown appuser:appuser /data
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY app/canopy/__init__.py app/canopy/domain.py app/canopy/postgres.py app/canopy/metrics.py ./canopy/
COPY app/ingestion/ ./ingestion/
COPY scripts/recovery/ledger.py ./scripts/recovery/ledger.py
USER 10001:10001
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=6 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9100/metrics',timeout=2)"]
CMD ["python", "-m", "ingestion.worker"]
