from __future__ import annotations

import os
from unittest.mock import MagicMock

os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("PINECONE_API_KEY", "pc-test")
os.environ.setdefault("PINECONE_INDEX", "vecturaflow-test")
os.environ.setdefault("REGISTRY_TABLE", "vecturaflow-registry")
os.environ.setdefault("INGESTION_BUCKET", "vecturaflow-test-bucket")
os.environ.setdefault("INGESTION_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/test/ing")
os.environ.setdefault("EMBEDDING_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/test/emb")


def test_verify_pinecone_queries_embedded_rows_by_gsi(monkeypatch):
    """Verification utility must use the status GSI, never a table scan."""
    from scripts import verify_pinecone

    table = MagicMock()
    table.query.return_value = {
        "Items": [{"doc_id": "doc1", "status": "embedded", "chunk_count": 1}]
    }
    monkeypatch.setattr(verify_pinecone, "_registry_table", lambda: table)

    rows = verify_pinecone._safe_query_embedded(limit=1)

    assert rows == [{"doc_id": "doc1", "status": "embedded", "chunk_count": 1}]
    table.query.assert_called_once()
    assert table.query.call_args.kwargs["IndexName"] == "status-ingested_at-index"
    table.scan.assert_not_called()
