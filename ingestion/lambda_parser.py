"""
VecturaFlow — ParserAgent Lambda handler.
Triggered by SQS ingestion queue. Downloads file from S3, parses it,
chunks it, and publishes chunks to the embedding queue.

Flow:
  SQS (ingestion queue)
    → lambda_parser.handler()
      → download S3
      → parser.parse_file()
      → chunker.chunk_blocks()
      → chunker.publish_chunks() → SQS (embedding queue)
      → DynamoDB status update
"""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
import json
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

from ingestion.chunker import (
    chunk_blocks,
    publish_chunks,
    update_registry_chunked,
    update_registry_empty,
)
from ingestion.logging_util import get_logger
from ingestion.parser import parse_file

logger = get_logger(__name__)


def _region() -> str:
    return os.environ.get("AWS_DEFAULT_REGION", "us-east-1")


@lru_cache(maxsize=1)
def _s3_client() -> Any:
    return boto3.client("s3", region_name=_region())


@lru_cache(maxsize=1)
def _dynamo_resource() -> Any:
    return boto3.resource("dynamodb", region_name=_region())


def _embedding_queue_url() -> str:
    return os.environ.get("EMBEDDING_QUEUE_URL", "")


def _registry_table_name() -> str:
    return os.environ.get("REGISTRY_TABLE", "vecturaflow-registry")


def _chunk_size() -> int:
    return int(os.environ.get("CHUNK_SIZE", "512"))


def _chunk_overlap() -> int:
    return int(os.environ.get("CHUNK_OVERLAP", "50"))


def _download_from_s3(bucket: str, key: str, doc_id: str) -> bytes:
    """Download file bytes from S3. Raises on failure."""
    try:
        obj = _s3_client().get_object(Bucket=bucket, Key=key)
        file_bytes = obj["Body"].read()
        logger.info(
            "s3.downloaded",
            doc_id=doc_id, bucket=bucket, key=key, size=len(file_bytes),
        )
        return file_bytes
    except ClientError as exc:
        logger.error(
            "s3.download_failed",
            doc_id=doc_id, bucket=bucket, key=key, error=str(exc),
        )
        raise


def _update_registry_failed(doc_id: str, error: str) -> None:
    """Mark document as parse_failed in DynamoDB."""
    try:
        table = _dynamo_resource().Table(_registry_table_name())
        table.update_item(
            Key={"doc_id": doc_id},
            UpdateExpression="SET #s = :s, parse_error = :e, updated_at = :u",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": "parse_failed",
                ":e": str(error)[:500],
                ":u": datetime.now(timezone.utc).isoformat(),
            },
        )
    except ClientError as exc:
        logger.error("dynamo.update_failed", doc_id=doc_id, error=str(exc))


def _process_record(message_body: dict[str, Any]) -> dict[str, str]:
    """
    Process one SQS message.
    Returns {"doc_id": ..., "status": "chunked" | "empty" | "failed"}.
    """
    doc_id = message_body.get("doc_id", "unknown")
    bucket = message_body.get("bucket")
    key = message_body.get("key")
    file_type = message_body.get("file_type")

    if not all([bucket, key, file_type]):
        logger.error("parser.invalid_message", doc_id=doc_id, body=message_body)
        return {"doc_id": doc_id, "status": "failed"}

    logger.info("parser.processing", doc_id=doc_id, file_type=file_type, key=key)

    # ── Download from S3 ──────────────────────────────────────────────────
    try:
        file_bytes = _download_from_s3(bucket, key, doc_id)
    except Exception as exc:
        _update_registry_failed(doc_id, str(exc))
        return {"doc_id": doc_id, "status": "failed"}

    # ── Parse ─────────────────────────────────────────────────────────────
    try:
        blocks = parse_file(
            file_bytes=file_bytes,
            file_type=file_type,
            doc_id=doc_id,
            source=key,
        )
    except ValueError as exc:
        logger.error("parser.unsupported_type", doc_id=doc_id, error=str(exc))
        _update_registry_failed(doc_id, str(exc))
        return {"doc_id": doc_id, "status": "failed"}
    except Exception as exc:
        logger.error(
            "parser.parse_failed", doc_id=doc_id, error=str(exc), exc_info=True,
        )
        _update_registry_failed(doc_id, str(exc))
        return {"doc_id": doc_id, "status": "failed"}

    table = _registry_table_name()

    # ── Empty document ────────────────────────────────────────────────────
    if not blocks:
        logger.warning("parser.empty_document", doc_id=doc_id, key=key)
        update_registry_empty(doc_id, table)
        return {"doc_id": doc_id, "status": "empty"}

    # ── Chunk ─────────────────────────────────────────────────────────────
    chunks = chunk_blocks(
        blocks, chunk_size=_chunk_size(), chunk_overlap=_chunk_overlap(),
    )
    if not chunks:
        update_registry_empty(doc_id, table)
        return {"doc_id": doc_id, "status": "empty"}

    # ── Publish to embedding queue ────────────────────────────────────────
    published = publish_chunks(chunks, _embedding_queue_url())

    # ── Update registry ───────────────────────────────────────────────────
    update_registry_chunked(doc_id, published, table)

    logger.info(
        "parser.complete",
        doc_id=doc_id, blocks=len(blocks), chunks=len(chunks), published=published,
    )
    return {"doc_id": doc_id, "status": "chunked"}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    SQS batch handler with partial-batch-response semantics.
    Up to 10 messages per invocation. Failed records are returned in
    ``batchItemFailures`` so SQS only retries the failures.
    """
    records = event.get("Records", [])
    results: dict[str, int] = {"chunked": 0, "empty": 0, "failed": 0}
    batch_item_failures: list[dict] = []

    for record in records:
        message_id = record.get("messageId", "unknown")
        try:
            body = json.loads(record["body"])
            result = _process_record(body)
            results[result["status"]] += 1
        except Exception as exc:
            logger.error(
                "parser.record_exception",
                messageId=message_id, error=str(exc), exc_info=True,
            )
            results["failed"] += 1
            batch_item_failures.append({"itemIdentifier": message_id})

    logger.info("parser.batch_complete", **results)
    return {"batchItemFailures": batch_item_failures}


# AWS Lambda handler-naming convention alias
lambda_handler = handler
