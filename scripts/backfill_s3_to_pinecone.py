#!/usr/bin/env python3
"""
VecturaFlow — S3 → Pinecone backfill.

Walks the ingestion bucket, parses each supported file, chunks it, embeds the
chunks via OpenAI, upserts the vectors into Pinecone, and writes/updates a row
in the DynamoDB registry. Idempotent via deterministic chunk IDs — safe to
re-run.

This is the offline twin of the `lambda_s3 → lambda_parser → lambda_embed`
Lambda chain. It exists so already-uploaded files (which missed their S3 event)
can be ingested without needing to re-upload or replay events.

Usage (run from repo root):
    python scripts/backfill_s3_to_pinecone.py
    python scripts/backfill_s3_to_pinecone.py --dry-run
    python scripts/backfill_s3_to_pinecone.py --force
    python scripts/backfill_s3_to_pinecone.py --prefix docs/ --bucket my-bucket

Requires the `.env` at repo root to have OPENAI_API_KEY, PINECONE_API_KEY,
PINECONE_INDEX, INGESTION_BUCKET, REGISTRY_TABLE and (optionally) AWS_*.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import sys
import time
from typing import Any

# Make `ingestion.*` importable when run as `python scripts/backfill_s3_to_pinecone.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import boto3  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402
from openai import OpenAI, RateLimitError  # noqa: E402
import pinecone  # noqa: E402

from ingestion.chunker import chunk_blocks  # noqa: E402
from ingestion.models import Chunk  # noqa: E402
from ingestion.parser import parse_file  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Constants — tuned to match the Lambda pipeline exactly so backfill output is
# indistinguishable from future auto-ingested output.
# ─────────────────────────────────────────────────────────────────────────────

SUPPORTED_TYPES = frozenset({"pdf", "docx", "csv", "txt", "json"})
EMBED_BATCH_SIZE = 100                   # OpenAI's per-request cap is 2048 but 100 is safer
UPSERT_BATCH_SIZE = 100                  # Pinecone's recommended upsert batch
MAX_METADATA_TEXT_LEN = 500              # matches embeddings/lambda_embed.py
OPENAI_MAX_RETRIES = 5
OPENAI_BASE_BACKOFF = 1.0
PINECONE_MAX_RETRIES = 3
PINECONE_BASE_BACKOFF = 0.5


# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)
log = logging.getLogger("backfill")


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

class Config:
    """Resolves config from the environment, failing fast if anything required is missing."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.bucket = args.bucket or self._required("INGESTION_BUCKET")
        self.prefix = args.prefix or ""
        self.registry_table = os.environ.get("REGISTRY_TABLE", "vecturaflow-prod-registry")
        self.region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        self.openai_key = self._required("OPENAI_API_KEY")
        self.pinecone_key = self._required("PINECONE_API_KEY")
        self.pinecone_index = os.environ.get("PINECONE_INDEX", "vecturaflow")
        self.embedding_model = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
        self.chunk_size = int(os.environ.get("CHUNK_SIZE", "512"))
        self.chunk_overlap = int(os.environ.get("CHUNK_OVERLAP", "50"))
        self.force = args.force
        self.dry_run = args.dry_run

    @staticmethod
    def _required(name: str) -> str:
        v = os.environ.get(name)
        if not v:
            raise RuntimeError(f"Missing required environment variable: {name}")
        return v


# ─────────────────────────────────────────────────────────────────────────────
# Clients (created once per run; no cross-run caching needed)
# ─────────────────────────────────────────────────────────────────────────────

def _build_clients(cfg: Config) -> dict[str, Any]:
    s3 = boto3.client("s3", region_name=cfg.region)
    dynamo = boto3.resource("dynamodb", region_name=cfg.region).Table(cfg.registry_table)
    openai_client = OpenAI(api_key=cfg.openai_key)
    pc = pinecone.Pinecone(api_key=cfg.pinecone_key)
    index = pc.Index(cfg.pinecone_index)
    return {"s3": s3, "dynamo": dynamo, "openai": openai_client, "pinecone": index}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _doc_id(bucket: str, key: str) -> str:
    """Deterministic SHA256 — matches `ingestion.lambda_s3._make_doc_id`."""
    import hashlib
    return hashlib.sha256(f"{bucket}/{key}".encode()).hexdigest()


