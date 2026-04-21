"""
VecturaFlow — RetrieverAgent + RAGAgent tests.

Covers:
Retriever:
  - Cache hit returns without calling OpenAI/Pinecone
  - Cache miss: embed + Pinecone query + cache store
  - Score threshold filtering: above-threshold chunks returned
  - Low-confidence fallback: top-3 returned with low_confidence=True
  - Redis unavailable: bypasses cache, still returns results
  - Pinecone failure: returns empty list (non-fatal)

RAGAgent nodes (unit):
  - decompose_query: simple query passed through unchanged
  - decompose_query: multi-part query split into sub-queries
  - retrieve_context: deduplication by chunk_id
  - generate_answer: no context → no_context confidence
  - generate_answer: with context → answer + source citations
  - validate_answer: UNGROUNDED verdict downgrades confidence
  - validate_answer: GROUNDED verdict keeps confidence

RAGAgent end-to-end (integration):
  - run_rag: full graph invocation with mocked retriever + LLM
  - run_rag: no context → no_context response

API endpoint:
  - POST /v1/chat/completions: happy path returns 200 with answer
  - POST /v1/chat/completions: RAGAgent error → 503
"""
from __future__ import annotations

import json
import os
import unittest
from unittest.mock import MagicMock, patch

# ── Set env vars BEFORE importing any app module ─────────────────────────────
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("PINECONE_API_KEY", "pc-test")
os.environ.setdefault("PINECONE_INDEX", "vecturaflow-test")
os.environ.setdefault("REGISTRY_TABLE", "vecturaflow-registry")
os.environ.setdefault("KEYS_TABLE", "vecturaflow-keys")
os.environ.setdefault("INGESTION_BUCKET", "vecturaflow-test-bucket")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("INGESTION_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/test/ingestion")
os.environ.setdefault("EMBEDDING_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/test/embedding")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fake_embedding(dim: int = 1536) -> list[float]:
    return [0.01] * dim


def _make_pinecone_match(
    chunk_id: str = "doc1_chunk_0",
    score: float = 0.85,
    doc_id: str = "doc1",
    source: str = "report.pdf",
    text: str = "Some retrieved text.",
    chunk_index: int = 0,
    page: int | None = 1,
) -> MagicMock:
    match = MagicMock()
    match.id = chunk_id
    match.score = score
    match.metadata = {
        "doc_id": doc_id,
        "source": source,
        "text": text,
        "chunk_index": chunk_index,
    }
    if page is not None:
        match.metadata["page"] = page
    return match


def _make_openai_embed_response(text: str = "query") -> MagicMock:
    resp = MagicMock()
    resp.data = [MagicMock(embedding=_fake_embedding())]
    return resp


def _make_llm_response(content: str) -> MagicMock:
    resp = MagicMock()
    resp.content = content
    return resp


def _make_retrieved_chunk_dict(
    chunk_id: str = "doc1_chunk_0",
    score: float = 0.85,
    low_confidence: bool = False,
) -> dict:
    return {
        "chunk_id": chunk_id,
        "doc_id": "doc1",
        "text": "Some context text about the topic.",
        "source": "report.pdf",
        "score": score,
        "chunk_index": 0,
        "low_confidence": low_confidence,
    }


# ─────────────────────────────────────────────────────────────────────────────
# RetrieverAgent tests
# ─────────────────────────────────────────────────────────────────────────────

