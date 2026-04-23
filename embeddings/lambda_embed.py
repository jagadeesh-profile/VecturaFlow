"""
VecturaFlow — EmbeddingAgent Lambda.

Triggered by SQS embedding queue. Consumes Chunk messages from
the parser/chunker pipeline, generates vector embeddings via OpenAI,
upserts them into Pinecone, and updates the DynamoDB registry.

Design principles:
- Module-level clients reused across Lambda warm starts (lower latency + cost)
- Batch embedding: all chunks in ONE OpenAI API call per invocation
- Batch upsert: all vectors in ONE Pinecone call per invocation
- Exponential backoff on OpenAI rate limits (max 5 attempts)
- Pinecone failure: retry 3x, then write to S3 failed-chunks/
- Per-record error handling: never fail the whole SQS batch
"""
from __future__ import annotations

import contextlib
from datetime import datetime, timezone
from functools import lru_cache
import importlib
import json
import os
import time
from typing import Any

import boto3
from openai import OpenAI, RateLimitError

from ingestion.logging_util import get_logger

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Lazy clients — created on first use so the module imports cleanly in tests
# and Lambda cold-starts without blocking on secret retrieval.
# ─────────────────────────────────────────────────────────────────────────────

def _region() -> str:
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")


@lru_cache(maxsize=1)
def _openai_client() -> OpenAI:
    return OpenAI(api_key=_secret_value("OPENAI_API_KEY"))


@lru_cache(maxsize=1)
def _pinecone_index() -> Any:
    pinecone = importlib.import_module("pinecone")
    pc = pinecone.Pinecone(api_key=_secret_value("PINECONE_API_KEY"))
    return pc.Index(os.environ["PINECONE_INDEX"])


@lru_cache(maxsize=1)
def _dynamo() -> Any:
    return boto3.resource("dynamodb", region_name=_region())


@lru_cache(maxsize=1)
def _secrets() -> Any:
    return boto3.client("secretsmanager", region_name=_region())


def _registry() -> Any:
    return _dynamo().Table(os.environ.get("REGISTRY_TABLE", "vecturaflow-registry"))


@lru_cache(maxsize=1)
def _s3() -> Any:
    return boto3.client("s3", region_name=_region())


@lru_cache(maxsize=1)
def _cw() -> Any:
    return boto3.client("cloudwatch", region_name=_region())


def _embedding_model() -> str:
    return os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")


def _ingestion_bucket() -> str:
    return os.environ.get("INGESTION_BUCKET", "")


def _secret_value(name: str) -> str:
    """Read a secret directly from env, or resolve its Secrets Manager ARN."""
    value = os.environ.get(name)
    if value:
        return value

    arn = os.environ.get(f"{name}_ARN")
    if not arn:
        return os.environ[name]

    response = _secrets().get_secret_value(SecretId=arn)
    secret = response.get("SecretString")
    if not secret:
        raise RuntimeError(f"{name} secret has no SecretString")
    return secret

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_OPENAI_MAX_RETRIES = 5
_PINECONE_MAX_RETRIES = 3
_OPENAI_BASE_BACKOFF = 1.0   # seconds
_PINECONE_BASE_BACKOFF = 0.5
_MAX_METADATA_TEXT_LEN = 500


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _embed_with_backoff(texts: list[str]) -> list[list[float]]:
    """
    Call OpenAI embeddings API with exponential backoff on rate-limit errors.
    Returns embedding vectors in the same order as ``texts``.
    """
    for attempt in range(1, _OPENAI_MAX_RETRIES + 1):
        try:
            response = _openai_client().embeddings.create(
                model=_embedding_model(),
                input=texts,
            )
            return [item.embedding for item in response.data]
        except RateLimitError:
            if attempt == _OPENAI_MAX_RETRIES:
                raise
            wait = _OPENAI_BASE_BACKOFF * (2 ** (attempt - 1))
            logger.warning("embed.rate_limited", attempt=attempt, wait_seconds=wait)
            time.sleep(wait)
    return []  # unreachable


