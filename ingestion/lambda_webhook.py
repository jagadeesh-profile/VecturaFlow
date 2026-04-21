"""
VecturaFlow — WebhookIngestionAgent Lambda handler.
Triggered via API Gateway POST /ingest/webhook.

Accepts any JSON payload (single object or array), converts it to text,
and pushes it directly to the SQS embedding queue — bypassing the
parser/chunker pipeline because webhook data is already structured text.

Design principles:
- Module-level clients reused across Lambda warm starts
- uuid4() for doc_id — avoids SHA256(source+timestamp) collision risk
  on high-frequency webhooks (same source + same second = same hash)
- One SQS message per item (supports batch payloads)
- Registers each item in DynamoDB with status="ingestion_started"
- Full API Gateway proxy response format: {statusCode, headers, body}
"""
from __future__ import annotations

import contextlib
from datetime import datetime, timezone
from functools import lru_cache
import json
import os
from typing import Any
from uuid import uuid4

import boto3
from botocore.exceptions import ClientError

from ingestion.logging_util import get_logger

_MAX_TEXT_LENGTH = 4000   # chars — keeps chunks within embedding token limit

logger = get_logger(__name__)


def _region() -> str:
    return os.environ.get("AWS_DEFAULT_REGION", "us-east-1")


@lru_cache(maxsize=1)
def _dynamo() -> Any:
    return boto3.resource("dynamodb", region_name=_region())


@lru_cache(maxsize=1)
def _sqs() -> Any:
    return boto3.client("sqs", region_name=_region())


@lru_cache(maxsize=1)
def _cw() -> Any:
    return boto3.client("cloudwatch", region_name=_region())


def _registry() -> Any:
    return _dynamo().Table(os.environ.get("REGISTRY_TABLE", "vecturaflow-registry"))


def _embedding_queue_url() -> str:
    return os.environ["EMBEDDING_QUEUE_URL"]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _item_to_text(item: dict[str, Any]) -> str:
    """
    Convert a JSON object to a human-readable key: value string.
    Includes numeric and boolean values (not just strings) for full context.
    Truncates to _MAX_TEXT_LENGTH to stay within embedding limits.
    """
    parts = []
    for k, v in item.items():
        if isinstance(v, (str, int, float, bool)):
            parts.append(f"{k}: {v}")
        elif isinstance(v, (list, dict)):
            # Flatten nested structures as JSON snippet
            parts.append(f"{k}: {json.dumps(v)}")
    text = " | ".join(parts)
    return text[:_MAX_TEXT_LENGTH]


def _register_doc(doc_id: str, source: str) -> None:
    """Write ingestion_started record to DynamoDB. Non-fatal on failure."""
    now = datetime.now(timezone.utc).isoformat()
    with contextlib.suppress(ClientError, Exception):
        _registry().put_item(
            Item={
                "doc_id": doc_id,
                "source": source,
                "file_type": "webhook",
                "status": "ingestion_started",
                "ingested_at": now,
                "updated_at": now,
            },
            ConditionExpression="attribute_not_exists(doc_id)",
        )


def _enqueue_chunk(doc_id: str, text: str, source: str) -> None:
    """
    Push a single chunk message to the SQS embedding queue.
    Webhook items go directly to embedding — no parser/chunker needed.
    Raises on SQS failure so the caller can return a 500.
    """
    message = {
        "chunk_id": f"{doc_id}_chunk_0",
        "doc_id": doc_id,
        "text": text,
        "source": source,
        "chunk_index": 0,
        "total_chunks": 1,
        "file_type": "webhook",
    }
    _sqs().send_message(
        QueueUrl=_embedding_queue_url(),
        MessageBody=json.dumps(message),
    )


def _emit_metric(name: str, value: float = 1.0) -> None:
    """Fire-and-forget CloudWatch metric. Never raises."""
    with contextlib.suppress(Exception):
        _cw().put_metric_data(
            Namespace="VecturaFlow/Webhook",
            MetricData=[{"MetricName": name, "Value": value, "Unit": "Count"}],
        )


def _response(status_code: int, body: dict) -> dict:
    """Build an API Gateway proxy response."""
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Lambda handler
# ─────────────────────────────────────────────────────────────────────────────

def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    API Gateway → Lambda webhook handler.

    Accepts POST /ingest/webhook with a JSON body that is either:
      - A single JSON object: {"field": "value", ...}
      - An array of JSON objects: [{"field": "value"}, ...]

    Optional request headers:
      X-Source-Name  — human-readable source label (default: "webhook")
      X-Webhook-ID   — idempotency key (ignored today, reserved for future)

    Returns:
      200 { status: "queued", doc_ids: [...], count: N }
      400 { error: "Empty payload" }
      422 { error: "Invalid JSON body" }
      500 { error: "Failed to queue N item(s)" }
    """
    headers = event.get("headers") or {}
    source = headers.get("X-Source-Name") or headers.get("x-source-name") or "webhook"

    # ── Parse body ────────────────────────────────────────────────────────────
    raw_body = event.get("body") or ""
    if not raw_body.strip():
        _emit_metric("WebhookEmptyBody")
        return _response(400, {"error": "Empty payload — request body is required"})

    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        _emit_metric("WebhookInvalidJSON")
        return _response(422, {"error": f"Invalid JSON body: {exc}"})

    # ── Validate ──────────────────────────────────────────────────────────────
    if body is None or body == {} or body == []:
        _emit_metric("WebhookEmptyBody")
        return _response(400, {"error": "Empty payload — body must contain at least one field"})

    # Normalise to list
    items: list[dict] = body if isinstance(body, list) else [body]

    # Filter out non-dict items in a batch
    items = [item for item in items if isinstance(item, dict) and item]
    if not items:
        return _response(400, {"error": "No valid JSON objects found in payload"})

    # ── Process each item ─────────────────────────────────────────────────────
    doc_ids: list[str] = []
    failed = 0

    for item in items:
        doc_id = str(uuid4()).replace("-", "")
        text = _item_to_text(item)

        if not text.strip():
            logger.warning("webhook.empty_text", doc_id=doc_id)
            failed += 1
            continue

        # Register in DynamoDB (best-effort — non-fatal)
        _register_doc(doc_id, source)

        # Push to SQS embedding queue
        try:
            _enqueue_chunk(doc_id, text, source)
            doc_ids.append(doc_id)
            logger.info(
                "webhook.queued", doc_id=doc_id, source=source, text_len=len(text),
            )
        except Exception as exc:
            logger.error(
                "webhook.sqs_failed", doc_id=doc_id, error=str(exc), exc_info=True,
            )
            failed += 1

    _emit_metric("WebhookItemsQueued", float(len(doc_ids)))

    if failed > 0 and not doc_ids:
        _emit_metric("WebhookFailed")
        return _response(500, {
            "error": f"Failed to queue {failed} item(s)",
            "doc_ids": [],
        })

    return _response(200, {
        "status": "queued",
        "doc_ids": doc_ids,
        "count": len(doc_ids),
        **({"warnings": f"{failed} item(s) failed to queue"} if failed else {}),
    })


# AWS Lambda handler-naming convention alias
lambda_handler = handler
