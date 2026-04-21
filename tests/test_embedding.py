"""
VecturaFlow — EmbeddingAgent tests.

Covers:
- Successful batch embed + Pinecone upsert + DynamoDB update
- OpenAI rate-limit retry with eventual success
- OpenAI total failure → all records returned as batchItemFailures
- Pinecone failure after retries → S3 fallback + all records failed
- DynamoDB failure is non-fatal (vectors still upserted)
- Partial batch: one bad JSON record → that record fails, rest succeed
- Metadata fields (page, section) propagated correctly to Pinecone vector
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
os.environ.setdefault("INGESTION_BUCKET", "vecturaflow-test-bucket")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_sqs_record(chunk: dict, message_id: str = "msg-001") -> dict:
    return {
        "messageId": message_id,
        "body": json.dumps(chunk),
        "receiptHandle": "handle-" + message_id,
    }


def _make_chunk(
    doc_id: str = "doc123",
    chunk_id: str = "doc123_chunk_0",
    text: str = "Hello world chunk text.",
    chunk_index: int = 0,
    page: int | None = 1,
    section: str | None = "Introduction",
) -> dict:
    chunk: dict = {
        "chunk_id": chunk_id,
        "doc_id": doc_id,
        "text": text,
        "source": "docs/report.pdf",
        "chunk_index": chunk_index,
        "total_chunks": 3,
        "file_type": "pdf",
    }
    if page is not None:
        chunk["page"] = page
    if section is not None:
        chunk["section"] = section
    return chunk


def _fake_embedding(dim: int = 1536) -> list[float]:
    return [0.01] * dim


def _make_openai_response(texts: list[str]) -> MagicMock:
    """Return a mock that mimics openai.embeddings.create() response."""
    mock_resp = MagicMock()
    mock_resp.data = [
        MagicMock(embedding=_fake_embedding()) for _ in texts
    ]
    return mock_resp


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestEmbeddingHandlerSuccess(unittest.TestCase):

    @patch("embeddings.lambda_embed._cw")
    @patch("embeddings.lambda_embed._registry")
    @patch("embeddings.lambda_embed._pinecone_index")
    @patch("embeddings.lambda_embed._openai_client")
    def test_single_chunk_happy_path(self, mock_openai, mock_index, mock_registry, mock_cw):
        """Single chunk: embed, upsert, update DynamoDB — no failures."""
        chunk = _make_chunk()
        event = {"Records": [_make_sqs_record(chunk, "msg-001")]}

        mock_openai.return_value.embeddings.create.return_value = _make_openai_response([chunk["text"]])

        from embeddings.lambda_embed import handler
        result = handler(event, {})

        self.assertEqual(result["batchItemFailures"], [])

        # OpenAI called once with the text
        mock_openai.return_value.embeddings.create.assert_called_once()
        call_kwargs = mock_openai.return_value.embeddings.create.call_args
        self.assertIn(chunk["text"], call_kwargs.kwargs.get("input", call_kwargs.args[0] if call_kwargs.args else []))

        # Pinecone upserted once
        mock_index.return_value.upsert.assert_called_once()
        upserted_vectors = mock_index.return_value.upsert.call_args.kwargs.get("vectors", [])
        self.assertEqual(len(upserted_vectors), 1)
        self.assertEqual(upserted_vectors[0]["id"], chunk["chunk_id"])
        self.assertEqual(len(upserted_vectors[0]["values"]), 1536)

        # DynamoDB updated
        mock_registry.return_value.update_item.assert_called_once()

    @patch("embeddings.lambda_embed._cw")
    @patch("embeddings.lambda_embed._registry")
    @patch("embeddings.lambda_embed._pinecone_index")
    @patch("embeddings.lambda_embed._openai_client")
    def test_batch_of_three_chunks(self, mock_openai, mock_index, mock_registry, mock_cw):
        """Three chunks from same doc: one OpenAI call, one Pinecone upsert."""
        chunks = [
            _make_chunk(chunk_id=f"doc123_chunk_{i}", chunk_index=i, page=i + 1)
            for i in range(3)
        ]
        records = [_make_sqs_record(c, f"msg-{i:03d}") for i, c in enumerate(chunks)]
        event = {"Records": records}

        texts = [c["text"] for c in chunks]
        mock_openai.return_value.embeddings.create.return_value = _make_openai_response(texts)

        from embeddings.lambda_embed import handler
        result = handler(event, {})

        self.assertEqual(result["batchItemFailures"], [])
        mock_openai.return_value.embeddings.create.assert_called_once()
        upserted = mock_index.return_value.upsert.call_args.kwargs.get("vectors", [])
        self.assertEqual(len(upserted), 3)

    @patch("embeddings.lambda_embed._cw")
    @patch("embeddings.lambda_embed._registry")
    @patch("embeddings.lambda_embed._pinecone_index")
    @patch("embeddings.lambda_embed._openai_client")
    def test_metadata_page_and_section_included(self, mock_openai, mock_index, mock_registry, mock_cw):
        """Page and section metadata must be present on the Pinecone vector."""
        chunk = _make_chunk(page=5, section="Results")
        event = {"Records": [_make_sqs_record(chunk)]}
        mock_openai.return_value.embeddings.create.return_value = _make_openai_response([chunk["text"]])

        from embeddings.lambda_embed import handler
        handler(event, {})

        vector = mock_index.return_value.upsert.call_args.kwargs["vectors"][0]
        self.assertEqual(vector["metadata"]["page"], 5)
        self.assertEqual(vector["metadata"]["section"], "Results")

    @patch("embeddings.lambda_embed._cw")
    @patch("embeddings.lambda_embed._registry")
    @patch("embeddings.lambda_embed._pinecone_index")
    @patch("embeddings.lambda_embed._openai_client")
    def test_metadata_text_truncated_to_500(self, mock_openai, mock_index, mock_registry, mock_cw):
        """Text stored in Pinecone metadata must be ≤ 500 characters."""
        long_text = "x" * 2000
        chunk = _make_chunk(text=long_text)
        event = {"Records": [_make_sqs_record(chunk)]}
        mock_openai.return_value.embeddings.create.return_value = _make_openai_response([long_text])

        from embeddings.lambda_embed import handler
        handler(event, {})

        vector = mock_index.return_value.upsert.call_args.kwargs["vectors"][0]
        self.assertLessEqual(len(vector["metadata"]["text"]), 500)

    @patch("embeddings.lambda_embed._cw")
    @patch("embeddings.lambda_embed._registry")
    @patch("embeddings.lambda_embed._pinecone_index")
    @patch("embeddings.lambda_embed._openai_client")
    def test_dynamo_failure_is_nonfatal(self, mock_openai, mock_index, mock_registry, mock_cw):
        """DynamoDB failure must NOT cause the record to fail — vectors already in Pinecone."""
        chunk = _make_chunk()
        event = {"Records": [_make_sqs_record(chunk)]}
        mock_openai.return_value.embeddings.create.return_value = _make_openai_response([chunk["text"]])
        mock_registry.return_value.update_item.side_effect = Exception("DynamoDB timeout")

        from embeddings.lambda_embed import handler
        result = handler(event, {})

        # Record should NOT be in failures
        self.assertEqual(result["batchItemFailures"], [])
        # Pinecone still called
        mock_index.return_value.upsert.assert_called_once()


class TestEmbeddingHandlerRetries(unittest.TestCase):

    @patch("embeddings.lambda_embed.time")
    @patch("embeddings.lambda_embed._cw")
    @patch("embeddings.lambda_embed._registry")
    @patch("embeddings.lambda_embed._pinecone_index")
    @patch("embeddings.lambda_embed._openai_client")
    def test_openai_rate_limit_retries_then_succeeds(
        self, mock_openai, mock_index, mock_registry, mock_cw, mock_time
    ):
        """Rate limit on first two attempts, success on third."""
        from openai import RateLimitError

        chunk = _make_chunk()
        event = {"Records": [_make_sqs_record(chunk)]}

        mock_openai.return_value.embeddings.create.side_effect = [
            RateLimitError("rate limit", response=MagicMock(status_code=429), body={}),
            RateLimitError("rate limit", response=MagicMock(status_code=429), body={}),
            _make_openai_response([chunk["text"]]),
        ]

        from embeddings.lambda_embed import handler
        result = handler(event, {})

        self.assertEqual(result["batchItemFailures"], [])
        self.assertEqual(mock_openai.return_value.embeddings.create.call_count, 3)

    @patch("embeddings.lambda_embed.time")
    @patch("embeddings.lambda_embed._cw")
    @patch("embeddings.lambda_embed._registry")
    @patch("embeddings.lambda_embed._pinecone_index")
    @patch("embeddings.lambda_embed._openai_client")
    def test_openai_total_failure_fails_all_records(
        self, mock_openai, mock_index, mock_registry, mock_cw, mock_time
    ):
        """OpenAI exhausts all retries → all records returned as batchItemFailures."""
        from openai import RateLimitError

        chunks = [_make_chunk(chunk_id=f"doc123_chunk_{i}", chunk_index=i) for i in range(2)]
        records = [_make_sqs_record(c, f"msg-{i:03d}") for i, c in enumerate(chunks)]
        event = {"Records": records}

        mock_openai.return_value.embeddings.create.side_effect = RateLimitError(
            "rate limit", response=MagicMock(status_code=429), body={}
        )

        from embeddings.lambda_embed import handler
        result = handler(event, {})

        self.assertEqual(len(result["batchItemFailures"]), 2)
        failed_ids = {f["itemIdentifier"] for f in result["batchItemFailures"]}
        self.assertIn("msg-000", failed_ids)
        self.assertIn("msg-001", failed_ids)


class TestEmbeddingHandlerPineconeFailure(unittest.TestCase):

    @patch("embeddings.lambda_embed.time")
    @patch("embeddings.lambda_embed._s3")
    @patch("embeddings.lambda_embed._cw")
    @patch("embeddings.lambda_embed._registry")
    @patch("embeddings.lambda_embed._pinecone_index")
    @patch("embeddings.lambda_embed._openai_client")
    def test_pinecone_failure_saves_to_s3_and_fails_records(
        self, mock_openai, mock_index, mock_registry, mock_cw, mock_s3, mock_time
    ):
        """Pinecone fails all retries → failed chunks written to S3, records failed."""
        chunk = _make_chunk()
        event = {"Records": [_make_sqs_record(chunk, "msg-001")]}
        mock_openai.return_value.embeddings.create.return_value = _make_openai_response([chunk["text"]])
        mock_index.return_value.upsert.side_effect = Exception("Pinecone connection error")

        from embeddings.lambda_embed import handler
        result = handler(event, {})

        self.assertEqual(len(result["batchItemFailures"]), 1)
        self.assertEqual(result["batchItemFailures"][0]["itemIdentifier"], "msg-001")

        # S3 fallback called
        mock_s3.return_value.put_object.assert_called_once()
        s3_call_kwargs = mock_s3.return_value.put_object.call_args.kwargs
        self.assertIn("failed-chunks/", s3_call_kwargs["Key"])


class TestEmbeddingHandlerEdgeCases(unittest.TestCase):

    def test_empty_records_returns_no_failures(self):
        """Empty SQS event should return cleanly with no failures."""
        from embeddings.lambda_embed import handler
        result = handler({"Records": []}, {})
        self.assertEqual(result["batchItemFailures"], [])

    @patch("embeddings.lambda_embed._cw")
    @patch("embeddings.lambda_embed._registry")
    @patch("embeddings.lambda_embed._pinecone_index")
    @patch("embeddings.lambda_embed._openai_client")
    def test_bad_json_record_fails_only_that_record(
        self, mock_openai, mock_index, mock_registry, mock_cw
    ):
        """One unparseable record → only that record fails, others succeed."""
        good_chunk = _make_chunk()
        bad_record = {
            "messageId": "msg-bad",
            "body": "NOT VALID JSON {{{",
            "receiptHandle": "handle-bad",
        }
        good_record = _make_sqs_record(good_chunk, "msg-good")
        event = {"Records": [bad_record, good_record]}

        mock_openai.return_value.embeddings.create.return_value = _make_openai_response([good_chunk["text"]])

        from embeddings.lambda_embed import handler
        result = handler(event, {})

        failed_ids = {f["itemIdentifier"] for f in result["batchItemFailures"]}
        self.assertIn("msg-bad", failed_ids)
        self.assertNotIn("msg-good", failed_ids)

    @patch("embeddings.lambda_embed._cw")
    @patch("embeddings.lambda_embed._registry")
    @patch("embeddings.lambda_embed._pinecone_index")
    @patch("embeddings.lambda_embed._openai_client")
    def test_deduplicates_dynamo_updates_per_doc_id(
        self, mock_openai, mock_index, mock_registry, mock_cw
    ):
        """Multiple chunks from same doc_id → DynamoDB updated only once."""
        chunks = [
            _make_chunk(chunk_id=f"doc123_chunk_{i}", chunk_index=i)
            for i in range(3)
        ]
        records = [_make_sqs_record(c, f"msg-{i:03d}") for i, c in enumerate(chunks)]
        event = {"Records": records}

        texts = [c["text"] for c in chunks]
        mock_openai.return_value.embeddings.create.return_value = _make_openai_response(texts)

        from embeddings.lambda_embed import handler
        handler(event, {})

        # All 3 chunks belong to "doc123" — DynamoDB should be called only once
        self.assertEqual(mock_registry.return_value.update_item.call_count, 1)


if __name__ == "__main__":
    unittest.main()
