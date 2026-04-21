"""
VecturaFlow — ChunkingAgent implementation.
Splits TextBlocks into Chunks sized for embedding, preserving source metadata.

Critical fix from agent scorecard audit:
  BEFORE (broken): concatenate ALL blocks → split → lose page/section metadata
  AFTER  (fixed):  chunk PER BLOCK → assign global index → metadata preserved

Why this matters:
  If a chunk is from page 12 of a PDF, RetrieverAgent needs to cite page 12.
  Concatenating loses that. Per-block chunking keeps it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
import json
import os
import time
from typing import Any

import boto3
from botocore.exceptions import ClientError
from langchain.text_splitter import RecursiveCharacterTextSplitter

from ingestion.logging_util import get_logger
from ingestion.models import Chunk, TextBlock

logger = get_logger(__name__)


def _region() -> str:
    return os.environ.get("AWS_DEFAULT_REGION", "us-east-1")


@lru_cache(maxsize=1)
def _sqs() -> Any:
    return boto3.client("sqs", region_name=_region())


@lru_cache(maxsize=1)
def _dynamo() -> Any:
    return boto3.resource("dynamodb", region_name=_region())


# ─────────────────────────────────────────────────────────────────────────────
# Core chunking logic
# ─────────────────────────────────────────────────────────────────────────────

def chunk_blocks(
    blocks: list[TextBlock],
    chunk_size: int = 512,
    chunk_overlap: int = 50,
) -> list[Chunk]:
    """
    Chunk a list of TextBlocks into Chunks for embedding.

    Strategy: chunk each TextBlock independently, then assign a global
    chunk_index across the whole document. This preserves per-block metadata
    (page, section, row) on every resulting Chunk.

    Args:
        blocks:        Parsed TextBlock list from ParserAgent.
        chunk_size:    Max characters per chunk (default 512 ≈ 128 tokens).
        chunk_overlap: Overlap between adjacent chunks (default 50 chars).

    Returns:
        Flat list of Chunk objects with global indices and preserved metadata.
    """
    if not blocks:
        return []

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", ". ", "! ", "? ", " ", ""],
        length_function=len,
    )

    all_chunks: list[Chunk] = []
    global_index = 0

    for block in blocks:
        if not block.text.strip():
            continue

        # Split this block's text (may produce 1 or more sub-chunks)
        sub_texts = splitter.split_text(block.text)

        for sub_text in sub_texts:
            sub_text = sub_text.strip()
            if not sub_text:
                continue

            all_chunks.append(Chunk(
                chunk_id=f"{block.doc_id}_chunk_{global_index}",
                doc_id=block.doc_id,
                text=sub_text,
                source=block.source,
                chunk_index=global_index,
                total_chunks=0,            # backfilled below
                file_type=block.file_type,
                page=block.page,
                section=block.section,
                row=block.row,
                element_type=block.element_type,
            ))
            global_index += 1

    # Backfill total_chunks now that we know the final count
    total = len(all_chunks)
    for chunk in all_chunks:
        chunk.total_chunks = total

    logger.info(
        "chunker.complete",
        doc_id=blocks[0].doc_id if blocks else "?",
        blocks=len(blocks),
        chunks=total,
    )
    return all_chunks


# ─────────────────────────────────────────────────────────────────────────────
# SQS publishing
# ─────────────────────────────────────────────────────────────────────────────

def _send_with_retry(queue_url: str, body: str, max_attempts: int = 3) -> None:
    """Send one SQS message with exponential backoff retry."""
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            _sqs().send_message(QueueUrl=queue_url, MessageBody=body)
            return
        except ClientError as exc:
            last_exc = exc
            wait = 2 ** attempt
            logger.warning(
                "sqs.send_retry",
                attempt=attempt + 1, wait_seconds=wait, error=str(exc),
            )
            time.sleep(wait)
    raise RuntimeError(
        f"SQS send failed after {max_attempts} attempts: {last_exc}"
    ) from last_exc


def _save_chunks_to_s3_fallback(chunks: list[Chunk], doc_id: str) -> None:
    """
    Last-resort fallback: write chunks to S3 ``failed-chunks/`` if SQS is down.
    Ops can replay from here manually.
    """
    try:
        s3 = boto3.client("s3", region_name=_region())
        bucket = os.environ.get("INGESTION_BUCKET", "")
        if not bucket:
            return
        key = f"failed-chunks/{doc_id}.json"
        payload = json.dumps([c.to_sqs_message() for c in chunks])
        s3.put_object(Bucket=bucket, Key=key, Body=payload.encode())
        logger.error("chunker.sqs_failed_saved_to_s3", doc_id=doc_id, key=key)
    except Exception as exc:
        logger.error("chunker.s3_fallback_failed", doc_id=doc_id, error=str(exc))


def publish_chunks(
    chunks: list[Chunk],
    queue_url: str,
) -> int:
    """
    Publish all chunks to SQS embedding queue.
    On total SQS failure, writes to S3 fallback bucket.

    Returns:
        Number of chunks successfully published.
    """
    if not chunks:
        logger.warning("chunker.publish_empty")
        return 0

    if len(chunks) > 1000:
        logger.warning(
            "chunker.large_document",
            doc_id=chunks[0].doc_id, chunks=len(chunks),
        )

    published = 0
    failed: list[Chunk] = []

    for chunk in chunks:
        try:
            body = json.dumps(chunk.to_sqs_message())
            _send_with_retry(queue_url, body)
            published += 1
        except Exception as exc:
            logger.error(
                "chunker.chunk_publish_failed",
                chunk_id=chunk.chunk_id, error=str(exc),
            )
            failed.append(chunk)

    if failed:
        doc_id = chunks[0].doc_id
        logger.error("chunker.partial_failure", doc_id=doc_id, failed=len(failed))
        _save_chunks_to_s3_fallback(failed, doc_id)

    logger.info(
        "chunker.published",
        doc_id=chunks[0].doc_id, published=published, failed=len(failed),
    )
    return published


# ─────────────────────────────────────────────────────────────────────────────
# DynamoDB registry update
# ─────────────────────────────────────────────────────────────────────────────

def update_registry_chunked(doc_id: str, chunk_count: int, table_name: str) -> None:
    """Update doc registry status to 'chunked' with chunk_count."""
    try:
        table = _dynamo().Table(table_name)
        table.update_item(
            Key={"doc_id": doc_id},
            UpdateExpression="SET #s = :s, chunk_count = :c, updated_at = :u",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": "chunked",
                ":c": chunk_count,
                ":u": datetime.now(timezone.utc).isoformat(),
            },
        )
    except ClientError as exc:
        logger.error("chunker.registry_update_failed", doc_id=doc_id, error=str(exc))


def update_registry_empty(doc_id: str, table_name: str) -> None:
    """Mark doc as having no extractable content."""
    try:
        table = _dynamo().Table(table_name)
        table.update_item(
            Key={"doc_id": doc_id},
            UpdateExpression="SET #s = :s, updated_at = :u",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": "empty_file",
                ":u": datetime.now(timezone.utc).isoformat(),
            },
        )
    except ClientError as exc:
        logger.error("chunker.registry_empty_failed", doc_id=doc_id, error=str(exc))
