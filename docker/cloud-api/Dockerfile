# syntax=docker/dockerfile:1
ARG PYTHON_IMAGE=python:3.12-alpine@sha256:c5ba3deef00c7758561a0f03c26f44f10589263d40e732b8748e0d1f38f6dd89
FROM ${PYTHON_IMAGE} AS base
SHELL ["/bin/ash", "-eo", "pipefail", "-c"]
# Alpine 3.5.9-r1 backports the November OpenSSL security fixes.
RUN apk add --no-cache zlib=1.4-r0 krb5-libs=1.22.3-r0 libcrypto3=3.5.9-r1 libssl3=3.5.9-r1
# Preserve all other Python native providers while removing SQLite explicitly.
RUN apk --no-network info --depends .python-rundeps 2>/dev/null | sed -n '/^so:/p' | grep -v '^so:libsqlite3' > /tmp/providers \
    && xargs apk --no-network add --virtual .cloud-rundeps < /tmp/providers \
    && apk --no-network del .python-rundeps sqlite-libs \
    && rm /tmp/providers \
    && rm -rf /usr/local/lib/python3.12/sqlite3 \
    && find /usr/local/lib/python3.12/lib-dynload -name '_sqlite3*.so' -delete

FROM base AS builder
WORKDIR /build
RUN python -m venv /opt/venv
COPY app/requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    /opt/venv/bin/pip install --require-hashes --only-binary=:all: -r requirements.txt

FROM base AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    BACKEND=postgres \
    METRICS_HOST=0.0.0.0 \
    METRICS_PORT=9100
RUN addgroup -g 10001 appuser && adduser -D -u 10001 -G appuser appuser
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY app/canopy/__init__.py app/canopy/api.py app/canopy/domain.py app/canopy/postgres.py app/canopy/metrics.py app/canopy/local_metrics.py ./canopy/
COPY app/canopy/static/ ./canopy/static/
COPY docker/cloud-api/storage.py ./canopy/storage.py
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3)"]
CMD ["python", "-m", "canopy.api"]
