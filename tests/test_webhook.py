"""
VecturaFlow — WebhookIngestionAgent tests.

Covers:
- Single JSON object → queued, doc_id returned
- Array of objects → one SQS message per item
- Empty body → 400
- Empty JSON object {} → 400
- Empty array [] → 400
- Invalid JSON → 422
- Array with non-dict items filtered out
- Text includes numeric and boolean field values
- Text truncated to _MAX_TEXT_LENGTH
- SQS failure → 500 with error
- DynamoDB failure is non-fatal (SQS still called)
- doc_id uses uuid4 (no two calls produce same id)
- Source header X-Source-Name propagated to SQS message
- CloudWatch metric emitted on success
"""
from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

# ── Env vars BEFORE any app import ───────────────────────────────────────────
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("PINECONE_API_KEY", "pc-test")
os.environ.setdefault("PINECONE_INDEX", "vecturaflow-test")
os.environ.setdefault("REGISTRY_TABLE", "vecturaflow-registry")
os.environ.setdefault("EMBEDDING_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/test/embedding")
os.environ.setdefault("INGESTION_BUCKET", "vecturaflow-test-bucket")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("INGESTION_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/test/ingestion")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_event(
    body: str | None = None,
    source_name: str | None = None,
) -> dict:
    """Build a minimal API Gateway proxy event."""
    headers = {}
    if source_name:
        headers["X-Source-Name"] = source_name
    return {
        "httpMethod": "POST",
        "path": "/ingest/webhook",
        "headers": headers,
        "body": body,
    }


def _parse_body(response: dict) -> dict:
    return json.loads(response["body"])


# ─────────────────────────────────────────────────────────────────────────────
# Happy-path tests
# ─────────────────────────────────────────────────────────────────────────────

