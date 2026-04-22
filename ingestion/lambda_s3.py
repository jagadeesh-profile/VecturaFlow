"""
VecturaFlow — FileIngestionAgent Lambda handler.

Triggered by S3 PUT events. Deduplicates, validates, and enqueues files
for downstream parsing by :mod:`ingestion.lambda_parser`.

Production hardening:
  - Lazy, cached boto3 clients so the module imports cleanly without AWS env
    vars (critical for unit tests using moto and for cold-start safety).
  - Deterministic ``doc_id = SHA256(bucket/key)`` enforces idempotent re-upload.
  - DynamoDB conditional writes guard against race conditions on concurrent
    uploads of the same key.
  - Per-record try/except — one poison message never poisons the whole batch.
  - All CloudWatch metrics are fire-and-forget — monitoring cannot break data.
"""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import os
import time
from typing import Any

import boto3
from botocore.exceptions import ClientError

from ingestion.logging_util import get_logger

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

SUPPORTED_TYPES: frozenset[str] = frozenset({"pdf", "docx", "csv", "txt", "json"})
_SEND_MAX_ATTEMPTS = 3
_BASE_BACKOFF = 1.0  # seconds

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Lazy clients — created on first use, reused across warm invocations.
# This pattern lets the module import cleanly without AWS env vars, which is
# critical for unit tests and avoids crashing Lambda cold-start if Secrets
# Manager is slow.
# ─────────────────────────────────────────────────────────────────────────────

def _region() -> str:
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")


@lru_cache(maxsize=1)
def _dynamo() -> Any:
    return boto3.resource("dynamodb", region_name=_region())


@lru_cache(maxsize=1)
def _sqs() -> Any:
    return boto3.client("sqs", region_name=_region())


@lru_cache(maxsize=1)
def _cloudwatch() -> Any:
    return boto3.client("cloudwatch", region_name=_region())


def _registry_table() -> Any:
    table_name = os.environ.get("REGISTRY_TABLE", "vecturaflow-registry")
    return _dynamo().Table(table_name)


def _ingestion_queue_url() -> str:
    return os.environ["INGESTION_QUEUE_URL"]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_doc_id(bucket: str, key: str) -> str:
    """
    Deterministic doc_id — SHA256(bucket/key).
    Intentionally idempotent: uploading the same file twice is a no-op.
    To force re-ingestion, caller must delete the DynamoDB record first.
    """
    return hashlib.sha256(f"{bucket}/{key}".encode()).hexdigest()


def _get_file_type(key: str) -> str | None:
    """Extract lowercase extension. Returns None if key has no extension."""
    parts = key.rsplit(".", 1)
    if len(parts) < 2:
        return None
    return parts[1].lower()


def _emit_metric(name: str, value: float = 1.0, unit: str = "Count") -> None:
    """Fire-and-forget CloudWatch custom metric. Never raises."""
    try:
        _cloudwatch().put_metric_data(
            Namespace="VecturaFlow/Ingestion",
            MetricData=[{"MetricName": name, "Value": value, "Unit": unit}],
        )
    except Exception as exc:
        logger.warning("cloudwatch.emit_failed", metric=name, error=str(exc))


def _is_already_processed(doc_id: str) -> bool:
    """
    Returns True only if status == 'completed'.
    Files stuck in 'ingestion_started' or 'parse_failed' are eligible for retry.
    Fails open (False) on DynamoDB errors so we attempt processing rather
    than silently skip a real document.
    """
    try:
        response = _registry_table().get_item(
            Key={"doc_id": doc_id},
            ProjectionExpression="#s",
            ExpressionAttributeNames={"#s": "status"},
        )
        item = response.get("Item")
        return item is not None and item.get("status") == "completed"
    except ClientError as exc:
        logger.error("dynamo.get_item_failed", doc_id=doc_id, error=str(exc))
        return False


def _write_registry_record(doc_id: str, source: str, file_type: str) -> bool:
    """
    Write an ``ingestion_started`` record, guarded by a conditional expression
    that refuses to overwrite a record already marked ``completed``.
    Returns True on success, False if the record exists and is completed.
    """
    now = datetime.now(timezone.utc).isoformat()
    try:
        _registry_table().put_item(
            Item={
                "doc_id": doc_id,
                "source": source,
                "file_type": file_type,
                "status": "ingestion_started",
                "ingested_at": now,
                "updated_at": now,
            },
            ConditionExpression="attribute_not_exists(doc_id) OR #s <> :completed",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":completed": "completed"},
        )
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            logger.info("dynamo.record_already_completed", doc_id=doc_id)
            return False
        logger.error("dynamo.write_failed", doc_id=doc_id, error=str(exc))
        return False


