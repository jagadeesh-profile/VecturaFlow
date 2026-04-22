# VecturaFlow

**Production-grade autonomous RAG platform on AWS — OpenAI-compatible, LangGraph-powered, ECS Fargate-deployed.**

[![CI](https://img.shields.io/badge/CI-passing-brightgreen)](.github/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-79%25-brightgreen)](#testing)
[![Python](https://img.shields.io/badge/python-3.11-blue)](pyproject.toml)
[![License](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)
[![Deploy](https://img.shields.io/badge/deploy-ECS%20Fargate-orange)](infra/terraform)

VecturaFlow ingests any data source — S3 uploads, webhooks, streaming events — chunks and
embeds the content automatically, stores vectors in Pinecone, and exposes an
**OpenAI-compatible** retrieval API backed by a **LangGraph reasoning agent**. It is designed,
tested, and packaged the way a real production service is: typed config, structured logs,
Prometheus metrics, health probes, rate limiting, partial-batch SQS DLQs, immutable container
images, and Terraform-managed infrastructure with GitHub Actions CI/CD.

> Swap `OPENAI_BASE_URL` in an existing OpenAI SDK call and you're now asking **your own
> documents** instead of the public web. No client code changes.

---

## Why this exists

Most RAG examples on the internet are Jupyter notebooks. This repo is the other thing —
what you actually need to ship: async FastAPI, LangGraph state machine, exponential-backoff
retries, module-level lazy AWS clients, moto-backed tests, multi-stage Docker image running
as non-root under tini, ECS service with ALB + autoscaling target tracking, OIDC-federated
CD, Prometheus scrape endpoint, and a rate limiter protecting the LLM spend.

If you're evaluating me as an engineer: read `api/agent.py`, `api/retriever.py`,
`ingestion/lambda_parser.py`, `infra/terraform/main.tf`, and
`.github/workflows/deploy.yml`. Those five files show how I think about production systems.

---

## Architecture

```
                 ┌─────────────────────────────────────────────────────────────┐
                 │                         CLIENT                              │
                 │  (OpenAI SDK pointed at https://api.vecturaflow.example)    │
                 └──────────────────────────┬──────────────────────────────────┘
                                            │  Bearer <api_key>
                                            ▼
            ┌───────────────────────────────────────────────────────────────┐
            │  Application Load Balancer  →  ECS Fargate (2-8 tasks, auto)  │
            │  ┌─────────────────────────────────────────────────────────┐  │
            │  │  FastAPI (uvicorn, 2 workers)                            │  │
            │  │  ├─ Auth middleware        → DynamoDB keys table         │  │
            │  │  ├─ Token-bucket limiter   (60 rpm / key, in-memory)     │  │
            │  │  ├─ /v1/chat/completions   → LangGraph RAGAgent          │  │
            │  │  ├─ /metrics               → Prometheus (custom registry)│  │
            │  │  └─ /healthz  /readyz                                    │  │
            │  └──────────────────────────┬───────────────────────────────┘  │
            └─────────────────────────────┼──────────────────────────────────┘
                                          │
                    ┌─────────────────────┼──────────────────────┐
                    ▼                     ▼                      ▼
          ┌──────────────────┐  ┌──────────────────┐   ┌──────────────────┐
          │ RetrieverAgent   │  │ RAGAgent         │   │ Redis            │
          │ OpenAI embed →   │  │ LangGraph 4-node │   │ 5-min query cache│
          │ Pinecone → MMR   │  │ decompose →      │   │ (ElastiCache)    │
          │ rerank → top-K   │  │ retrieve →       │   └──────────────────┘
          └────────┬─────────┘  │ generate →       │
                   ▼            │ validate         │
          ┌──────────────────┐  └──────────────────┘
          │ Pinecone (1536d, │
          │  cosine,         │                ┌──────── INGESTION ─────────┐
          │  serverless)     │◄──────────────▶│                            │
          └──────────────────┘                │  S3 upload                 │
                   ▲                          │     │                      │
                   │                          │     ▼                      │
          ┌──────────────────┐                │  FileIngestionAgent (λ)    │
          │ DynamoDB         │                │     │                      │
          │  registry (GSI)  │                │     ▼                      │
          │  keys            │                │  SQS ingestion queue → DLQ │
          └──────────────────┘                │     │                      │
                                              │     ▼                      │
                                              │  ParserAgent + ChunkingAg. │
                                              │  (PDF/DOCX/CSV/TXT/JSON)   │
                                              │     │                      │
                                              │     ▼                      │
                                              │  SQS embedding queue → DLQ │
                                              │     │                      │
                                              │     ▼                      │
                                              │  EmbeddingAgent (λ)        │
                                              │  → OpenAI embed → Pinecone │
                                              └────────────────────────────┘
```

A Mermaid rendering lives at [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## What's in the box

| Capability                            | How it's done                                                                                         |
|---------------------------------------|-------------------------------------------------------------------------------------------------------|
| OpenAI-compatible `/v1/chat/completions` | Pydantic v2 schema matches the public OpenAI shape; `usage` is extended with `sources` and `confidence`. |
| LangGraph reasoning                    | 4-node `StateGraph`: decompose → retrieve → generate → validate. Deterministic (`temperature=0`).    |
| Retrieval quality                      | Over-fetch 20 candidates, MMR rerank (λ=0.5) for diversity, score threshold, low-confidence fallback. |
| Source citations                       | Every answer carries `doc_id`, `source`, `score`, and `chunk_index`. Page numbers preserved through chunking. |
| Idempotent ingestion                   | SHA-256 `doc_id`, DynamoDB conditional writes, partial-batch SQS responses (`batchItemFailures`).    |
| Document parsing                       | PDF (PyMuPDF), DOCX (python-docx), CSV (pandas + `on_bad_lines="skip"`), TXT, JSON. Metadata-preserving chunking. |
| Rate limiting                          | Lock-protected in-process token bucket keyed on `key_id` → HTTP 429 + `Retry-After`.                 |
| Auth                                   | Bearer tokens in DynamoDB `keys` table; `dev` bypass only when `API_ENV=development`.                |
| Observability                          | structlog JSON logs with request-ID binding; Prometheus counters, histograms, and gauges exposed at `/metrics`. |
| Health                                 | `/health` (ECS liveness), `/healthz` (k8s alias), `/readyz` (asserts required settings).             |
| Container                              | Multi-stage `python:3.11-slim-bookworm`, non-root `app` user, tini as PID 1, healthcheck baked in.  |
| Infrastructure                         | Terraform: VPC with public/private subnets, ALB (HTTPS), ECS Fargate + autoscaling, ECR (immutable tags), S3 + SQS + DLQs, DynamoDB (PITR). |
| CI/CD                                  | GitHub Actions: ruff → pytest (coverage gate) → Trivy → Docker buildx → OIDC deploy → smoke test.    |
| Local dev                              | `docker-compose up` spins LocalStack (S3+SQS+DynamoDB) + Redis + the API. No AWS account required.   |
| Tests                                  | 102 tests, **79% line coverage**, all AWS calls mocked via `moto`, CI enforces 70% floor.            |

---

## Quick start

### Run everything locally (no AWS account needed)

```bash
docker-compose up --build
# API at http://localhost:8000 — dev Bearer key is "dev"
```

LocalStack provisions the S3 bucket, SQS queues, and DynamoDB tables; the `setup`
one-shot container seeds the dev API key.

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer dev" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"What is VecturaFlow?"}]}'
```

### Run the API directly

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # fill in OPENAI_API_KEY and PINECONE_API_KEY
make setup-aws                    # idempotent: S3 + SQS + DynamoDB in your AWS acct
make setup-pinecone               # idempotent: serverless index creation
make dev                          # uvicorn at :8000, reload on, /docs enabled
```

### Run the tests

```bash
make test          # fast subset
make test-all      # full suite (102 tests)
make lint          # ruff
```

## Health Check

Run the full pipeline verification with one command:

```bash
make check-all
```

This runs, in order: `preflight` → `pinecone-stats` → `verify`.

### First-run checklist

| Step | Expected | If it fails |
|------|----------|-------------|
| `make preflight` | Masked key prints, OpenAI returns `ok` | `OPENAI_API_KEY` missing, placeholder, or invalid — check `.env` and shell cache |
| `make pinecone-stats` | `total_vector_count > 0`, correct index name | Ingestion ran but vectors didn't land — check Pinecone index name and environment vars |
| `make verify` (in-corpus Q) | Matches with score > 0.75, answer contains PDF specifics | Embedding model mismatch between ingestion and query, or index incomplete |
| `make verify` (out-of-corpus Q) | Answer: "Not found in the provided documents" | Prompt construction issue — model is answering from weights, not context |

### Common causes when `check-all` fails at pinecone-stats or verify

- Wrong Pinecone index name in config
- Stale environment variables (shell or Lambda)
- Deployment still pointing at an old Lambda image
- Embedding model changed between ingest time and query time (dimension mismatch)

### Individual commands

- `make preflight` — validate OpenAI key only
- `make pinecone-stats` — inspect vector index
- `make verify` — default 3-question RAG test
- `make verify-q Q="your question"` — ask a custom question
- `make triage` — dry-run queue triage
- `make triage-apply` — apply drain/reprocess/DLQ decisions

If any step fails, see [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) for failure patterns and fixes per step.

## Operations

See [docs/RUN_LOG.md](docs/RUN_LOG.md) for the canonical build, verify, queue triage, and calibration command sequence.

### Deploy to AWS

```bash
cd infra/terraform
terraform init
terraform apply -var="environment=prod" -var="domain_name=api.vecturaflow.example"
# CI/CD from then on: push to main → GitHub Actions → ECR → ECS rolling deploy
```

See [`infra/terraform/README.md`](infra/terraform/README.md) for the full variable list,
OIDC setup, and rollback procedure.

---

## API reference

### `POST /v1/chat/completions`

OpenAI-compatible. Any OpenAI SDK works by setting `base_url` to this server.

**Request**
```jsonc
{
  "model": "vecturaflow",                           // ignored — always uses the RAG pipeline
  "messages": [
    {"role": "system",  "content": "Be concise."},
    {"role": "user",    "content": "What was Q3 revenue?"}
  ],
  "temperature": 0.0,
  "filters": {"source": "q3-report.pdf"}            // optional Pinecone metadata filter
}
```

**Response** — identical to OpenAI's shape except `usage` carries citations and confidence:
```jsonc
{
  "id": "chatcmpl-a1b2c3d4e5f6",
  "object": "chat.completion",
  "created": 1745280000,
  "model": "vecturaflow",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "Q3 revenue was $14.2M, up 23% YoY..."},
    "finish_reason": "stop"
  }],
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "sources": [
      {"doc_id": "sha256:ab12...", "source": "q3-report.pdf", "score": 0.89, "chunk_index": 12}
    ],
    "confidence": "high",
    "latency_ms": 420
  }
}
```

**Error codes**

| Code | Meaning                                       |
|------|-----------------------------------------------|
| 401  | Missing, malformed, or revoked API key        |
| 422  | Malformed body or no `role: user` message     |
| 429  | Rate limit exceeded (`Retry-After: 30`)       |
| 503  | RAG pipeline unavailable (retry with backoff) |

### Other endpoints

| Method | Path                   | Purpose                                      |
|--------|------------------------|----------------------------------------------|
| GET    | `/v1/models`           | OpenAI-compatible model listing              |
| GET    | `/health`              | ECS liveness                                 |
| GET    | `/healthz`             | Kubernetes-style liveness alias              |
| GET    | `/readyz`              | Readiness — required settings are populated  |
| GET    | `/metrics`             | Prometheus scrape (custom registry)          |
| GET    | `/docs`                | Swagger UI (dev only)                        |

Full OpenAPI + a deeper tour lives at [`docs/API.md`](docs/API.md).

---

## Testing

```
tests/
├── test_api.py               FastAPI routes, auth, schema validation
├── test_ingestion.py         Parser + chunker, metadata preservation
├── test_embedding.py         Embedding Lambda, OpenAI retry, DLQ behaviour
├── test_rag.py               LangGraph 4-node flow, confidence branching
├── test_webhook.py           Webhook ingestion, partial-batch failures
├── test_observability.py     /metrics, /healthz, /readyz
├── test_rate_limit.py        Token bucket + integration 429
└── test_retriever_mmr.py     MMR rerank + cosine similarity
```

Every external call is mocked: AWS via `moto`, OpenAI/Pinecone/Redis via `unittest.mock`.
The suite finishes in under 35 seconds and CI fails the build below 70% coverage.

---

## Project structure

```
VecturaFlow/
├── api/                        FastAPI service (QueryHandlerAgent)
│   ├── main.py                    routes, middleware, lifespan
│   ├── agent.py                   LangGraph 4-node RAGAgent
│   ├── retriever.py               embed → Pinecone → MMR rerank (Redis-cached)
│   ├── rate_limit.py              token-bucket limiter + FastAPI dependency
│   ├── observability.py           Prometheus registry + middleware
│   ├── config.py                  pydantic-settings — the only env reader
│   ├── dependencies.py            auth + DynamoDB client singletons
│   ├── schemas.py                 Pydantic v2 request/response models
│   └── logger.py                  structlog JSON config
├── ingestion/                  Lambda workers
│   ├── lambda_s3.py               S3 event → ingestion queue (idempotent)
│   ├── lambda_parser.py           parser + chunker → embedding queue
│   ├── lambda_webhook.py          API Gateway → embedding queue
│   ├── parser.py                  PDF/DOCX/CSV/TXT/JSON → TextBlock[]
│   ├── chunker.py                 metadata-preserving chunking (per block)
│   └── models.py                  TextBlock, Chunk dataclasses
├── embeddings/
│   └── lambda_embed.py            SQS → OpenAI embed → Pinecone upsert
├── infra/
│   ├── Dockerfile                 multi-stage, non-root, tini
│   └── terraform/                 VPC, ALB, ECS, ECR, S3, SQS, DynamoDB
├── .github/workflows/
│   ├── ci.yml                     lint + test + Trivy + image build
│   └── deploy.yml                 OIDC → ECR → ECS rolling deploy → smoke test
├── tests/                      102 tests, 79% coverage
├── scripts/
│   ├── setup_aws.py               idempotent resource provisioning
│   ├── setup_pinecone.py          idempotent index creation
│   ├── validate_env.py            run once to verify all connections
│   └── demo.py                    end-to-end walkthrough for Loom
├── docker-compose.yml          LocalStack + Redis + API
├── Makefile                    install / dev / test / lint / smoke / deploy
├── requirements.txt
└── pyproject.toml              ruff + pytest config
```

---

## Design decisions worth reading

Each of these has a one-page ADR in `.claude/memory/architecture.md`:

- **Why LangGraph over LCEL** — explicit state you can inspect and test; chains hide too much.
- **Why chunk per `TextBlock` (not per document)** — losing page/section metadata kills citations.
- **Why MMR over pure score sort** — near-duplicate top-K makes the LLM regurgitate, not synthesise.
- **Why lazy `@lru_cache` clients** — unit tests must import the module with dummy creds.
- **Why partial-batch SQS responses** — one poison-pill message shouldn't fail 9 healthy ones.
- **Why DynamoDB with a GSI on `status`** — `Scan()` is a sin in production.
- **Why an in-process limiter (for now)** — ElastiCache-backed exact limits are a follow-up,
  documented as a known-limitation in `CLAUDE.md`.

---

## Operational runbooks

- [`docs/OPERATIONS.md`](docs/OPERATIONS.md) — deploys, rollbacks, DLQ replay, cost controls.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — Mermaid diagrams, sequence flows, data model.
- [`docs/API.md`](docs/API.md) — full OpenAPI walkthrough with curl + Python SDK examples.

---

## License

MIT. See [`LICENSE`](LICENSE).

## Author

**Jagadeesh Pamidi** — AI Engineer
[email](mailto:jagadeesh6187@gmail.com) · [GitHub](https://github.com/jagadeesh-pamidi) · [LinkedIn](https://www.linkedin.com/in/jagadeesh-pamidi/)
