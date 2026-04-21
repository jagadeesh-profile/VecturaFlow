"""Tests for MMR reranking in api.retriever."""
from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("PINECONE_API_KEY", "pc-test")
os.environ.setdefault("PINECONE_INDEX", "vecturaflow-test")
os.environ.setdefault("REGISTRY_TABLE", "vecturaflow-registry")
os.environ.setdefault("KEYS_TABLE", "vecturaflow-keys")
os.environ.setdefault("INGESTION_BUCKET", "vecturaflow-test-bucket")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("INGESTION_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/test/ing")
os.environ.setdefault("EMBEDDING_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/test/emb")

from api.retriever import _cosine, _mmr_rerank


def _match(id_, score, values):
    """Stand-in for a Pinecone match object."""
    return SimpleNamespace(id=id_, score=score, values=values, metadata={})


def test_cosine_basics():
    assert _cosine([1, 0, 0], [1, 0, 0]) == 1.0
    assert _cosine([1, 0, 0], [0, 1, 0]) == 0.0
    # Zero vector returns 0, not NaN
    assert _cosine([0, 0, 0], [1, 2, 3]) == 0.0


def test_mmr_picks_diverse_after_relevance():
    """
    Three candidates: a and b are near-duplicates both highly relevant,
    c is less relevant but genuinely diverse. λ=0.3 tilts MMR toward
    diversity so the pair {a, c} beats {a, b}.
    """
    query = [1.0, 0.0, 0.0]
    a = _match("a", 0.99, [1.0, 0.0, 0.01])   # near-parallel to query
    b = _match("b", 0.98, [0.99, 0.01, 0.0])  # near-duplicate of a
    c = _match("c", 0.80, [0.5, 0.87, 0.0])   # 60° off axis — diverse

    reranked = _mmr_rerank(query, [a, b, c], top_k=2, lambda_=0.3)
    ids = [m.id for m in reranked]
    assert ids[0] == "a"          # highest pure relevance wins first slot
    assert "c" in ids             # diversity beats the near-duplicate b
    assert "b" not in ids


def test_mmr_pure_relevance_when_lambda_one():
    """λ=1.0 should reduce to plain score-ordering."""
    query = [1.0, 0.0, 0.0]
    a = _match("a", 0.95, [1.0, 0.0, 0.0])
    b = _match("b", 0.94, [0.99, 0.01, 0.0])  # redundant but still relevant
    c = _match("c", 0.70, [0.0, 1.0, 0.0])

    reranked = _mmr_rerank(query, [a, b, c], top_k=2, lambda_=1.0)
    assert [m.id for m in reranked] == ["a", "b"]


def test_mmr_falls_back_when_values_missing():
    """If candidates lack embedding vectors, MMR degrades gracefully to score-order."""
    a = SimpleNamespace(id="a", score=0.9, metadata={})   # no .values
    b = SimpleNamespace(id="b", score=0.8, metadata={})

    reranked = _mmr_rerank([1.0, 0.0], [a, b], top_k=2)
    assert [m.id for m in reranked] == ["a", "b"]


def test_mmr_empty_input():
    assert _mmr_rerank([1.0, 0.0], [], top_k=5) == []