def _upsert_with_retry(vectors: list[dict]) -> None:
    """
    Upsert vectors to Pinecone with up to ``_PINECONE_MAX_RETRIES`` attempts.
    On final failure the caller is responsible for fallback (S3).
    """
    index = _pinecone_index()
    for attempt in range(1, _PINECONE_MAX_RETRIES + 1):
        try:
            index.upsert(vectors=vectors)
            return
        except Exception as exc:
            if attempt == _PINECONE_MAX_RETRIES:
                logger.error("pinecone.upsert_failed_final", error=str(exc))
                raise
            wait = _PINECONE_BASE_BACKOFF * (2 ** (attempt - 1))
            logger.warning(
                "pinecone.upsert_retry",
                attempt=attempt, wait_seconds=wait, error=str(exc),
            )
            time.sleep(wait)


def _save_failed_chunks_to_s3(doc_id: str, failed: list[dict]) -> None:
    """Persist failed chunk payloads to S3 ``failed-chunks/`` for replay."""
    bucket = _ingestion_bucket()
    if not bucket:
        return
    key = f"failed-chunks/{doc_id}_{int(time.time())}.json"
    with contextlib.suppress(Exception):
        _s3().put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(failed),
            ContentType="application/json",
        )
        logger.error("embed.saved_failed_chunks", doc_id=doc_id, key=key)


def _mark_registry_embedded(doc_id: str, timestamp: str) -> None:
    """Mark the document as fully embedded in DynamoDB."""
    _registry().update_item(
        Key={"doc_id": doc_id},
        UpdateExpression="SET #st = :s, embedded_at = :ts, updated_at = :ts",
        ExpressionAttributeNames={"#st": "status"},
        ExpressionAttributeValues={
            ":s": "embedded",
            ":ts": timestamp,
        },
    )


def _record_registry_progress(doc_id: str, chunk_ids: set[str], total_chunks: int) -> None:
    """Record unique embedded chunks and mark complete only after all are seen."""
    timestamp = datetime.now(timezone.utc).isoformat()
    response = _registry().update_item(
        Key={"doc_id": doc_id},
        UpdateExpression=(
            "ADD embedded_chunk_ids :chunk_ids "
            "SET total_chunks = :total, updated_at = :ts"
        ),
        ExpressionAttributeValues={
            ":chunk_ids": chunk_ids,
            ":total": total_chunks,
            ":ts": timestamp,
        },
        ReturnValues="UPDATED_NEW",
    )
    if not isinstance(response, dict):
        return

    attrs = response.get("Attributes", {})
    embedded_chunk_ids = attrs.get("embedded_chunk_ids", set())
    embedded_count = len(embedded_chunk_ids) if embedded_chunk_ids else 0
    expected_total = int(attrs.get("total_chunks", total_chunks) or total_chunks)
    if expected_total > 0 and embedded_count >= expected_total:
        _mark_registry_embedded(doc_id, timestamp)


def _emit_metric(latency_ms: float, vector_count: int) -> None:
    """Push embedding latency and throughput metrics to CloudWatch."""
    with contextlib.suppress(Exception):
        _cw().put_metric_data(
            Namespace="VecturaFlow/Embedding",
            MetricData=[
                {
                    "MetricName": "EmbeddingLatencyMs",
                    "Value": latency_ms,
                    "Unit": "Milliseconds",
                },
                {
                    "MetricName": "VectorsUpserted",
                    "Value": vector_count,
                    "Unit": "Count",
                },
            ],
        )


