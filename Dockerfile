# syntax=docker/dockerfile:1.7
# ─────────────────────────────────────────────────────────────────────────────
# VecturaFlow API — production Dockerfile.
# Multi-stage build: a build stage with compile toolchain, a slim runtime
# stage with only the wheels + source needed to serve requests.
# ─────────────────────────────────────────────────────────────────────────────

ARG PYTHON_VERSION=3.11-slim-bookworm

# ── 1. builder ───────────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION} AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=180

WORKDIR /build

# System packages only needed for compilation (gcc for c-extensions, etc.)
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential gcc \
    && rm -rf /var/lib/apt/lists/*

# Install runtime dependencies from a pre-fetched wheelhouse for build stability
COPY requirements.runtime.txt ./
COPY wheelhouse/ ./wheelhouse/
RUN pip install --no-index --find-links=/build/wheelhouse --prefix=/install -r requirements.runtime.txt


# ── 2. runtime ───────────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PORT=8000 \
    API_ENV=production \
    API_DEBUG=false

# Minimal runtime deps (curl for healthcheck, tini as PID 1)
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1001 app \
    && useradd  --system --uid 1001 --gid app --home-dir /app --shell /bin/bash app

WORKDIR /app

# Wheels from builder
COPY --from=builder /install /usr/local

# Source — copied after deps so code changes don't bust the dep-install layer
COPY --chown=app:app api/ ./api/
COPY --chown=app:app ingestion/ ./ingestion/
COPY --chown=app:app embeddings/ ./embeddings/
COPY --chown=app:app scripts/ ./scripts/

USER app

EXPOSE 8000

# Container-level healthcheck — ECS/K8s layer will add its own too
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

# tini reaps zombies so uvicorn's workers shut down cleanly on SIGTERM
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT} --workers ${UVICORN_WORKERS:-2} --proxy-headers --forwarded-allow-ips='*'"]
