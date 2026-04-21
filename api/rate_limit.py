"""
VecturaFlow — in-process token-bucket rate limiter.

Usage:
    from api.rate_limit import require_rate_limit

    @app.post("/v1/chat/completions", dependencies=[Depends(require_rate_limit)])
    async def chat_completions(...):
        ...

Design notes:
- Per-API-key bucket, keyed on the authenticated key_id (falls back to client IP).
- Lock-free under CPython thanks to the GIL; under uvicorn workers each process
  has its own buckets — exact request accounting requires ElastiCache-backed
  buckets, which is a follow-up.
- Default: 60 requests / 60 seconds / key. Override via settings.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
import time
from typing import Any

from fastapi import Depends, HTTPException, Request, status

from api.config import settings
from api.dependencies import verify_api_key
from api.logger import logger
from api.schemas import ErrorDetail


@dataclass
class _Bucket:
    tokens: float
    updated: float = field(default_factory=time.monotonic)


class TokenBucketLimiter:
    """Refills `capacity` tokens over `period_seconds`."""

    def __init__(self, capacity: int, period_seconds: float) -> None:
        self.capacity = float(capacity)
        self.rate = self.capacity / period_seconds
        self._buckets: dict[str, _Bucket] = {}
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            b = self._buckets.get(key)
            if b is None:
                self._buckets[key] = _Bucket(tokens=self.capacity - 1, updated=now)
                return True
            elapsed = now - b.updated
            b.tokens = min(self.capacity, b.tokens + elapsed * self.rate)
            b.updated = now
            if b.tokens < 1:
                return False
            b.tokens -= 1
            return True


_limiter = TokenBucketLimiter(
    capacity=getattr(settings, "rate_limit_per_minute", 60),
    period_seconds=60,
)


def require_rate_limit(
    request: Request,
    key_info: dict[str, Any] = Depends(verify_api_key),
) -> dict[str, Any]:
    """
    FastAPI dependency that enforces the rate limit and still returns the
    authenticated key info, so route handlers don't need to depend on
    both ``verify_api_key`` and this.
    """
    bucket_key = key_info.get("key_id") or (
        request.client.host if request.client else "anonymous"
    )
    if not _limiter.allow(bucket_key):
        logger.warning("rate_limit.exceeded", key=bucket_key)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=ErrorDetail(
                message="Rate limit exceeded. Please slow down and retry.",
                type="rate_limit_error",
                code="too_many_requests",
            ).model_dump(),
            headers={"Retry-After": "30"},
        )
    return key_info
