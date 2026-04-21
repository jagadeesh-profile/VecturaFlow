"""
VecturaFlow — FastAPI application entrypoint.
OpenAI-compatible RAG API deployed on ECS Fargate behind API Gateway.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
import time
import uuid

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import structlog

from api.agent import run_rag  # noqa: E402 — after third-party imports
from api.config import settings
from api.dependencies import verify_api_key
from api.logger import logger
from api.observability import (
    RAG_LATENCY,
    RAG_QUERIES,
    metrics_middleware,
    metrics_response,
)
from api.rate_limit import require_rate_limit
from api.schemas import (
    ChatRequest,
    ChatResponse,
    Choice,
    ErrorDetail,
    ErrorResponse,
    ResponseMessage,
    SourceCitation,
    UsageMetadata,
)

# ─────────────────────────────────────────────────────────────────────────────
# Lifespan — startup / shutdown hooks
# ─────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "vecturaflow.startup",
        env=settings.api_env,
        version=settings.api_version,
        pinecone_index=settings.pinecone_index,
        generation_model=settings.generation_model,
    )
    yield
    logger.info("vecturaflow.shutdown")


# ─────────────────────────────────────────────────────────────────────────────
# App factory
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.api_title,
    version=settings.api_version,
    description="Autonomous agentic RAG platform — OpenAI-compatible API",
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
    lifespan=lifespan,
)

# ─────────────────────────────────────────────────────────────────────────────
# Middleware
# ─────────────────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if not settings.is_production else [],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


app.middleware("http")(metrics_middleware)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    """Log every request with method, path, status, and latency."""
    request_id = str(uuid.uuid4())
    structlog.contextvars.bind_contextvars(request_id=request_id)

    start = time.perf_counter()
    response = await call_next(request)
    latency_ms = int((time.perf_counter() - start) * 1000)

    logger.info(
        "http.request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        latency_ms=latency_ms,
    )
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Latency-MS"] = str(latency_ms)

    structlog.contextvars.clear_contextvars()
    return response


# ─────────────────────────────────────────────────────────────────────────────
# Global exception handlers
# ─────────────────────────────────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("unhandled_exception", path=request.url.path, error=str(exc), exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=ErrorResponse(
            error=ErrorDetail(
                message="An internal error occurred",
                type="internal_error",
                code="500",
            )
        ).model_dump(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["ops"], summary="Liveness probe")
async def health():
    """ECS liveness probe — returns 200 as long as the process is running."""
    return {
        "status": "ok",
        "version": settings.api_version,
        "env": settings.api_env,
    }


@app.get("/healthz", tags=["ops"], summary="Liveness probe (k8s alias)")
async def healthz():
    return {"status": "ok"}


@app.get("/readyz", tags=["ops"], summary="Readiness probe")
async def readyz():
    """
    Readiness probe. Returns 200 only when the service has all the config
    it needs to serve requests. This is a cheap sync check — no external
    network calls are made (those are owned by the RAG pipeline's own
    retries).
    """
    required = {
        "openai_api_key": bool(settings.openai_api_key),
        "pinecone_api_key": bool(settings.pinecone_api_key),
        "pinecone_index": bool(settings.pinecone_index),
        "registry_table": bool(settings.registry_table),
    }
    ready = all(required.values())
    status_code = status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(
        status_code=status_code,
        content={"ready": ready, "checks": required},
    )


@app.get("/metrics", tags=["ops"], summary="Prometheus metrics", include_in_schema=False)
async def metrics():
    return metrics_response()


@app.get("/v1/models", tags=["models"], summary="List available models")
async def list_models(_: dict = Depends(verify_api_key)):
    """OpenAI-compatible models endpoint."""
    return {
        "object": "list",
        "data": [
            {
                "id": "vecturaflow",
                "object": "model",
                "created": 1_700_000_000,
                "owned_by": "vecturaflow",
            }
        ],
    }


@app.post(
    "/v1/chat/completions",
    response_model=ChatResponse,
    tags=["chat"],
    summary="RAG chat completion — OpenAI-compatible",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or missing API key"},
        422: {"model": ErrorResponse, "description": "Malformed request body"},
        503: {"model": ErrorResponse, "description": "RAG pipeline unavailable"},
    },
)
async def chat_completions(
    request: ChatRequest,
    key_info: dict = Depends(require_rate_limit),
):
    """
    Main RAG endpoint. Accepts an OpenAI-compatible messages array,
    retrieves relevant context from Pinecone via LangGraph RAGAgent,
    generates a grounded answer via GPT-4o mini, and returns a structured
    response with source citations and confidence level.
    """
    start = time.perf_counter()

    # Extract the last user message as the query
    query = next(
        (m.content for m in reversed(request.messages) if m.role.value == "user"),
        None,
    )
    if not query:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ErrorDetail(
                message="No user message found in messages array",
                type="invalid_request_error",
                code="missing_user_message",
            ).model_dump(),
        )

    logger.info(
        "rag.query_received",
        key_id=key_info.get("key_id"),
        query_length=len(query),
        has_filters=request.filters is not None,
    )

    # ── RAGAgent ──────────────────────────────────────────────────────────────
    try:
        with RAG_LATENCY.time():
            result = run_rag(query=query, filters=request.filters)
    except Exception as exc:
        RAG_QUERIES.labels(confidence="error").inc()
        logger.error("rag.pipeline_error", error=str(exc), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=ErrorDetail(
                message="RAG pipeline is temporarily unavailable. Please retry.",
                type="service_unavailable",
                code="503",
            ).model_dump(),
        ) from exc

    RAG_QUERIES.labels(confidence=result["confidence"]).inc()
    latency_ms = int((time.perf_counter() - start) * 1000)

    logger.info(
        "rag.query_complete",
        key_id=key_info.get("key_id"),
        confidence=result["confidence"],
        source_count=len(result["sources"]),
        latency_ms=latency_ms,
    )

    return ChatResponse(
        choices=[
            Choice(
                message=ResponseMessage(content=result["answer"]),
                finish_reason="stop",
            )
        ],
        usage=UsageMetadata(
            sources=[SourceCitation(**s) for s in result["sources"]],
            confidence=result["confidence"],
            latency_ms=latency_ms,
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Dev entrypoint
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.api_debug,
        log_level="debug" if settings.api_debug else "info",
    )
