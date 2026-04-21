"""
VecturaFlow — RetrieverAgent.

Embeds a query using OpenAI, searches Pinecone for the most relevant
chunks, and returns a ranked list of RetrievedChunk objects.

Redis is used as a short-lived query cache (5-min TTL) to reduce
latency and API cost on repeated questions.

Design principles:
- Module-level clients reused across process lifetime
- Redis failure is non-fatal — falls through to live Pinecone query
- Low-confidence fallback: if no chunk meets score threshold, return
  top-3 flagged as low_confidence so the RAGAgent can handle them
- All retries are synchronous (Lambda / FastAPI thread context)
"""
from __future__ import annotations

import contextlib
from functools import lru_cache
import hashlib
import json
import time
from typing import Any

from openai import OpenAI
from pinecone import Pinecone
import redis

from api.config import settings
from api.logger import logger
from api.schemas import RetrievedChunk

try:  # observability is optional — retriever must work in slim Lambda bundles
    from api.observability import RETRIEVER_CACHE
except Exception:  # pragma: no cover
    RETRIEVER_CACHE = None  # type: ignore[assignment]

_CACHE_TTL = 300          # 5 minutes
_EMBED_MAX_RETRIES = 3
_PINECONE_MAX_RETRIES = 2
_BASE_BACKOFF = 0.5

# MMR (Maximal Marginal Relevance) — trade off relevance against diversity so
# the top-K doesn't contain N near-duplicates of the same paragraph.
_MMR_LAMBDA = 0.5          # 1.0 = pure relevance, 0.0 = pure diversity
_MMR_CANDIDATES = 20       # pull this many from Pinecone before reranking


# ─────────────────────────────────────────────────────────────────────────────
# Lazy clients — constructed on first call so the module can import even when
# Pinecone/OpenAI creds are absent (unit tests, docs generation, etc.).
# ─────────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _openai_client() -> OpenAI:
    return OpenAI(api_key=settings.openai_api_key, timeout=25)


@lru_cache(maxsize=1)
def _pinecone_index():
    pc = Pinecone(api_key=settings.pinecone_api_key)
    return pc.Index(settings.pinecone_index)


@lru_cache(maxsize=1)
def _redis_cache() -> redis.Redis | None:
    """Create a Redis client and ping it. Return None on any failure."""
    try:
        client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        client.ping()
        return client
    except Exception as exc:
        logger.info("retriever.redis_unavailable", error=str(exc))
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _cache_key(query: str, top_k: int, filters: dict | None) -> str:
    raw = f"{query}|{top_k}|{json.dumps(filters, sort_keys=True) if filters else ''}"
    return "vf:retriever:" + hashlib.md5(raw.encode()).hexdigest()


def _embed_query(query: str) -> list[float]:
    """Embed a single query string with exponential backoff."""
    client = _openai_client()
    for attempt in range(1, _EMBED_MAX_RETRIES + 1):
        try:
            response = client.embeddings.create(
                model=settings.embedding_model,
                input=[query],
            )
            return response.data[0].embedding
        except Exception as exc:
            if attempt == _EMBED_MAX_RETRIES:
                logger.error("retriever.embed_failed", error=str(exc), query_len=len(query))
                raise
            time.sleep(_BASE_BACKOFF * (2 ** (attempt - 1)))
    return []  # unreachable — loop always raises or returns on last attempt


def _query_pinecone(
    vector: list[float],
    top_k: int,
    filters: dict | None,
    include_values: bool = False,
) -> list[Any]:
    """Query Pinecone with up to _PINECONE_MAX_RETRIES attempts."""
    index = _pinecone_index()
    for attempt in range(1, _PINECONE_MAX_RETRIES + 1):
        try:
            results = index.query(
                vector=vector,
                top_k=top_k,
                include_metadata=True,
                include_values=include_values,
                filter=filters,
            )
            return results.matches
        except Exception as exc:
            if attempt == _PINECONE_MAX_RETRIES:
                logger.error("retriever.pinecone_failed", error=str(exc))
                raise
            time.sleep(_BASE_BACKOFF * (2 ** (attempt - 1)))
    return []  # unreachable — loop always raises or returns on last attempt


def _cosine(a: list[float], b: list[float]) -> float:
    """Plain cosine similarity — avoids pulling in numpy for one operation."""
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0 or nb == 0:
        return 0.0
    return dot / ((na ** 0.5) * (nb ** 0.5))


