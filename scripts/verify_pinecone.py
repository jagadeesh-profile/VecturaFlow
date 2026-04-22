#!/usr/bin/env python3
"""
VecturaFlow — Pinecone verification utility.

Checks the live Pinecone index used by the project, prints index stats,
and fetches a couple of known vector IDs derived from the embedded registry.

Usage:
    python scripts/verify_pinecone.py
    python scripts/verify_pinecone.py --limit 2
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

import boto3
from pinecone import Pinecone

from api.config import settings


def _registry_table() -> Any:
    dynamo = boto3.resource("dynamodb", region_name=settings.aws_default_region)
    return dynamo.Table(settings.registry_table)


def _safe_scan_embedded(limit: int) -> list[dict[str, Any]]:
    table = _registry_table()
    response = table.scan(
        ProjectionExpression="doc_id, #s, chunk_count, #src",
        ExpressionAttributeNames={"#s": "status", "#src": "source"},
    )
    items = response.get("Items", [])
    embedded = [item for item in items if item.get("status") == "embedded"]
    return embedded[:limit]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=2, help="How many registry rows to inspect")
    args = parser.parse_args()

    print("Connecting to Pinecone index from project config...")
    print(f"  index   : {settings.pinecone_index}")
    print(f"  region  : {settings.pinecone_region}")
    print(f"  model   : {settings.embedding_model}")

    pc = Pinecone(api_key=settings.pinecone_api_key)
    index = pc.Index(settings.pinecone_index)

    stats = index.describe_index_stats()
    namespaces = getattr(stats, "namespaces", {}) or {}
    print("\nIndex stats:")
    print(f"  total vectors: {stats.total_vector_count}")
    print(f"  namespaces    : {list(namespaces.keys()) or ['(none)']}")
    if namespaces:
        for namespace, namespace_stats in namespaces.items():
            vector_count = getattr(namespace_stats, "vector_count", None)
            print(f"    - {namespace}: {vector_count}")

    embedded_rows = _safe_scan_embedded(args.limit)
    if not embedded_rows:
        print("\nNo embedded registry rows were found. Nothing to fetch.")
        return 1

    vector_ids: list[str] = []
    print("\nRegistry samples:")
    for row in embedded_rows:
        doc_id = row["doc_id"]
        chunk_count = int(row.get("chunk_count", 0) or 0)
        source = row.get("source", "")
        vector_id = f"{doc_id}_chunk_0"
        vector_ids.append(vector_id)
        print(f"  doc_id={doc_id}")
        print(f"    source     : {source}")
        print(f"    chunk_count: {chunk_count}")
        print(f"    vector_id  : {vector_id}")

    print("\nFetching vectors from Pinecone...")
    fetched = index.fetch(ids=vector_ids)
    vectors = getattr(fetched, "vectors", {}) or {}

    for vector_id in vector_ids:
        vector = vectors.get(vector_id)
        if vector is None:
            print(f"  {vector_id}: NOT FOUND")
            continue

        metadata = getattr(vector, "metadata", {}) or {}
        print(f"  {vector_id}:")
        print(f"    score/values: fetched")
        print(f"    source      : {metadata.get('source', '')}")
        snippet = (metadata.get("text", "") or "")[:180].replace("\n", " ")
        print(f"    snippet     : {snippet}")
        print(f"    metadata    : {metadata}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())