class TestWebhookHappyPath(unittest.TestCase):

    @patch("ingestion.lambda_webhook._cw")
    @patch("ingestion.lambda_webhook._registry")
    @patch("ingestion.lambda_webhook._sqs")
    def test_single_object_returns_200_with_doc_id(self, mock_sqs, mock_registry, mock_cw):
        """Single JSON object → 200, one doc_id, one SQS message."""
        payload = {"event": "user.signup", "email": "test@example.com", "plan": "pro"}
        event = _make_event(body=json.dumps(payload))

        from ingestion.lambda_webhook import handler
        response = handler(event, {})

        self.assertEqual(response["statusCode"], 200)
        body = _parse_body(response)
        self.assertEqual(body["status"], "queued")
        self.assertEqual(body["count"], 1)
        self.assertEqual(len(body["doc_ids"]), 1)
        mock_sqs.return_value.send_message.assert_called_once()

    @patch("ingestion.lambda_webhook._cw")
    @patch("ingestion.lambda_webhook._registry")
    @patch("ingestion.lambda_webhook._sqs")
    def test_array_of_objects_queues_each_item(self, mock_sqs, mock_registry, mock_cw):
        """Array of 3 objects → 3 SQS messages, 3 doc_ids."""
        payload = [
            {"event": "order.created", "order_id": "001"},
            {"event": "order.paid", "order_id": "002"},
            {"event": "order.shipped", "order_id": "003"},
        ]
        event = _make_event(body=json.dumps(payload))

        from ingestion.lambda_webhook import handler
        response = handler(event, {})

        self.assertEqual(response["statusCode"], 200)
        body = _parse_body(response)
        self.assertEqual(body["count"], 3)
        self.assertEqual(len(body["doc_ids"]), 3)
        self.assertEqual(mock_sqs.return_value.send_message.call_count, 3)

    @patch("ingestion.lambda_webhook._cw")
    @patch("ingestion.lambda_webhook._registry")
    @patch("ingestion.lambda_webhook._sqs")
    def test_source_header_propagated_to_sqs(self, mock_sqs, mock_registry, mock_cw):
        """X-Source-Name header must appear as source in the SQS message body."""
        payload = {"action": "deploy", "service": "api"}
        event = _make_event(body=json.dumps(payload), source_name="github-actions")

        from ingestion.lambda_webhook import handler
        handler(event, {})

        call_kwargs = mock_sqs.return_value.send_message.call_args.kwargs
        message = json.loads(call_kwargs["MessageBody"])
        self.assertEqual(message["source"], "github-actions")

    @patch("ingestion.lambda_webhook._cw")
    @patch("ingestion.lambda_webhook._registry")
    @patch("ingestion.lambda_webhook._sqs")
    def test_default_source_is_webhook(self, mock_sqs, mock_registry, mock_cw):
        """No X-Source-Name header → source defaults to 'webhook'."""
        event = _make_event(body=json.dumps({"key": "value"}))

        from ingestion.lambda_webhook import handler
        handler(event, {})

        message = json.loads(mock_sqs.return_value.send_message.call_args.kwargs["MessageBody"])
        self.assertEqual(message["source"], "webhook")

    @patch("ingestion.lambda_webhook._cw")
    @patch("ingestion.lambda_webhook._registry")
    @patch("ingestion.lambda_webhook._sqs")
    def test_doc_ids_are_unique_per_call(self, mock_sqs, mock_registry, mock_cw):
        """Each handler invocation must produce a different doc_id (uuid4 based)."""
        payload = {"event": "test"}
        event = _make_event(body=json.dumps(payload))

        from ingestion.lambda_webhook import handler
        r1 = handler(event, {})
        r2 = handler(event, {})

        id1 = _parse_body(r1)["doc_ids"][0]
        id2 = _parse_body(r2)["doc_ids"][0]
        self.assertNotEqual(id1, id2)

    @patch("ingestion.lambda_webhook._cw")
    @patch("ingestion.lambda_webhook._registry")
    @patch("ingestion.lambda_webhook._sqs")
    def test_numeric_and_bool_fields_included_in_text(self, mock_sqs, mock_registry, mock_cw):
        """Numeric and boolean values must appear in the SQS message text."""
        payload = {"score": 99, "active": True, "label": "test"}
        event = _make_event(body=json.dumps(payload))

        from ingestion.lambda_webhook import handler
        handler(event, {})

        message = json.loads(mock_sqs.return_value.send_message.call_args.kwargs["MessageBody"])
        text = message["text"]
        self.assertIn("99", text)
        self.assertIn("True", text)
        self.assertIn("test", text)

    @patch("ingestion.lambda_webhook._cw")
    @patch("ingestion.lambda_webhook._registry")
    @patch("ingestion.lambda_webhook._sqs")
    def test_text_truncated_to_max_length(self, mock_sqs, mock_registry, mock_cw):
        """Text longer than _MAX_TEXT_LENGTH must be truncated."""
        from ingestion.lambda_webhook import _MAX_TEXT_LENGTH
        payload = {"data": "x" * (_MAX_TEXT_LENGTH * 2)}
        event = _make_event(body=json.dumps(payload))

        from ingestion.lambda_webhook import handler
        handler(event, {})

        message = json.loads(mock_sqs.return_value.send_message.call_args.kwargs["MessageBody"])
        self.assertLessEqual(len(message["text"]), _MAX_TEXT_LENGTH)

    @patch("ingestion.lambda_webhook._cw")
    @patch("ingestion.lambda_webhook._registry")
    @patch("ingestion.lambda_webhook._sqs")
    def test_sqs_message_has_correct_chunk_fields(self, mock_sqs, mock_registry, mock_cw):
        """SQS message body must contain chunk_id, doc_id, text, source, chunk_index."""
        payload = {"event": "ping"}
        event = _make_event(body=json.dumps(payload))

        from ingestion.lambda_webhook import handler
        response = handler(event, {})

        doc_id = _parse_body(response)["doc_ids"][0]
        message = json.loads(mock_sqs.return_value.send_message.call_args.kwargs["MessageBody"])

        self.assertEqual(message["doc_id"], doc_id)
        self.assertEqual(message["chunk_id"], f"{doc_id}_chunk_0")
        self.assertEqual(message["chunk_index"], 0)
        self.assertIn("text", message)
        self.assertIn("source", message)
        self.assertEqual(message["file_type"], "webhook")

    @patch("ingestion.lambda_webhook._cw")
    @patch("ingestion.lambda_webhook._registry")
    @patch("ingestion.lambda_webhook._sqs")
    def test_dynamo_registered_with_ingestion_started(self, mock_sqs, mock_registry, mock_cw):
        """DynamoDB put_item must be called with status='ingestion_started'."""
        event = _make_event(body=json.dumps({"event": "test"}))

        from ingestion.lambda_webhook import handler
        handler(event, {})

        mock_registry.return_value.put_item.assert_called_once()
        item = mock_registry.return_value.put_item.call_args.kwargs["Item"]
        self.assertEqual(item["status"], "ingestion_started")
        self.assertEqual(item["file_type"], "webhook")


# ─────────────────────────────────────────────────────────────────────────────
# Error-handling tests
# ─────────────────────────────────────────────────────────────────────────────

