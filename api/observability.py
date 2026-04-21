"""
VecturaFlow — observability primitives.

Exposes:
- Prometheus metrics (request count, latency histogram, in-flight gauge,
  RAG pipeline counters)
- A FastAPI middleware that records every HTTP request
- A `/metrics` route handler that returns the Prometheus text format
- A `/readyz` readiness probe that pings critical dependencies

Designed to be a pure dependency of `api/main.py` — no FastAPI app instance is
created here.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
import time

from fastapi import Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# ─────────────────────────────────────────────────────────────────────────────
# Registry — explicit instead of default global, so tests can build a fresh one
# ─────────────────────────────────────────────────────────────────────────────

REGISTRY = CollectorRegistry(auto_describe=True)

HTTP_REQUESTS = Counter(
    "vecturaflow_http_requests_total",
    "Total HTTP requests processed",
    labelnames=("method", "path", "status"),
    registry=REGISTRY,
)

HTTP_LATENCY = Histogram(
    "vecturaflow_http_request_duration_seconds",
    "HTTP request latency in seconds",
    labelnames=("method", "path"),
    buckets=(0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
    registry=REGISTRY,
)

HTTP_IN_FLIGHT = Gauge(
    "vecturaflow_http_in_flight_requests",
    "Number of HTTP requests currently being handled",
    registry=REGISTRY,
)

RAG_QUERIES = Counter(
    "vecturaflow_rag_queries_total",
    "Total RAG queries by outcome",
    labelnames=("confidence",),
    registry=REGISTRY,
)

RAG_LATENCY = Histogram(
    "vecturaflow_rag_pipeline_duration_seconds",
    "End-to-end RAG pipeline latency in seconds",
    buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
    registry=REGISTRY,
)

RETRIEVER_CACHE = Counter(
    "vecturaflow_retriever_cache_total",
    "Retriever cache outcomes",
    labelnames=("outcome",),  # hit | miss | error
    registry=REGISTRY,
)


# ─────────────────────────────────────────────────────────────────────────────
# Middleware
# ─────────────────────────────────────────────────────────────────────────────

# Paths whose latency we never want to record (would dominate the histogram).
_EXCLUDED_PATHS = frozenset({"/metrics", "/health", "/healthz", "/readyz"})


async def metrics_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Record per-request metrics. Safe even if the handler raises."""
    if request.url.path in _EXCLUDED_PATHS:
        return await call_next(request)

    method = request.method
    path = _route_template(request) or request.url.path
    HTTP_IN_FLIGHT.inc()
    start = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        elapsed = time.perf_counter() - start
        HTTP_LATENCY.labels(method=method, path=path).observe(elapsed)
        HTTP_REQUESTS.labels(method=method, path=path, status=str(status_code)).inc()
        HTTP_IN_FLIGHT.dec()


def _route_template(request: Request) -> str | None:
    """Return the Starlette route template (e.g. ``/v1/items/{id}``) if matched."""
    route = request.scope.get("route")
    return getattr(route, "path", None)


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

def metrics_response() -> Response:
    """Render the Prometheus text exposition format."""
    return Response(content=generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