class TestRetrieverCacheHit(unittest.TestCase):

    @patch("api.retriever._pinecone_index")
    @patch("api.retriever._openai_client")
    @patch("api.retriever._redis_cache")
    def test_cache_hit_skips_openai_and_pinecone(self, mock_cache, mock_openai, mock_index):
        """Cache hit: OpenAI and Pinecone must NOT be called."""
        from api.schemas import RetrievedChunk
        cached_chunk = RetrievedChunk(
            chunk_id="doc1_chunk_0", doc_id="doc1",
            text="cached text", source="report.pdf",
            score=0.9, chunk_index=0,
        )
        mock_cache.return_value.get.return_value = json.dumps([cached_chunk.model_dump()])

        from api.retriever import retrieve
        result = retrieve("what is the policy?")

        mock_openai.return_value.embeddings.create.assert_not_called()
        mock_index.return_value.query.assert_not_called()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].chunk_id, "doc1_chunk_0")

    @patch("api.retriever._pinecone_index")
    @patch("api.retriever._openai_client")
    @patch("api.retriever._redis_cache")
    def test_cache_miss_calls_openai_and_pinecone(self, mock_cache, mock_openai, mock_index):
        """Cache miss: OpenAI embed + Pinecone query must be called."""
        mock_cache.return_value.get.return_value = None
        mock_openai.return_value.embeddings.create.return_value = _make_openai_embed_response()

        match = _make_pinecone_match(score=0.88)
        mock_index.return_value.query.return_value = MagicMock(matches=[match])

        from api.retriever import retrieve
        result = retrieve("what is the policy?")

        mock_openai.return_value.embeddings.create.assert_called_once()
        mock_index.return_value.query.assert_called_once()
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0].score, 0.88)

    @patch("api.retriever._pinecone_index")
    @patch("api.retriever._openai_client")
    @patch("api.retriever._redis_cache")
    def test_score_threshold_filters_low_matches(self, mock_cache, mock_openai, mock_index):
        """Matches below 0.70 threshold must be excluded unless fallback applies."""
        mock_cache.return_value.get.return_value = None
        mock_openai.return_value.embeddings.create.return_value = _make_openai_embed_response()

        high = _make_pinecone_match(chunk_id="c1", score=0.85)
        low = _make_pinecone_match(chunk_id="c2", score=0.50)
        mock_index.return_value.query.return_value = MagicMock(matches=[high, low])

        from api.retriever import retrieve
        result = retrieve("question?")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].chunk_id, "c1")
        self.assertFalse(result[0].low_confidence)

    @patch("api.retriever._pinecone_index")
    @patch("api.retriever._openai_client")
    @patch("api.retriever._redis_cache")
    def test_low_confidence_fallback_returns_top3(self, mock_cache, mock_openai, mock_index):
        """All scores below threshold: top-3 returned with low_confidence=True."""
        mock_cache.return_value.get.return_value = None
        mock_openai.return_value.embeddings.create.return_value = _make_openai_embed_response()

        matches = [_make_pinecone_match(chunk_id=f"c{i}", score=0.4 - i * 0.05) for i in range(5)]
        mock_index.return_value.query.return_value = MagicMock(matches=matches)

        from api.retriever import retrieve
        result = retrieve("obscure question")

        self.assertEqual(len(result), 3)
        self.assertTrue(all(c.low_confidence for c in result))

    @patch("api.retriever._pinecone_index")
    @patch("api.retriever._openai_client")
    @patch("api.retriever._redis_cache")
    def test_redis_unavailable_still_queries_pinecone(self, mock_cache, mock_openai, mock_index):
        """Redis failure must not prevent retrieval."""
        mock_cache.return_value.get.side_effect = Exception("Redis connection refused")
        mock_openai.return_value.embeddings.create.return_value = _make_openai_embed_response()
        match = _make_pinecone_match(score=0.80)
        mock_index.return_value.query.return_value = MagicMock(matches=[match])

        from api.retriever import retrieve
        result = retrieve("test query")

        self.assertEqual(len(result), 1)

    @patch("api.retriever._pinecone_index")
    @patch("api.retriever._openai_client")
    @patch("api.retriever._redis_cache")
    def test_pinecone_failure_returns_empty(self, mock_cache, mock_openai, mock_index):
        """Pinecone failure after retries → empty list (non-fatal)."""
        mock_cache.return_value.get.return_value = None
        mock_openai.return_value.embeddings.create.return_value = _make_openai_embed_response()
        mock_index.return_value.query.side_effect = Exception("Pinecone timeout")

        from api.retriever import retrieve
        result = retrieve("test query")

        self.assertEqual(result, [])


# ─────────────────────────────────────────────────────────────────────────────
# RAGAgent node unit tests
# ─────────────────────────────────────────────────────────────────────────────

class TestDecomposeQuery(unittest.TestCase):

    @patch("api.agent._llm")
    def test_simple_query_passes_through(self, mock_llm):
        """Single-intent query → sub_queries contains the original query."""
        mock_llm.return_value.invoke.return_value = _make_llm_response("What is the refund policy?")

        from api.agent import decompose_query
        state = {"query": "What is the refund policy?", "sub_queries": [], "chunks": [],
                 "answer": "", "sources": [], "confidence": "no_context", "filters": None}
        result = decompose_query(state)

        self.assertEqual(result["sub_queries"], ["What is the refund policy?"])

    @patch("api.agent._llm")
    def test_multi_part_query_is_split(self, mock_llm):
        """Multi-intent query → sub_queries contains multiple lines."""
        mock_llm.return_value.invoke.return_value = _make_llm_response(
            "What is the refund policy?\nHow long does shipping take?"
        )

        from api.agent import decompose_query
        state = {"query": "What is the refund policy and how long does shipping take?",
                 "sub_queries": [], "chunks": [], "answer": "",
                 "sources": [], "confidence": "no_context", "filters": None}
        result = decompose_query(state)

        self.assertEqual(len(result["sub_queries"]), 2)
        self.assertIn("refund", result["sub_queries"][0])
        self.assertIn("shipping", result["sub_queries"][1])

    @patch("api.agent._llm")
    def test_llm_failure_falls_back_to_original_query(self, mock_llm):
        """LLM failure in decompose → original query used as-is (no crash)."""
        mock_llm.return_value.invoke.side_effect = Exception("LLM timeout")

        from api.agent import decompose_query
        state = {"query": "What is X?", "sub_queries": [], "chunks": [],
                 "answer": "", "sources": [], "confidence": "no_context", "filters": None}
        result = decompose_query(state)

        self.assertEqual(result["sub_queries"], ["What is X?"])