def _mmr_rerank(
    query_vec: list[float],
    matches: list[Any],
    top_k: int,
    lambda_: float = _MMR_LAMBDA,
) -> list[Any]:
    """
    Re-rank candidate matches by Maximal Marginal Relevance.

    Each iteration picks the candidate that maximises
        λ * sim(query, candidate)  -  (1-λ) * max sim(candidate, selected)

    Returns the top_k reranked matches. If candidates lack ``values``
    (the embedding vector), falls back to relevance-only ordering.
    """
    if not matches:
        return matches

    candidates = list(matches)
    # All candidates must carry their embedding for diversity computation;
    # if Pinecone wasn't asked for ``include_values`` we fall through to
    # score-ordering unchanged.
    vecs = [getattr(c, "values", None) for c in candidates]
    if any(v is None for v in vecs):
        return candidates[:top_k]

    selected: list[Any] = []
    selected_vecs: list[list[float]] = []
    remaining = list(zip(candidates, vecs))

    # Pre-compute query similarity (== match.score when the index is cosine,
    # but we recompute to keep this function self-contained and correct even
    # if the underlying index uses a different metric).
    query_sims = {id(c): _cosine(query_vec, v) for c, v in remaining}

    while remaining and len(selected) < top_k:
        best_idx = 0
        best_score = float("-inf")
        for i, (cand, cand_vec) in enumerate(remaining):
            relevance = query_sims[id(cand)]
            if selected_vecs:
                diversity = max(_cosine(cand_vec, sv) for sv in selected_vecs)
            else:
                diversity = 0.0
            mmr = lambda_ * relevance - (1 - lambda_) * diversity
            if mmr > best_score:
                best_score = mmr
                best_idx = i
        chosen, chosen_vec = remaining.pop(best_idx)
        selected.append(chosen)
        selected_vecs.append(chosen_vec)
    return selected


def _matches_to_chunks(matches: list[Any], low_confidence: bool = False) -> list[RetrievedChunk]:
    """Convert Pinecone match objects to RetrievedChunk models."""
    chunks = []
    for m in matches:
        meta = m.metadata or {}
        chunks.append(
            RetrievedChunk(
                chunk_id=m.id,
                doc_id=meta.get("doc_id", ""),
                text=meta.get("text", ""),
                source=meta.get("source", ""),
                score=float(m.score),
                chunk_index=int(meta.get("chunk_index", 0)),
                low_confidence=low_confidence,
            )
        )
    return chunks


# ─────────────────────────────────────────────────────────────────────────────
# Public interface
# ─────────────────────────────────────────────────────────────────────────────

def retrieve(
    query: str,
    top_k: int | None = None,
    filters: dict[str, Any] | None = None,
    use_mmr: bool = True,
) -> list[RetrievedChunk]:
    """
    Retrieve the most relevant chunks for a query.

    Args:
        query:   The user's question or sub-query.
        top_k:   Number of results to request from Pinecone.
                 Defaults to settings.retrieval_top_k (5).
        filters: Optional Pinecone metadata filters
                 e.g. {"source": "report.pdf"}.
        use_mmr: If True (default), over-fetch candidates from Pinecone and
                 re-rank with MMR for diversity. Disable for raw-score
                 retrieval when debugging.

    Returns:
        Ranked list of RetrievedChunk objects, sorted by score descending.
        If no chunk meets the score threshold, returns the top-3 with
        low_confidence=True so the RAGAgent can respond appropriately.

    Raises:
        Exception: If both the OpenAI embed call and Pinecone query fail
                   after all retries.
    """
    if top_k is None:
        top_k = settings.retrieval_top_k

    # ── Cache check ───────────────────────────────────────────────────────────
    key = _cache_key(query, top_k, filters)
    cache = _redis_cache()
    if cache is not None:
        try:
            cached = cache.get(key)
            if cached:
                logger.debug("retriever.cache_hit", query_len=len(query))
                if RETRIEVER_CACHE is not None:
                    RETRIEVER_CACHE.labels(outcome="hit").inc()
                data = json.loads(cached)
                return [RetrievedChunk(**c) for c in data]
            if RETRIEVER_CACHE is not None:
                RETRIEVER_CACHE.labels(outcome="miss").inc()
        except Exception:
            if RETRIEVER_CACHE is not None:
                RETRIEVER_CACHE.labels(outcome="error").inc()
            pass  # Redis failure is non-fatal

    # ── Embed query ───────────────────────────────────────────────────────────
    vector = _embed_query(query)

    # ── Query Pinecone ────────────────────────────────────────────────────────
    # When MMR is on we over-fetch candidates and rely on the rerank to pick
    # the final top_k; otherwise we only ask for exactly top_k.
    fetch_k = max(top_k, _MMR_CANDIDATES) if use_mmr else top_k
    try:
        matches = _query_pinecone(
            vector,
            fetch_k,
            filters,
            include_values=use_mmr,
        )
    except Exception:
        # Pinecone unavailable — return empty list with error flag
        return []

    if not matches:
        return []

    # ── Apply score threshold ─────────────────────────────────────────────────
    threshold = settings.retrieval_score_threshold
    above_threshold = [m for m in matches if m.score >= threshold]

    if above_threshold:
        if use_mmr:
            reranked = _mmr_rerank(vector, above_threshold, top_k)
        else:
            reranked = above_threshold[:top_k]
        chunks = _matches_to_chunks(reranked)
    else:
        # Low-confidence fallback: return top-3 regardless, flagged
        logger.info(
            "retriever.low_confidence_fallback",
            top_score=matches[0].score if matches else 0,
            threshold=threshold,
        )
        chunks = _matches_to_chunks(matches[:3], low_confidence=True)

    # ── Sort descending by score ──────────────────────────────────────────────
    chunks.sort(key=lambda c: c.score, reverse=True)

    # ── Cache result ──────────────────────────────────────────────────────────
    if cache is not None:
        with contextlib.suppress(Exception):
            cache.setex(key, _CACHE_TTL, json.dumps([c.model_dump() for c in chunks]))

    logger.info(
        "retriever.complete",
        query_len=len(query),
        chunk_count=len(chunks),
        low_confidence=any(c.low_confidence for c in chunks),
    )

    return chunks
