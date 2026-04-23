# architecture.md — Tech Stack & Layer Map

## One-screen diagram

```
                   ┌──────────────────────────────────────────────────┐
                   │                      AWS                         │
                   │                                                  │
   S3 upload ─────▶│ λ lambda_s3 ─── SQS ingest ─── λ lambda_parser  │
                   │                                        │        │
                   │                                        ▼        │
                   │                                   SQS embed     │
                   │                                        │        │
                   │                                        ▼        │
                   │                                 λ lambda_embed  │
                   │                                   │        │    │
                   │                                   ▼        ▼    │
                   │                              Pinecone   DynamoDB│
                   │                                                  │
                   │        (registry: vecturaflow-registry           │
                   │         keys:     vecturaflow-keys)              │
                   └──────────────────────────────────────────────────┘
                                       ▲           ▲
                                       │           │
   webhook POST ───▶ λ lambda_webhook ─┘           │
                                                   │
   client ─▶ ALB ─▶ ECS Fargate ─▶ FastAPI (api.main)
                                   │
                                   ├─ api.agent (LangGraph: decompose→retrieve→generate→validate)
                                   ├─ api.retriever (OpenAI embed → Pinecone MMR → Redis cache)
                                   ├─ api.rate_limit (token bucket, per-key)
                                   └─ api.observability (Prometheus /metrics)
```

---

## Locked tech choices

| Layer          | Choice                           | Version  | Why                                              |
|----------------|----------------------------------|----------|--------------------------------------------------|
| API framework  | FastAPI                          | 0.111.0  | Async, Pydantic v2 native, great OpenAPI output. |
| Validation     | Pydantic v2                      | 2.7.1    | `model_dump()` not `dict()`.                     |
| Config         | pydantic-settings                | —        | Single-file env loading; no `os.environ` calls.  |
| Agent graph    | LangGraph                        | 0.1.1    | TypedDict StateGraph. 4 nodes, no cycles.        |
| LLM wrappers   | LangChain (core + openai)        | 0.2.1    | Only `ChatOpenAI` + `HumanMessage`/`SystemMessage`. No LCEL. |
| Embeddings     | OpenAI `text-embedding-3-small`  | —        | 1536-dim, cheap, good recall.                    |
| Generation     | OpenAI `gpt-4o-mini`             | —        | `temperature=0` — deterministic for RAG.         |
| Vector DB      | Pinecone serverless              | 3.2.2    | us-east-1, cosine, 1536-dim.                     |
| Queue          | AWS SQS                          | —        | Standard queues (not FIFO) with DLQs.            |
| Registry       | AWS DynamoDB                     | —        | On-demand. GSI `status-ingested_at-index`.       |
| Auth store     | AWS DynamoDB                     | —        | `vecturaflow-keys` table, PK = `api_key_hash`.   |
| Cache          | Redis                            | 5.0.4    | ElastiCache in prod, localhost in dev. 5-min TTL for retrieval. |
| Logging        | structlog                        | 24.1.0   | JSON in prod, console in dev.                    |
| Metrics        | prometheus_client                | —        | Explicit `CollectorRegistry`, `/metrics` endpoint. |
| Testing        | pytest + pytest-asyncio + moto   | 8.2 / 5.0.6 | 107 tests, 82% coverage. moto mocks all AWS.  |
| Container      | Docker python:3.11-slim + tini   | —        | Non-root user, multi-stage build.                |
| Orchestration  | ECS Fargate behind ALB           | —        | Autoscaling 2→10, CPU-based.                     |
| IaC            | Terraform                        | >= 1.6   | VPC, ALB, ECS, ECR, IAM, Secrets Manager.        |
| CI/CD          | GitHub Actions + OIDC            | —        | ruff + pytest + Trivy + Buildx + ecs-deploy.     |
| Linting        | ruff                             | —        | Replaces flake8 + isort + black. Config in `pyproject.toml`. |

---

## Layered responsibility

```
┌──────────────────────────────────────────────────────────────┐
│ Edge          │ ALB (HTTPS)                                  │
├──────────────────────────────────────────────────────────────┤
│ Service       │ FastAPI (api.main)                           │
│               │  ├─ middleware: CORS, metrics, logging       │
│               │  ├─ auth: api.dependencies.verify_api_key    │
│               │  └─ rate limit: api.rate_limit               │
├──────────────────────────────────────────────────────────────┤
│ Orchestration │ api.agent (LangGraph 4-node)                 │
├──────────────────────────────────────────────────────────────┤
│ Retrieval     │ api.retriever (OpenAI embed + Pinecone MMR)  │
├──────────────────────────────────────────────────────────────┤
│ Cache         │ Redis (5-min TTL on retrieval results)       │
├──────────────────────────────────────────────────────────────┤
│ Persistence   │ Pinecone + DynamoDB                          │
├──────────────────────────────────────────────────────────────┤
│ Ingest        │ S3 → SQS → λ parser → SQS → λ embed          │
├──────────────────────────────────────────────────────────────┤
│ Observability │ structlog JSON → CloudWatch; Prom /metrics   │
└──────────────────────────────────────────────────────────────┘
```

---

## Environment variables (canonical)

Single source: `api/config.py` → `Settings` (pydantic-settings).
Never read `os.environ` anywhere else.

| Name                          | Default               | Owner(s)                    |
|-------------------------------|-----------------------|-----------------------------|
| `OPENAI_API_KEY`              | —                     | retriever, agent            |
| `PINECONE_API_KEY`            | —                     | retriever, embed Lambda     |
| `PINECONE_INDEX`              | `vecturaflow`         | retriever, embed Lambda     |
| `PINECONE_REGION`             | `us-east-1`           | setup_pinecone              |
| `AWS_DEFAULT_REGION`          | `us-east-1`           | every boto3 client          |
| `INGESTION_BUCKET`            | —                     | lambda_s3, lambda_webhook   |
| `INGESTION_QUEUE_URL`         | —                     | lambda_s3, lambda_webhook   |
| `EMBEDDING_QUEUE_URL`         | —                     | chunker                     |
| `REGISTRY_TABLE`              | `vecturaflow-registry`| every pipeline agent        |
| `KEYS_TABLE`                  | `vecturaflow-keys`    | dependencies.verify_api_key |
| `REDIS_HOST` / `REDIS_PORT`   | `localhost` / `6379`  | retriever cache             |
| `API_ENV`                     | `development`         | config.is_production        |
| `API_DEV_BYPASS`              | `false`               | local-only Bearer dev bypass |
| `RETRIEVAL_TOP_K`             | `5`                   | retriever                   |
| `RETRIEVAL_SCORE_THRESHOLD`   | `0.70`                | retriever                   |
| `CHUNK_SIZE` / `CHUNK_OVERLAP`| `512` / `50`          | chunker                     |
| `RATE_LIMIT_PER_MINUTE`       | `60`                  | rate_limit                  |

New env var? Add to **all three**: `config.py`, `.env.example`, this table.