class TestRetrieveContext(unittest.TestCase):

    @patch("api.agent.retrieve")
    def test_deduplicates_chunks_by_chunk_id(self, mock_retrieve):
        """Same chunk_id from multiple sub-queries appears only once."""
        from api.schemas import RetrievedChunk
        chunk = RetrievedChunk(
            chunk_id="doc1_chunk_0", doc_id="doc1", text="text",
            source="file.pdf", score=0.9, chunk_index=0,
        )
        mock_retrieve.return_value = [chunk]

        from api.agent import retrieve_context
        state = {"query": "q", "sub_queries": ["sub1", "sub2"],
                 "chunks": [], "answer": "", "sources": [],
                 "confidence": "no_context", "filters": None}
        result = retrieve_context(state)

        # Two sub-queries but same chunk_id → only one chunk in state
        self.assertEqual(len(result["chunks"]), 1)

    @patch("api.agent.retrieve")
    def test_retrieve_failure_is_non_fatal(self, mock_retrieve):
        """If retrieve throws, the node should not crash."""
        mock_retrieve.return_value.side_effect = Exception("Pinecone error")

        from api.agent import retrieve_context
        state = {"query": "q", "sub_queries": ["sub1"],
                 "chunks": [], "answer": "", "sources": [],
                 "confidence": "no_context", "filters": None}
        result = retrieve_context(state)

        self.assertEqual(result["chunks"], [])


class TestGenerateAnswer(unittest.TestCase):

    def test_no_context_returns_no_context_confidence(self):
        """Empty chunks → no_context confidence, honest answer."""
        from api.agent import generate_answer
        state = {"query": "What is X?", "sub_queries": [], "chunks": [],
                 "answer": "", "sources": [], "confidence": "no_context", "filters": None}
        result = generate_answer(state)

        self.assertEqual(result["confidence"], "no_context")
        self.assertEqual(result["sources"], [])
        self.assertIn("don't have enough", result["answer"].lower())

    @patch("api.agent._llm")
    def test_with_context_returns_high_confidence(self, mock_llm):
        """With chunks above threshold → high confidence, sources populated."""
        mock_llm.return_value.invoke.return_value = _make_llm_response("The answer is 42.")

        from api.agent import generate_answer
        state = {
            "query": "What is X?",
            "sub_queries": [],
            "chunks": [_make_retrieved_chunk_dict()],
            "answer": "",
            "sources": [],
            "confidence": "no_context",
            "filters": None,
        }
        result = generate_answer(state)

        self.assertEqual(result["confidence"], "high")
        self.assertEqual(result["answer"], "The answer is 42.")
        self.assertEqual(len(result["sources"]), 1)

    @patch("api.agent._llm")
    def test_low_confidence_chunks_yield_low_confidence(self, mock_llm):
        """Chunks flagged low_confidence → confidence stays low."""
        mock_llm.return_value.invoke.return_value = _make_llm_response("Maybe it's 42.")

        from api.agent import generate_answer
        state = {
            "query": "What is X?",
            "sub_queries": [],
            "chunks": [_make_retrieved_chunk_dict(low_confidence=True)],
            "answer": "",
            "sources": [],
            "confidence": "no_context",
            "filters": None,
        }
        result = generate_answer(state)

        self.assertEqual(result["confidence"], "low")


class TestValidateAnswer(unittest.TestCase):

    @patch("api.agent._llm")
    def test_ungrounded_verdict_downgrades_confidence(self, mock_llm):
        """UNGROUNDED verdict must downgrade confidence to 'low'."""
        mock_llm.return_value.invoke.return_value = _make_llm_response("UNGROUNDED")

        from api.agent import validate_answer
        state = {
            "query": "q",
            "sub_queries": [],
            "chunks": [_make_retrieved_chunk_dict()],
            "answer": "Some answer with fabricated facts.",
            "sources": [],
            "confidence": "high",
            "filters": None,
        }
        result = validate_answer(state)
        self.assertEqual(result["confidence"], "low")

    @patch("api.agent._llm")
    def test_grounded_verdict_keeps_confidence(self, mock_llm):
        """GROUNDED verdict must leave confidence unchanged."""
        mock_llm.return_value.invoke.return_value = _make_llm_response("GROUNDED")

        from api.agent import validate_answer
        state = {
            "query": "q",
            "sub_queries": [],
            "chunks": [_make_retrieved_chunk_dict()],
            "answer": "The answer is 42.",
            "sources": [],
            "confidence": "high",
            "filters": None,
        }
        result = validate_answer(state)
        self.assertEqual(result["confidence"], "high")

    def test_no_context_skips_validation(self):
        """no_context confidence → validate_answer returns state unchanged."""
        from api.agent import validate_answer
        state = {
            "query": "q",
            "sub_queries": [],
            "chunks": [],
            "answer": "I don't have enough information.",
            "sources": [],
            "confidence": "no_context",
            "filters": None,
        }
        result = validate_answer(state)
        self.assertEqual(result["confidence"], "no_context")