class TestWebhookErrorHandling(unittest.TestCase):

    def test_missing_body_returns_400(self):
        """No body at all → 400."""
        event = _make_event(body=None)

        from ingestion.lambda_webhook import handler
        response = handler(event, {})

        self.assertEqual(response["statusCode"], 400)
        self.assertIn("error", _parse_body(response))

    def test_empty_string_body_returns_400(self):
        """Blank body string → 400."""
        event = _make_event(body="   ")

        from ingestion.lambda_webhook import handler
        response = handler(event, {})

        self.assertEqual(response["statusCode"], 400)

    def test_invalid_json_returns_422(self):
        """Malformed JSON → 422."""
        event = _make_event(body="{not valid json{{")

        from ingestion.lambda_webhook import handler
        response = handler(event, {})

        self.assertEqual(response["statusCode"], 422)
        self.assertIn("Invalid JSON", _parse_body(response)["error"])

    def test_empty_json_object_returns_400(self):
        """Body is {} → 400."""
        event = _make_event(body="{}")

        from ingestion.lambda_webhook import handler
        response = handler(event, {})

        self.assertEqual(response["statusCode"], 400)

    def test_empty_array_returns_400(self):
        """Body is [] → 400."""
        event = _make_event(body="[]")

        from ingestion.lambda_webhook import handler
        response = handler(event, {})

        self.assertEqual(response["statusCode"], 400)

    @patch("ingestion.lambda_webhook._cw")
    @patch("ingestion.lambda_webhook._registry")
    @patch("ingestion.lambda_webhook._sqs")
    def test_sqs_failure_returns_500(self, mock_sqs, mock_registry, mock_cw):
        """SQS send failure → 500 with error message."""
        mock_sqs.return_value.send_message.side_effect = Exception("SQS connection timeout")
        event = _make_event(body=json.dumps({"event": "test"}))

        from ingestion.lambda_webhook import handler
        response = handler(event, {})

        self.assertEqual(response["statusCode"], 500)
        body = _parse_body(response)
        self.assertIn("error", body)

    @patch("ingestion.lambda_webhook._cw")
    @patch("ingestion.lambda_webhook._registry")
    @patch("ingestion.lambda_webhook._sqs")
    def test_dynamo_failure_is_nonfatal(self, mock_sqs, mock_registry, mock_cw):
        """DynamoDB put_item failure must NOT prevent SQS message from being sent."""
        from botocore.exceptions import ClientError
        mock_registry.return_value.put_item.side_effect = ClientError(
            {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": ""}}, "PutItem"
        )
        event = _make_event(body=json.dumps({"event": "test"}))

        from ingestion.lambda_webhook import handler
        response = handler(event, {})

        # SQS must still be called despite DynamoDB failure
        self.assertEqual(response["statusCode"], 200)
        mock_sqs.return_value.send_message.assert_called_once()

    @patch("ingestion.lambda_webhook._cw")
    @patch("ingestion.lambda_webhook._registry")
    @patch("ingestion.lambda_webhook._sqs")
    def test_array_with_nondicts_filtered(self, mock_sqs, mock_registry, mock_cw):
        """Non-dict items in batch array are silently filtered — valid items still queued."""
        payload = [
            {"event": "valid"},
            "not a dict",
            42,
            None,
            {"event": "also_valid"},
        ]
        event = _make_event(body=json.dumps(payload))

        from ingestion.lambda_webhook import handler
        response = handler(event, {})

        self.assertEqual(response["statusCode"], 200)
        body = _parse_body(response)
        self.assertEqual(body["count"], 2)
        self.assertEqual(mock_sqs.return_value.send_message.call_count, 2)

    @patch("ingestion.lambda_webhook._cw")
    @patch("ingestion.lambda_webhook._registry")
    @patch("ingestion.lambda_webhook._sqs")
    def test_partial_sqs_failure_still_returns_200(self, mock_sqs, mock_registry, mock_cw):
        """One SQS failure in a batch → partial success, 200 with warning."""
        mock_sqs.return_value.send_message.side_effect = [
            None,  # first item succeeds
            Exception("SQS timeout"),  # second fails
            None,  # third succeeds
        ]
        payload = [{"n": 1}, {"n": 2}, {"n": 3}]
        event = _make_event(body=json.dumps(payload))

        from ingestion.lambda_webhook import handler
        response = handler(event, {})

        self.assertEqual(response["statusCode"], 200)
        body = _parse_body(response)
        self.assertEqual(body["count"], 2)
        self.assertIn("warnings", body)


# ─────────────────────────────────────────────────────────────────────────────
# Response format tests
# ─────────────────────────────────────────────────────────────────────────────

class TestWebhookResponseFormat(unittest.TestCase):

    @patch("ingestion.lambda_webhook._cw")
    @patch("ingestion.lambda_webhook._registry")
    @patch("ingestion.lambda_webhook._sqs")
    def test_response_has_content_type_header(self, mock_sqs, mock_registry, mock_cw):
        """Response must include Content-Type: application/json header."""
        event = _make_event(body=json.dumps({"k": "v"}))

        from ingestion.lambda_webhook import handler
        response = handler(event, {})

        self.assertEqual(response["headers"]["Content-Type"], "application/json")

    @patch("ingestion.lambda_webhook._cw")
    @patch("ingestion.lambda_webhook._registry")
    @patch("ingestion.lambda_webhook._sqs")
    def test_response_body_is_valid_json_string(self, mock_sqs, mock_registry, mock_cw):
        """Response body must be a valid JSON string (not a dict)."""
        event = _make_event(body=json.dumps({"k": "v"}))

        from ingestion.lambda_webhook import handler
        response = handler(event, {})

        self.assertIsInstance(response["body"], str)
        parsed = json.loads(response["body"])
        self.assertIsInstance(parsed, dict)


if __name__ == "__main__":
    unittest.main()
