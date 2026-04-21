"""
VecturaFlow — FastAPI dependency injection.
All shared resources (DB clients, auth) live here.
Inject via Depends() — never instantiate clients inside route handlers.
"""
from __future__ import annotations

from functools import lru_cache

import boto3
from fastapi import Depends, Header, HTTPException, status

from api.config import Settings, get_settings
from api.logger import logger
from api.schemas import ErrorDetail

# ─────────────────────────────────────────────────────────────────────────────
# DynamoDB clients (module-level singletons — reused across Lambda invocations)
# ─────────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _get_dynamodb_resource():
    return boto3.resource("dynamodb", region_name=get_settings().aws_default_region)


def get_registry_table():
    return _get_dynamodb_resource().Table(get_settings().registry_table)


def get_keys_table():
    return _get_dynamodb_resource().Table(get_settings().keys_table)


# ─────────────────────────────────────────────────────────────────────────────
# API key authentication
# ─────────────────────────────────────────────────────────────────────────────

async def verify_api_key(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> dict:
    """
    Validates Bearer token against DynamoDB keys table.
    Returns the key record (includes key_id, created_at, owner).
    Raises HTTP 401 on any failure — never leaks reason to caller.
    """
    if not authorization:
        logger.warning("auth.missing_header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ErrorDetail(
                message="Missing Authorization header",
                type="authentication_error",
                code="missing_key",
            ).model_dump(),
        )

    if not authorization.startswith("Bearer "):
        logger.warning("auth.malformed_header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ErrorDetail(
                message="Authorization header must use Bearer scheme",
                type="authentication_error",
                code="invalid_scheme",
            ).model_dump(),
        )

    api_key = authorization.removeprefix("Bearer ").strip()
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ErrorDetail(
                message="API key is empty",
                type="authentication_error",
                code="empty_key",
            ).model_dump(),
        )

    # Skip DynamoDB auth in development if key == "dev"
    if settings.api_env == "development" and api_key == "dev":
        logger.debug("auth.dev_bypass")
        return {"api_key": "dev", "owner": "local", "key_id": "dev-key"}

    try:
        keys_table = get_keys_table()
        response = keys_table.get_item(Key={"api_key": api_key})
        item = response.get("Item")
    except Exception as exc:
        logger.error("auth.dynamo_error", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=ErrorDetail(
                message="Authentication service unavailable",
                type="service_error",
                code="auth_unavailable",
            ).model_dump(),
        ) from exc

    if not item:
        logger.warning("auth.invalid_key", key_prefix=api_key[:8])
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ErrorDetail(
                message="Invalid API key",
                type="authentication_error",
                code="invalid_key",
            ).model_dump(),
        )

    if item.get("revoked"):
        logger.warning("auth.revoked_key", key_id=item.get("key_id"))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ErrorDetail(
                message="API key has been revoked",
                type="authentication_error",
                code="revoked_key",
            ).model_dump(),
        )

    return item