def _build_vector(msg: dict[str, Any], embedding: list[float]) -> dict:
    """Build a Pinecone vector dict from a chunk message and its embedding."""
    metadata: dict[str, Any] = {
        "doc_id": msg["doc_id"],
        "source": msg["source"],
        "text": msg["text"][:_MAX_METADATA_TEXT_LEN],
        "chunk_index": msg["chunk_index"],
        "file_type": msg.get("file_type", "unknown"),
    }
    # Optional positional metadata — only include if present
    for optional_field in ("page", "section"):
        if msg.get(optional_field) is not None:
            metadata[optional_field] = msg[optional_field]

    return {
        "id": msg["chunk_id"],
        "values": embedding,
        "metadata": metadata,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Lambda handler
# ─────────────────────────────────────────────────────────────────────────────

def handler(event: dict, context: Any) -> dict:  # noqa: C901

    """
    SQS-triggered Lambda handler.

    Processes up to 10 chunk messages per invocation:
    1. Parse all SQS records
    2. Embed all texts in one OpenAI batch call
    3. Upsert all vectors to Pinecone in one call
    4. Update DynamoDB status for each unique doc_id
    5. Return batchItemFailures for any records that errored
    """
    records = event.get("Records", [])
    batch_item_failures: list[dict] = []
    start = time.perf_counter()

    # ── Parse all records ─────────────────────────────────────────────────────
    parsed: list[tuple[dict, dict]] = []   # (sqs_record, chunk_message)
    for record in records:
        try:
            msg = json.loads(record["body"])
            parsed.append((record, msg))
        except Exception:
            # Unparseable record — send to DLQ immediately
            batch_item_failures.append({"itemIdentifier": record["messageId"]})
            continue

    if not parsed:
        return {"batchItemFailures": batch_item_failures}

    sqs_records = [p[0] for p in parsed]
    messages = [p[1] for p in parsed]
    texts = [m["text"] for m in messages]

    # ── Embed all texts in one OpenAI call ────────────────────────────────────
    try:
        embeddings = _embed_with_backoff(texts)
    except Exception:
        # If embedding fails for the whole batch, fail all records
        for record in sqs_records:
            batch_item_failures.append({"itemIdentifier": record["messageId"]})
        return {"batchItemFailures": batch_item_failures}

    # ── Build Pinecone vectors ─────────────────────────────────────────────────
    vectors = [
        _build_vector(msg, emb)
        for msg, emb in zip(messages, embeddings, strict=False)
    ]

    # ── Upsert to Pinecone ────────────────────────────────────────────────────
    try:
        _upsert_with_retry(vectors)
    except Exception:
        # Pinecone failed after all retries — save failed chunks to S3
        doc_ids_in_batch = {m["doc_id"] for m in messages}
        for doc_id in doc_ids_in_batch:
            failed_msgs = [m for m in messages if m["doc_id"] == doc_id]
            _save_failed_chunks_to_s3(doc_id, failed_msgs)
        # Mark all records as failed so SQS retries them
        for record in sqs_records:
            batch_item_failures.append({"itemIdentifier": record["messageId"]})
        return {"batchItemFailures": batch_item_failures}

    # ── Update DynamoDB — one progress write per unique doc_id ────────────────
    progress_by_doc: dict[str, dict[str, Any]] = {}
    for msg in messages:
        doc_id = msg["doc_id"]
        progress = progress_by_doc.setdefault(
            doc_id,
            {"chunk_ids": set(), "total_chunks": int(msg.get("total_chunks", 1) or 1)},
        )
        progress["chunk_ids"].add(msg["chunk_id"])
        progress["total_chunks"] = max(
            int(progress["total_chunks"]),
            int(msg.get("total_chunks", 1) or 1),
        )

    for doc_id, progress in progress_by_doc.items():
        with contextlib.suppress(Exception):
            _record_registry_progress(
                doc_id,
                progress["chunk_ids"],
                int(progress["total_chunks"]),
            )

    # ── Emit CloudWatch metrics ───────────────────────────────────────────────
    latency_ms = (time.perf_counter() - start) * 1000
    _emit_metric(latency_ms, len(vectors))

    logger.info(
        "embed.batch_complete",
        vectors=len(vectors),
        latency_ms=int(latency_ms),
        failed=len(batch_item_failures),
    )
    return {"batchItemFailures": batch_item_failures}


# AWS Lambda handler-naming convention alias
lambda_handler = handler
