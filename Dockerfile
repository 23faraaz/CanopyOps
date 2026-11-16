# syntax=docker/dockerfile:1
ARG PYTHON_IMAGE=python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

# Apply the available Debian security fix once for compatible builder/runtime libraries.
FROM ${PYTHON_IMAGE} AS base
RUN apt-get update \
    && apt-get install --yes --no-install-recommends zlib1g=1:1.3.dfsg+really1.3.1-1+deb13u1 \
    && rm -rf /var/lib/apt/lists/*

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
RUN groupadd --gid 10001 appuser \
    && useradd --uid 10001 --gid 10001 --no-log-init --create-home appuser \
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

