# VecturaFlow — API Reference

VecturaFlow exposes an OpenAI-compatible surface so any existing OpenAI SDK works by
redirecting `base_url` at this server. Auth is a Bearer token; every request must carry one
(the `dev` key is valid only when `API_ENV=development`).

---

## Authentication

```
Authorization: Bearer <api_key>
```

Keys live in the DynamoDB `keys` table. A missing, malformed, empty, or revoked key all
return `401` with the OpenAI-style error body:

```json
{
  "error": {
    "message": "Invalid API key",
    "type": "authentication_error",
    "code": "invalid_key"
  }
}
```

---

## Endpoints

### `POST /v1/chat/completions`

OpenAI-compatible chat completion backed by the VecturaFlow RAG pipeline.

**Request body**

| Field         | Type                    | Required | Notes                                                |
|---------------|-------------------------|----------|------------------------------------------------------|
| `model`       | string                  | no       | Always interpreted as `vecturaflow`; other values accepted but ignored. |
| `messages`    | array of `ChatMessage`  | yes      | Must contain at least one message with `role="user"`. |
| `stream`      | boolean                 | no       | Currently must be `false` — SSE streaming is a follow-up. |
| `temperature` | float `[0.0, 2.0]`      | no       | Forwarded to the generator. Default `0.0`.           |
| `max_tokens`  | int `[1, 4096]`         | no       | Cap on generator output.                             |
| `filters`     | object                  | no       | Pinecone metadata filters, e.g. `{"source": "q3.pdf"}`. |

A `ChatMessage` is `{"role": "system|user|assistant", "content": "<str 1..32000>"}`.

**Response body**

OpenAI-shape with `usage` extended:

```jsonc
{
  "id": "chatcmpl-a1b2c3d4e5f6",
  "object": "chat.completion",
  "created": 1745280000,
  "model": "vecturaflow",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "<grounded answer>"},
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "sources": [
      {"doc_id": "sha256:abc123...", "source": "q3-report.pdf", "score": 0.89, "chunk_index": 12}
    ],
    "confidence": "high",
    "latency_ms": 420
  }
}
```

`usage.confidence` is one of:

- `high` — all retrieved chunks cleared the score threshold.
- `low` — below-threshold fallback; answer is speculative.
- `no_context` — nothing was retrieved; response says so politely.

**Errors**

| Status | `error.code`            | Meaning                                              |
|--------|-------------------------|------------------------------------------------------|
| 401    | `missing_key`           | No `Authorization` header.                           |
| 401    | `invalid_scheme`        | Header didn't start with `Bearer `.                  |
| 401    | `empty_key`             | Bearer value was whitespace.                         |
| 401    | `invalid_key`           | Key wasn't in the keys table.                        |
| 401    | `revoked_key`           | Key exists but `revoked=true`.                       |
| 422    | `missing_user_message`  | No message with `role="user"` in `messages`.         |
| 422    | —                       | Body failed Pydantic validation (schema mismatch).   |
| 429    | `too_many_requests`     | Token bucket exhausted. `Retry-After: 30` header.    |
| 503    | `503`                   | RAG pipeline had an unrecoverable error. Retry with backoff. |
| 503    | `auth_unavailable`      | DynamoDB lookup failed. Retry with backoff.          |

---

### `GET /v1/models`

OpenAI-compatible model list. Returns:

```json
{
  "object": "list",
  "data": [
    {"id": "vecturaflow", "object": "model", "created": 1700000000, "owned_by": "vecturaflow"}
  ]
}
```

---

### `GET /health` — liveness

Returns 200 as long as the process is running. Used by the ECS task health check.

```json
{"status": "ok", "version": "1.0.0", "env": "production"}
```

### `GET /healthz` — Kubernetes alias

Minimal `{"status": "ok"}` for environments that standardise on k8s-style probes.

### `GET /readyz` — readiness

Returns 200 only when the service has everything it needs to serve a real request. A cheap
sync check — no external network calls.

```json
{
  "ready": true,
  "checks": {
    "openai_api_key": true,
    "pinecone_api_key": true,
    "pinecone_index": true,
    "registry_table": true
  }
}
```

503 if any check is false.

### `GET /metrics` — Prometheus

Scrape target for Prometheus / Grafana. Uses a custom `CollectorRegistry` so it doesn't
collide with other instrumented libraries.

Metric families:

| Name                                             | Type      | Labels                       |
|--------------------------------------------------|-----------|------------------------------|
| `vecturaflow_http_requests_total`                | Counter   | `method`, `path`, `status`   |
| `vecturaflow_http_request_duration_seconds`      | Histogram | `method`, `path`             |
| `vecturaflow_http_in_flight_requests`            | Gauge     | —                            |
| `vecturaflow_rag_queries_total`                  | Counter   | `confidence`                 |
| `vecturaflow_rag_pipeline_duration_seconds`      | Histogram | —                            |
| `vecturaflow_retriever_cache_total`              | Counter   | `outcome` (hit/miss/error)   |

---

## Client examples

### Python — OpenAI SDK redirect

```python
from openai import OpenAI

client = OpenAI(base_url="https://api.vecturaflow.example/v1", api_key="<your-key>")

resp = client.chat.completions.create(
    model="vecturaflow",
    messages=[{"role": "user", "content": "Summarise the Q3 report."}],
)
print(resp.choices[0].message.content)
print("sources:", resp.model_dump()["usage"]["sources"])
```

### curl

```bash
curl -s -X POST https://api.vecturaflow.example/v1/chat/completions \
  -H "Authorization: Bearer $VF_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "messages":[{"role":"user","content":"What was Q3 revenue?"}],
    "filters": {"source":"q3-report.pdf"}
  }' | jq
```

### Rate-limit handling

```python
import time, httpx
r = httpx.post(url, headers=headers, json=payload)
if r.status_code == 429:
    wait = int(r.headers.get("Retry-After", "30"))
    time.sleep(wait)
    r = httpx.post(url, headers=headers, json=payload)
```

---

## Operational expectations

- p50 latency: ~400 ms for high-confidence queries (warm cache + OpenAI + Pinecone).
- p99 latency: ~1.8 s (cold cache + decompose into 2 sub-queries).
- Limit: 60 requests / minute / key (configurable via `RATE_LIMIT_PER_MINUTE`).
- Request-ID: every response carries `X-Request-ID` and `X-Latency-MS` headers. Include the
  request ID when reporting issues.
