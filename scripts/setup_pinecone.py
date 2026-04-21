"""
VecturaFlow — Pinecone index setup script.
Idempotent: safe to run multiple times. Creates the index if it doesn't exist,
validates dimension and metric if it does.

Usage:
    python -m scripts.setup_pinecone
    python -m scripts.setup_pinecone --dry-run
"""
from __future__ import annotations

import argparse
import sys
import time

from pinecone import Pinecone, ServerlessSpec

# text-embedding-3-small produces 1536-dim vectors
EMBEDDING_DIMENSION = 1536
METRIC = "cosine"


def setup_pinecone_index(
    api_key: str,
    index_name: str,
    region: str = "us-east-1",
    dry_run: bool = False,
) -> bool:
    """
    Creates a Pinecone serverless index for VecturaFlow.
    Returns True if index is ready, False on failure.
    """
    print(f"\n{'[DRY RUN] ' if dry_run else ''}Setting up Pinecone index: {index_name}")
    print(f"  Region  : {region}")
    print(f"  Dim     : {EMBEDDING_DIMENSION}")
    print(f"  Metric  : {METRIC}")

    pc = Pinecone(api_key=api_key)

    # ── Check if index already exists ─────────────────────────────────────────
    existing = pc.list_indexes()
    existing_names = [idx.name for idx in existing]

    if index_name in existing_names:
        print(f"\n  Index '{index_name}' already exists. Validating config...")
        desc = pc.describe_index(index_name)

        actual_dim = desc.dimension
        actual_metric = desc.metric

        if actual_dim != EMBEDDING_DIMENSION:
            print(f"  ERROR: Existing index has dimension {actual_dim}, expected {EMBEDDING_DIMENSION}.")
            print("  Delete the index and re-run this script to recreate it.")
            return False

        if actual_metric != METRIC:
            print(f"  WARNING: Index metric is '{actual_metric}', expected '{METRIC}'.")
            print("  Cosine similarity is strongly recommended for text embeddings.")

        print(f"  Index validated. Status: {desc.status.get('ready', False)}")
        print(f"\n  Pinecone index '{index_name}' is ready.")
        return True

    # ── Create index ──────────────────────────────────────────────────────────
    if dry_run:
        print(f"\n  [DRY RUN] Would create index '{index_name}' — no changes made.")
        return True

    print(f"\n  Creating index '{index_name}'...")
    pc.create_index(
        name=index_name,
        dimension=EMBEDDING_DIMENSION,
        metric=METRIC,
        spec=ServerlessSpec(cloud="aws", region=region),
    )

    # ── Wait for index to become ready ────────────────────────────────────────
    print("  Waiting for index to become ready", end="", flush=True)
    for _ in range(30):
        time.sleep(3)
        desc = pc.describe_index(index_name)
        if desc.status.get("ready"):
            print(" ready!")
            break
        print(".", end="", flush=True)
    else:
        print("\n  WARNING: Index creation timed out. Check Pinecone console.")
        return False

    # ── Validate index stats ──────────────────────────────────────────────────
    index = pc.Index(index_name)
    stats = index.describe_index_stats()
    print(f"\n  Index stats:")
    print(f"    Total vectors : {stats.total_vector_count}")
    print(f"    Namespaces    : {list(stats.namespaces.keys()) or ['(none)']}")
    print(f"\n  Pinecone index '{index_name}' created and ready.")
    return True


def main():
    parser = argparse.ArgumentParser(description="Set up VecturaFlow Pinecone index")
    parser.add_argument("--dry-run", action="store_true", help="Validate only, no changes")
    args = parser.parse_args()

    # Load from .env
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    import os
    api_key = os.environ.get("PINECONE_API_KEY")
    index_name = os.environ.get("PINECONE_INDEX", "vecturaflow")
    region = os.environ.get("PINECONE_REGION", "us-east-1")

    if not api_key:
        print("ERROR: PINECONE_API_KEY not set. Copy .env.example to .env and fill in your key.")
        sys.exit(1)

    success = setup_pinecone_index(
        api_key=api_key,
        index_name=index_name,
        region=region,
        dry_run=args.dry_run,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
