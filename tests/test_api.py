"""
VecturaFlow — API tests.
Covers: API health, auth, schema validation, Lambda ingestion handler.
All AWS calls mocked with moto — no real AWS needed.
"""
from __future__ import annotations

import hashlib
import os

from moto import mock_aws
import pytest

# ── Set dummy env vars before any app import ──────────────────────────────────
os.environ.update({
    "OPENAI_API_KEY": "sk-test",
    "PINECONE_API_KEY": "pcsk-test",
    "PINECONE_INDEX": "vecturaflow-test",
    "PINECONE_REGION": "us-east-1",
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_ACCESS_KEY_ID": "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",
    "AWS_SECURITY_TOKEN": "testing",
    "AWS_SESSION_TOKEN": "testing",
    "INGESTION_BUCKET": "test-bucket",
    "INGESTION_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/000000000000/vecturaflow-ingestion",
    "EMBEDDING_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/000000000000/vecturaflow-embedding",
    "REGISTRY_TABLE": "vecturaflow-registry-test",
    "KEYS_TABLE": "vecturaflow-keys-test",
    "API_ENV": "development",
    "API_DEV_BYPASS": "true",
    "API_DEBUG": "true",
})


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def aws_resources():
    """Creates real mocked AWS resources for each test."""
    with mock_aws():
        import boto3
        region = "us-east-1"

        # DynamoDB tables
        dynamo = boto3.resource("dynamodb", region_name=region)
        dynamo.create_table(
            TableName="vecturaflow-registry-test",
            KeySchema=[{"AttributeName": "doc_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "doc_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        dynamo.create_table(
            TableName="vecturaflow-keys-test",
            KeySchema=[{"AttributeName": "api_key_hash", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "api_key_hash", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        # SQS queue
        sqs = boto3.client("sqs", region_name=region)
        sqs.create_queue(QueueName="vecturaflow-ingestion")

        yield


@pytest.fixture
def api_client(aws_resources):
    """FastAPI test client."""
    from httpx import ASGITransport, AsyncClient

    from api.main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ─────────────────────────────────────────────────────────────────────────────
# API tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health(api_client):
    async with api_client as client:
        r = await client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "version" in data


@pytest.mark.asyncio
async def test_chat_no_auth_header(api_client):
    async with api_client as client:
        r = await client.post("/v1/chat/completions", json={
            "messages": [{"role": "user", "content": "test"}]
        })
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_chat_invalid_key(api_client):
    async with api_client as client:
        r = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer invalid-key-xyz"},
            json={"messages": [{"role": "user", "content": "test"}]},
        )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_chat_dev_key_succeeds(api_client):
    async with api_client as client:
        r = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer dev"},
            json={"messages": [{"role": "user", "content": "What is VecturaFlow?"}]},
        )
    assert r.status_code == 200
    data = r.json()
    assert "choices" in data
    assert len(data["choices"]) == 1
    assert data["choices"][0]["message"]["role"] == "assistant"
    assert len(data["choices"][0]["message"]["content"]) > 0


@pytest.mark.asyncio
async def test_chat_empty_messages_rejected(api_client):
    async with api_client as client:
        r = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer dev"},
            json={"messages": []},
        )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_chat_no_user_message_rejected(api_client):
    async with api_client as client:
        r = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer dev"},
            json={"messages": [{"role": "system", "content": "You are helpful."}]},
        )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_response_is_openai_compatible(api_client):
    async with api_client as client:
        r = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer dev"},
            json={"messages": [{"role": "user", "content": "ping"}]},
        )
    data = r.json()
    assert "id" in data
    assert data["id"].startswith("chatcmpl-")
    assert data["object"] == "chat.completion"
    assert "created" in data
    assert "usage" in data


@pytest.mark.asyncio
async def test_list_models(api_client):
    async with api_client as client:
        r = await client.get("/v1/models", headers={"Authorization": "Bearer dev"})
    assert r.status_code == 200
    data = r.json()
    assert data["object"] == "list"
    assert any(m["id"] == "vecturaflow" for m in data["data"])


@pytest.mark.asyncio
async def test_api_key_lookup_uses_sha256_hash(aws_resources):
    import boto3

    from api.config import Settings
    from api.dependencies import _get_dynamodb_resource, verify_api_key

    api_key = "vf_live_secret"
    api_key_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    table = boto3.resource("dynamodb", region_name="us-east-1").Table("vecturaflow-keys-test")
    table.put_item(Item={"api_key_hash": api_key_hash, "key_id": "live-key", "owner": "test", "revoked": False})

    _get_dynamodb_resource.cache_clear()
    settings = Settings(api_env="production", api_dev_bypass=False)

    item = await verify_api_key(authorization=f"Bearer {api_key}", settings=settings)

    assert item["key_id"] == "live-key"
    assert item["api_key_hash"] == api_key_hash
    assert "api_key" not in item


@pytest.mark.asyncio
async def test_dev_key_requires_explicit_bypass_flag(aws_resources):
    from fastapi import HTTPException

    from api.config import Settings
    from api.dependencies import _get_dynamodb_resource, verify_api_key

    _get_dynamodb_resource.cache_clear()
    settings = Settings(api_env="development", api_dev_bypass=False)

    with pytest.raises(HTTPException) as exc:
        await verify_api_key(authorization="Bearer dev", settings=settings)

    assert exc.value.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# Lambda ingestion handler tests
# ─────────────────────────────────────────────────────────────────────────────

@mock_aws
def test_lambda_processes_pdf():
    import boto3
    region = "us-east-1"
    dynamo = boto3.resource("dynamodb", region_name=region)
    dynamo.create_table(
        TableName="vecturaflow-registry-test",
        KeySchema=[{"AttributeName": "doc_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "doc_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    sqs = boto3.client("sqs", region_name=region)
    queue = sqs.create_queue(QueueName="vecturaflow-ingestion")
    os.environ["INGESTION_QUEUE_URL"] = queue["QueueUrl"]

    import importlib

    import ingestion.lambda_s3 as m
    importlib.reload(m)  # reload so module-level clients pick up mocked AWS

    event = {"Records": [{"s3": {"bucket": {"name": "test-bucket"}, "object": {"key": "docs/report.pdf"}}}]}
    result = m.handler(event, None)

    assert result["processed"] == 1
    assert result["failed"] == 0


@mock_aws
def test_lambda_skips_unsupported_type():
    import boto3
    region = "us-east-1"
    dynamo = boto3.resource("dynamodb", region_name=region)
    dynamo.create_table(
        TableName="vecturaflow-registry-test",
        KeySchema=[{"AttributeName": "doc_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "doc_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    sqs = boto3.client("sqs", region_name=region)
    queue = sqs.create_queue(QueueName="vecturaflow-ingestion")
    os.environ["INGESTION_QUEUE_URL"] = queue["QueueUrl"]

    import importlib

    import ingestion.lambda_s3 as m
    importlib.reload(m)

    event = {"Records": [{"s3": {"bucket": {"name": "test-bucket"}, "object": {"key": "image.png"}}}]}
    result = m.handler(event, None)

    assert result["skipped"] == 1
    assert result["processed"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# Schema tests
# ─────────────────────────────────────────────────────────────────────────────

def test_chat_request_requires_user_message():
    from pydantic import ValidationError

    from api.schemas import ChatMessage, ChatRequest, Role
    with pytest.raises(ValidationError, match="user"):
        ChatRequest(messages=[ChatMessage(role=Role.system, content="You are helpful.")])


def test_doc_id_is_deterministic():
    from ingestion.lambda_s3 import _make_doc_id
    assert _make_doc_id("bucket", "key") == _make_doc_id("bucket", "key")
    assert _make_doc_id("bucket", "key1") != _make_doc_id("bucket", "key2")


def test_doc_id_format():
    from ingestion.lambda_s3 import _make_doc_id
    doc_id = _make_doc_id("my-bucket", "folder/file.pdf")
    assert len(doc_id) == 64  # SHA256 hex = 64 chars
    assert doc_id == hashlib.sha256(b"my-bucket/folder/file.pdf").hexdigest()