# ─────────────────────────────────────────────────────────────────────────────
# run_rag end-to-end integration test
# ─────────────────────────────────────────────────────────────────────────────

class TestRunRAG(unittest.TestCase):

    @patch("api.agent._llm")
    @patch("api.agent.retrieve")
    def test_full_pipeline_happy_path(self, mock_retrieve, mock_llm):
        """Full graph: decompose → retrieve → generate → validate → answer."""
        from api.schemas import RetrievedChunk

        chunk = RetrievedChunk(
            chunk_id="doc1_chunk_0", doc_id="doc1",
            text="VecturaFlow processes documents using an AI pipeline.",
            source="docs/overview.pdf", score=0.92, chunk_index=0,
        )
        mock_retrieve.return_value = [chunk]

        # LLM calls: decompose, generate, validate
        mock_llm.return_value.invoke.side_effect = [
            _make_llm_response("What does VecturaFlow do?"),   # decompose
            _make_llm_response("VecturaFlow processes documents using AI."),  # generate
            _make_llm_response("GROUNDED"),                    # validate
        ]

        from api.agent import run_rag
        result = run_rag("What does VecturaFlow do?")

        self.assertIn("answer", result)
        self.assertIn("sources", result)
        self.assertIn("confidence", result)
        self.assertEqual(result["confidence"], "high")
        self.assertEqual(len(result["sources"]), 1)

    @patch("api.agent._llm")
    @patch("api.agent.retrieve")
    def test_no_context_returns_no_context_response(self, mock_retrieve, mock_llm):
        """No matching chunks → no_context answer, empty sources."""
        mock_retrieve.return_value = []
        mock_llm.return_value.invoke.return_value = _make_llm_response("Unknown query")

        from api.agent import run_rag
        result = run_rag("Something completely off-topic")

        self.assertEqual(result["confidence"], "no_context")
        self.assertEqual(result["sources"], [])


# ─────────────────────────────────────────────────────────────────────────────
# API endpoint integration test
# ─────────────────────────────────────────────────────────────────────────────

class TestChatCompletionsEndpoint(unittest.TestCase):

    def setUp(self):
        """Patch run_rag and DynamoDB key lookup before importing the app."""
        self._run_rag_patcher = patch(
            "api.main.run_rag",
            return_value={
                "answer": "VecturaFlow is a RAG platform.",
                "sources": [{"doc_id": "doc1", "source": "overview.pdf", "score": 0.9, "chunk_index": 0}],
                "confidence": "high",
            },
        )
        self._dynamo_patcher = patch("api.dependencies.get_keys_table")
        self.mock_run_rag = self._run_rag_patcher.start()
        self.mock_dynamo = self._dynamo_patcher.start()
        self.mock_dynamo.return_value.get_item.return_value = {"Item": {"api_key": "dev", "key_id": "dev"}}

        from fastapi.testclient import TestClient

        from api.main import app
        self.client = TestClient(app)

    def tearDown(self):
        self._run_rag_patcher.stop()
        self._dynamo_patcher.stop()

    def test_happy_path_returns_200(self):
        """Valid request → 200 with answer and sources."""
        response = self.client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer dev"},
            json={
                "model": "vecturaflow",
                "messages": [{"role": "user", "content": "What is VecturaFlow?"}],
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["choices"][0]["message"]["content"], "VecturaFlow is a RAG platform.")
        self.assertEqual(data["usage"]["confidence"], "high")
        self.assertEqual(len(data["usage"]["sources"]), 1)

    def test_missing_api_key_returns_401(self):
        """No Authorization header → 401."""
        response = self.client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "Hello"}]},
        )
        self.assertEqual(response.status_code, 401)

    def test_rag_pipeline_error_returns_503(self):
        """run_rag raises → 503 Service Unavailable."""
        self.mock_run_rag.side_effect = Exception("LangGraph state error")
        response = self.client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer dev"},
            json={"messages": [{"role": "user", "content": "What is X?"}]},
        )
        self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()
