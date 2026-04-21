"""Tests for api.rate_limit — token-bucket limiter."""
from __future__ import annotations

import os

os.environ.update({
    "OPENAI_API_KEY": "sk-test",
    "PINECONE_API_KEY": "pcsk-test",
    "PINECONE_INDEX": "vecturaflow-test",
    "PINECONE_REGION": "us-east-1",
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_ACCESS_KEY_ID": "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",
    "INGESTION_BUCKET": "test-bucket",
    "INGESTION_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/000000000000/vecturaflow-ingestion",
    "EMBEDDING_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/000000000000/vecturaflow-embedding",
    "REGISTRY_TABLE": "vecturaflow-registry-test",
    "KEYS_TABLE": "vecturaflow-keys-test",
    "API_ENV": "development",
    "API_DEBUG": "true",
})

from moto import mock_aws
import pytest

from api.rate_limit import TokenBucketLimiter

# ─────────────────────────────────────────────────────────────────────────────
# Unit tests for TokenBucketLimiter
# ─────────────────────────────────────────────────────────────────────────────

def test_limiter_allows_up_to_capacity():
    limiter = TokenBucketLimiter(capacity=3, period_seconds=60)
    assert limiter.allow("k") is True
    assert limiter.allow("k") is True
    assert limiter.allow("k") is True
    assert limiter.allow("k") is False


def test_limiter_is_per_key():
    limiter = TokenBucketLimiter(capacity=2, period_seconds=60)
    assert limiter.allow("alice") is True
    assert limiter.allow("alice") is True
    assert limiter.allow("alice") is False
    # bob's bucket is independent
    assert limiter.allow("bob") is True
    assert limiter.allow("bob") is True
    assert limiter.allow("bob") is False


def test_limiter_refills_over_time(monkeypatch):
    """Advance monotonic clock and confirm tokens refill proportionally."""
    import api.rate_limit as mod

    t = [1000.0]

    def fake_monotonic():
        return t[0]

    monkeypatch.setattr(mod.time, "monotonic", fake_monotonic)

    limiter = TokenBucketLimiter(capacity=2, period_seconds=60)  # refill = 1 token / 30s
    assert limiter.allow("x") is True
    assert limiter.allow("x") is True
    assert limiter.allow("x") is False

    t[0] += 31  # one token refilled
    assert limiter.allow("x") is True
    assert limiter.allow("x") is False


# ─────────────────────────────────────────────────────────────────────────────
# Integration test — rate limit surfaces as HTTP 429 on /v1/chat/completions
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def aws_resources():
    """Spin up mocked DynamoDB + SQS and seed the dev API key."""
    with mock_aws():
        import boto3

        dynamo = boto3.resource("dynamodb", region_name="us-east-1")
        dynamo.create_table(
            TableName="vecturaflow-registry-test",
            KeySchema=[{"AttributeName": "doc_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "doc_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        keys_table = dynamo.create_table(
            TableName="vecturaflow-keys-test",
            KeySchema=[{"AttributeName": "api_key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "api_key", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        keys_table.put_item(Item={"api_key": "dev", "key_id": "dev-key", "owner": "test", "revoked": False})
        yield


def test_chat_endpoint_returns_429_when_bucket_exhausted(aws_resources, monkeypatch):
    """Tighten the limiter to 2 rpm and assert the 3rd request returns 429."""
    from fastapi.testclient import TestClient

    # Tighten the global limiter so the test is fast.
    import api.rate_limit as rl
    original_limiter = rl._limiter
    rl._limiter = rl.TokenBucketLimiter(capacity=2, period_seconds=60)

    # Stub run_rag so we don't hit OpenAI / Pinecone.
    from api import main as main_mod

    def fake_run_rag(query, filters=None):  # noqa: ARG001
        return {"answer": "pong", "sources": [], "confidence": "high"}

    monkeypatch.setattr(main_mod, "run_rag", fake_run_rag)

    try:
        client = TestClient(main_mod.app)
        headers = {"Authorization": "Bearer dev"}
        body = {"messages": [{"role": "user", "content": "hi"}]}

        r1 = client.post("/v1/chat/completions", headers=headers, json=body)
        r2 = client.post("/v1/chat/completions", headers=headers, json=body)
        r3 = client.post("/v1/chat/completions", headers=headers, json=body)

        assert r1.status_code == 200, r1.text
        assert r2.status_code == 200, r2.text
        assert r3.status_code == 429, r3.text
        assert r3.headers.get("Retry-After") == "30"
        payload = r3.json()
        assert payload["detail"]["code"] == "too_many_requests"
    finally:
        rl._limiter = original_limiter
