# syntax=docker/dockerfile:1
ARG PYTHON_IMAGE=python:3.12-alpine@sha256:c5ba3deef00c7758561a0f03c26f44f10589263d40e732b8748e0d1f38f6dd89

# Apply the available Alpine security fix once for compatible builder/runtime libraries.
FROM ${PYTHON_IMAGE} AS base
RUN apk add --no-cache zlib=1.4-r0 krb5-libs=1.22.3-r0

FROM base AS builder
WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --require-hashes --only-binary=:all: -r requirements.txt

FROM base AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    BACKEND=local \
    METRICS_HOST=0.0.0.0 \
    METRICS_PORT=9100 \
    DATABASE_PATH=/data/canopy.db
RUN addgroup -g 10001 appuser \
    && adduser -D -u 10001 -G appuser appuser \
    && mkdir /data \
    && chown appuser:appuser /data
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY canopy/ ./canopy/
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3)"]
CMD ["python", "-m", "canopy.api"]