def _file_type(key: str) -> str | None:
    parts = key.rsplit(".", 1)
    return parts[1].lower() if len(parts) == 2 else None


def _already_embedded(dynamo: Any, doc_id: str) -> bool:
    try:
        resp = dynamo.get_item(
            Key={"doc_id": doc_id},
            ProjectionExpression="#s",
            ExpressionAttributeNames={"#s": "status"},
        )
        item = resp.get("Item")
        return bool(item and item.get("status") == "embedded")
    except ClientError as exc:
        log.warning("registry_check_failed doc_id=%s error=%s", doc_id, exc)
        return False


def _update_registry_embedded(
    dynamo: Any, doc_id: str, source: str, file_type: str, chunk_count: int,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    dynamo.put_item(Item={
        "doc_id": doc_id,
        "source": source,
        "file_type": file_type,
        "status": "embedded",
        "chunk_count": chunk_count,
        "ingested_at": now,
        "embedded_at": now,
        "updated_at": now,
        "ingested_via": "backfill",
    })


def _embed_with_backoff(client: OpenAI, model: str, texts: list[str]) -> list[list[float]]:
    """Call OpenAI embeddings with exponential backoff on 429. Matches lambda_embed."""
    for attempt in range(1, OPENAI_MAX_RETRIES + 1):
        try:
            resp = client.embeddings.create(model=model, input=texts)
            return [item.embedding for item in resp.data]
        except RateLimitError:
            if attempt == OPENAI_MAX_RETRIES:
                raise
            wait = OPENAI_BASE_BACKOFF * (2 ** (attempt - 1))
            log.warning("openai.rate_limited attempt=%d wait=%.1fs", attempt, wait)
            time.sleep(wait)
    return []  # unreachable


def _upsert_with_retry(index: Any, vectors: list[dict]) -> None:
    """Pinecone upsert with retry — matches lambda_embed."""
    for attempt in range(1, PINECONE_MAX_RETRIES + 1):
        try:
            index.upsert(vectors=vectors)
            return
        except Exception as exc:
            if attempt == PINECONE_MAX_RETRIES:
                raise
            wait = PINECONE_BASE_BACKOFF * (2 ** (attempt - 1))
            log.warning("pinecone.upsert_retry attempt=%d wait=%.1fs error=%s", attempt, wait, exc)
            time.sleep(wait)


def _build_vector(chunk: Chunk, embedding: list[float]) -> dict[str, Any]:
    """Build a Pinecone vector dict — schema matches lambda_embed._build_vector."""
    metadata: dict[str, Any] = {
        "doc_id": chunk.doc_id,
        "source": chunk.source,
        "text": chunk.text[:MAX_METADATA_TEXT_LEN],
        "chunk_index": chunk.chunk_index,
        "file_type": chunk.file_type,
    }
    for optional in ("page", "section"):
        val = getattr(chunk, optional, None)
        if val is not None:
            metadata[optional] = val
    return {"id": chunk.chunk_id, "values": embedding, "metadata": metadata}


def _batched(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


# ─────────────────────────────────────────────────────────────────────────────
# Core
# ─────────────────────────────────────────────────────────────────────────────

def _list_objects(s3: Any, bucket: str, prefix: str) -> list[dict[str, Any]]:
    """Full paginated list of objects in the bucket (skipping 0-byte and failed-chunks/)."""
    objects: list[dict[str, Any]] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.startswith("failed-chunks/") or obj["Size"] == 0:
                continue
            objects.append(obj)
    return objects


def _process_one(
    clients: dict[str, Any], cfg: Config, key: str, size: int,
) -> dict[str, Any]:
    """Parse → chunk → embed → upsert → registry. Returns a per-file summary dict."""
    doc_id = _doc_id(cfg.bucket, key)
    file_type = _file_type(key)
    summary = {"key": key, "doc_id": doc_id, "file_type": file_type, "status": "unknown"}

    if file_type not in SUPPORTED_TYPES:
        log.warning("skip.unsupported key=%s type=%s", key, file_type)
        summary["status"] = "skipped_unsupported"
        return summary

    if not cfg.force and _already_embedded(clients["dynamo"], doc_id):
        log.info("skip.already_embedded key=%s", key)
        summary["status"] = "skipped_embedded"
        return summary

    log.info("download key=%s size=%d", key, size)
    try:
        body = clients["s3"].get_object(Bucket=cfg.bucket, Key=key)["Body"].read()
    except ClientError as exc:
        log.error("s3.download_failed key=%s error=%s", key, exc)
        summary["status"] = "failed_download"
        return summary

    log.info("parse key=%s", key)
    try:
        blocks = parse_file(body, file_type, doc_id, source=key)
    except Exception as exc:
        log.error("parse_failed key=%s error=%s", key, exc)
        summary["status"] = "failed_parse"
        return summary

    if not blocks:
        log.warning("empty_document key=%s", key)
        summary["status"] = "empty"
        return summary

    chunks = chunk_blocks(blocks, chunk_size=cfg.chunk_size, chunk_overlap=cfg.chunk_overlap)
    if not chunks:
        log.warning("no_chunks key=%s", key)
        summary["status"] = "empty"
        return summary

    log.info("chunked key=%s blocks=%d chunks=%d", key, len(blocks), len(chunks))

    if cfg.dry_run:
        summary["status"] = "dry_run"
        summary["blocks"] = len(blocks)
        summary["chunks"] = len(chunks)
        return summary

    # ── Embed in batches ──────────────────────────────────────────────────
    all_vectors: list[dict[str, Any]] = []
    for batch in _batched(chunks, EMBED_BATCH_SIZE):
        texts = [c.text for c in batch]
        embeddings = _embed_with_backoff(clients["openai"], cfg.embedding_model, texts)
        all_vectors.extend(_build_vector(c, e) for c, e in zip(batch, embeddings, strict=True))
    log.info("embedded key=%s vectors=%d", key, len(all_vectors))

    # ── Upsert in batches ─────────────────────────────────────────────────
    for batch in _batched(all_vectors, UPSERT_BATCH_SIZE):
        _upsert_with_retry(clients["pinecone"], batch)
    log.info("upserted key=%s vectors=%d", key, len(all_vectors))

    # ── Registry ──────────────────────────────────────────────────────────
    _update_registry_embedded(
        clients["dynamo"], doc_id, source=key,
        file_type=file_type, chunk_count=len(all_vectors),
    )
    summary["status"] = "embedded"
    summary["chunks"] = len(all_vectors)
    return summary


def run(cfg: Config) -> dict[str, Any]:
    """Top-level entry — returns an aggregated summary. Never raises on per-file errors."""
    clients = _build_clients(cfg)
    log.info(
        "backfill.start bucket=%s prefix=%s index=%s dry_run=%s force=%s",
        cfg.bucket, cfg.prefix or "(none)", cfg.pinecone_index, cfg.dry_run, cfg.force,
    )

    objects = _list_objects(clients["s3"], cfg.bucket, cfg.prefix)
    log.info("backfill.objects found=%d", len(objects))

    per_file: list[dict[str, Any]] = []
    for obj in objects:
        try:
            per_file.append(_process_one(clients, cfg, obj["Key"], obj["Size"]))
        except Exception as exc:  # defensive — keep going
            log.exception("unexpected.file_error key=%s error=%s", obj["Key"], exc)
            per_file.append({"key": obj["Key"], "status": "failed_unexpected", "error": str(exc)})

    totals: dict[str, int] = {}
    for f in per_file:
        totals[f["status"]] = totals.get(f["status"], 0) + 1

    log.info("backfill.done totals=%s", totals)
    return {"totals": totals, "per_file": per_file}


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--bucket", help="Override INGESTION_BUCKET env var.")
    p.add_argument("--prefix", default="", help="Only ingest keys under this S3 prefix.")
    p.add_argument("--dry-run", action="store_true", help="Parse+chunk only; no embed/upsert/write.")
    p.add_argument("--force", action="store_true", help="Re-ingest docs already marked 'embedded'.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        cfg = Config(args)
    except RuntimeError as exc:
        log.error("config_error %s", exc)
        return 2

    result = run(cfg)
    totals = result["totals"]

    # Exit non-zero only if *every* file failed (otherwise partial success is fine).
    attempted = sum(totals.values())
    failed = sum(v for k, v in totals.items() if k.startswith("failed"))
    if attempted and failed == attempted:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