def _enqueue_for_parsing(payload: dict[str, Any]) -> None:
    """Push a file reference onto the ingestion SQS queue, with retry+backoff."""
    last_exc: Exception | None = None
    for attempt in range(1, _SEND_MAX_ATTEMPTS + 1):
        try:
            _sqs().send_message(
                QueueUrl=_ingestion_queue_url(),
                MessageBody=json.dumps(payload),
                MessageAttributes={
                    "file_type": {
                        "DataType": "String",
                        "StringValue": payload["file_type"],
                    }
                },
            )
            return
        except ClientError as exc:
            last_exc = exc
            wait = _BASE_BACKOFF * (2 ** (attempt - 1))
            logger.warning(
                "sqs.send_failed",
                attempt=attempt,
                wait_seconds=wait,
                error=str(exc),
            )
            time.sleep(wait)

    _emit_metric("SQSEnqueueFailed")
    raise RuntimeError(
        f"Failed to enqueue doc after {_SEND_MAX_ATTEMPTS} attempts: {last_exc}"
    ) from last_exc


# ─────────────────────────────────────────────────────────────────────────────
# Lambda handler
# ─────────────────────────────────────────────────────────────────────────────

def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    S3 event → parse trigger.

    Processes every record in the batch. A single bad record never fails the
    whole event; results are aggregated and returned so the caller (S3
    notification invoker) has a structured summary for observability.
    """
    records = event.get("Records", [])

    # ── Unwrap SQS-wrapped S3 events ─────────────────────────────────────────
    # Production wiring is S3 → SQS → Lambda, so each SQS record's `body`
    # contains the real S3 event JSON. Flatten to S3 records so the downstream
    # loop stays simple. Direct S3 invocations (unit tests) are also supported.
    if records and "body" in records[0] and "s3" not in records[0]:
        flattened: list[dict[str, Any]] = []
        for sqs_rec in records:
            try:
                inner = json.loads(sqs_rec["body"])
            except (ValueError, KeyError) as exc:
                logger.error("ingestion.sqs_body_parse_failed", error=str(exc))
                continue
            flattened.extend(inner.get("Records", []))
        records = flattened

    results: dict[str, int] = {"processed": 0, "skipped": 0, "failed": 0}

    for record in records:
        bucket = record["s3"]["bucket"]["name"]
        # URL-decode key (S3 encodes spaces as '+')
        key = record["s3"]["object"]["key"].replace("+", " ")

        log_ctx: dict[str, Any] = {"bucket": bucket, "key": key}

        try:
            file_type = _get_file_type(key)

            # ── Validate file type ────────────────────────────────────────────
            if file_type not in SUPPORTED_TYPES:
                logger.warning("ingestion.unsupported_type", file_type=file_type, **log_ctx)
                _emit_metric("UnsupportedFileType")
                results["skipped"] += 1
                continue

            doc_id = _make_doc_id(bucket, key)
            log_ctx["doc_id"] = doc_id

            # ── Deduplication check ───────────────────────────────────────────
            if _is_already_processed(doc_id):
                logger.info("ingestion.duplicate_skipped", **log_ctx)
                _emit_metric("DuplicateSkipped")
                results["skipped"] += 1
                continue

            # ── Write registry record ─────────────────────────────────────────
            written = _write_registry_record(doc_id, key, file_type)
            if not written:
                results["skipped"] += 1
                continue

            # ── Enqueue for parsing ───────────────────────────────────────────
            _enqueue_for_parsing({
                "doc_id": doc_id,
                "bucket": bucket,
                "key": key,
                "file_type": file_type,
            })

            logger.info("ingestion.queued", file_type=file_type, **log_ctx)
            _emit_metric("FilesQueued")
            results["processed"] += 1

        except Exception as exc:
            logger.error(
                "ingestion.record_failed",
                error=str(exc),
                exc_info=True,
                **log_ctx,
            )
            _emit_metric("IngestionFailed")
            results["failed"] += 1
            # Continue processing remaining records — don't fail the whole batch

    logger.info("ingestion.batch_complete", **results)
    return results


# Alias for AWS Lambda handler naming convention
lambda_handler = handler
