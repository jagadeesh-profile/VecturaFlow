"""Tests for /metrics, /healthz, /readyz endpoints."""
from __future__ import annotations

import os

os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("PINECONE_API_KEY", "pc-test")
os.environ.setdefault("PINECONE_INDEX", "vecturaflow-test")
os.environ.setdefault("REGISTRY_TABLE", "vecturaflow-registry")
os.environ.setdefault("KEYS_TABLE", "vecturaflow-keys")
os.environ.setdefault("INGESTION_BUCKET", "vecturaflow-test-bucket")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("INGESTION_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/test/ing")
os.environ.setdefault("EMBEDDING_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/test/emb")

from fastapi.testclient import TestClient
import pytest

from api.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_healthz_returns_ok(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_readyz_green_when_settings_populated(client):
    r = client.get("/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["ready"] is True
    assert body["checks"]["openai_api_key"] is True
    assert body["checks"]["pinecone_api_key"] is True


def test_metrics_endpoint_returns_prometheus_format(client):
    # Issue some traffic so a counter ticks
    client.get("/health")
    client.get("/healthz")
    r = client.get("/metrics")
    assert r.status_code == 200
    body = r.text
    # core metric families we defined
    assert "vecturaflow_http_requests_total" in body
    assert "vecturaflow_http_request_duration_seconds" in body
    assert "vecturaflow_rag_queries_total" in body
