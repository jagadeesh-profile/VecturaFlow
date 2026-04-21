# VecturaFlow — Claude Code Project Brain

> Read this file first. Every session. Every agent.
> This is the single source of truth for the entire project.
>
> **Cross-editor note:** the same information is mirrored in `graphify/` so that
> any AI agent (Cursor, Aider, Codex, Copilot, Continue, Windsurf) can read it.
> The root `AGENTS.md` points there. If you are Claude Code, this file is still
> your primary brief; `graphify/` exists so non–Claude agents get the same context.
> Regenerate `graphify/modules/*.md` and `graphify/graph.json` after structural
> changes with `python scripts/graphify.py`.

---

## What This Project Is

**VecturaFlow** is an autonomous agentic RAG (Retrieval-Augmented Generation) data platform
built on AWS. It ingests any data source — S3 files, webhooks, Kinesis streams — chunks and
embeds automatically, stores vectors in Pinecone, and exposes an OpenAI-compatible retrieval
API backed by a LangGraph reasoning agent.

**Owner:** Jagadeesh Pamidi — AI Engineer  
**Purpose:** Production portfolio project + interview showcase  
**Status:** Feature-complete. In production-hardening pass — Docker, Terraform,
CI/CD, observability, rate limiting, and MMR reranking all landed.

---

## Project Structure

```
VecturaFlow/
├── CLAUDE.md                  ← YOU ARE HERE — read first every session
├── .claude/
│   ├── agents/                ← 11 specialist agents (read before touching their domain)
│   │   ├── file-ingestion-agent.md
│   │   ├── parser-agent.md
│   │   ├── chunking-agent.md
│   │   ├── webhook-ingestion-agent.md
│   │   ├── embedding-agent.md
│   │   ├── retriever-agent.md
│   │   ├── rag-agent.md
│   │   ├── query-handler-agent.md
│   │   ├── infra-deploy-agent.md
│   │   ├── test-agent.md
│   │   └── demo-agent.md
│   └── memory/                ← shared state between agents and sessions
│       ├── architecture.md    ← tech decisions, ADRs, rationale
│       ├── sprint-status.md   ← what's done, what's next, blockers
│       ├── env-config.md      ← all env vars, AWS resource names
│       └── known-issues.md    ← bugs found, fixes applied, risks
├── api/                       ← FastAPI service (QueryHandlerAgent domain)
│   ├── main.py                ← app entry, routes, middleware
│   ├── config.py              ← pydantic-settings — ONLY place to read env
│   ├── schemas.py             ← all Pydantic v2 request/response models
│   ├── dependencies.py        ← FastAPI DI: auth, DynamoDB clients
│   └── logger.py              ← structlog JSON logger — use everywhere
├── ingestion/                 ← ingest pipeline Lambdas
│   ├── models.py              ← TextBlock, Chunk dataclasses
│   ├── lambda_s3.py           ← FileIngestionAgent Lambda
│   ├── parser.py              ← ParserAgent: PDF/DOCX/CSV/TXT/JSON
│   ├── chunker.py             ← ChunkingAgent: chunk per block (metadata-preserving)
│   └── lambda_parser.py      ← SQS consumer: parser → chunker → embed queue
├── embeddings/                ← EmbeddingAgent Lambda (not built yet)
├── infra/                     ← Dockerfile, ECS task, deploy scripts
├── tests/
│   ├── test_api.py            ← API, auth, schema, Lambda handler tests
│   └── test_ingestion.py      ← parser, chunker, metadata preservation tests
├── poc/
│   ├── poc_runner.py          ← 5-test POC validation script
│   └── README.md
├── docs/
│   └── PRD.md                 ← full product requirements document
├── scripts/
│   ├── setup_aws.py           ← idempotent AWS resource provisioning
│   ├── setup_pinecone.py      ← idempotent Pinecone index creation
│   └── validate_env.py        ← run once before starting to verify all connections
├── .env.example               ← copy to .env and fill in
├── requirements.txt           ← pinned dependencies
├── pyproject.toml             ← Python project config, ruff, pytest
└── Makefile                   ← all commands: install, dev, test, poc, deploy
```

---

## Tech Stack — Locked Decisions

| Layer | Technology | Version | Notes |
|---|---|---|---|
| API | FastAPI | 0.111.0 | async, Pydantic v2 native |
| Validation | Pydantic v2 | 2.7.1 | use model_dump() not dict() |
| Agent orchestration | LangGraph | 0.1.1 | StateGraph with TypedDict |
| LLM chains | LangChain | 0.2.1 | wrappers only, not LCEL |
| Embeddings | OpenAI text-embedding-3-small | — | 1536-dim, cosine metric |
| Generation | GPT-4o mini | — | temperature=0 for RAG |
| Vector DB | Pinecone serverless | 3.2.2 | free tier, us-east-1 |
| Queue | AWS SQS | — | FIFO between every pipeline stage |
| Registry | AWS DynamoDB | — | on-demand, GSI on status+ingested_at |
| Cache | Redis | 5.0.4 | ElastiCache in prod, localhost in dev |
| Logging | structlog | 24.1.0 | JSON in prod, console in dev |
| Testing | pytest + moto | 8.2.0 / 5.0.6 | moto mocks all AWS |
| Deploy | ECS Fargate | — | Pending |
| Container | Docker | — | python:3.11-slim base |
| Linting | ruff | — | enforced via pyproject.toml |

---

## Agent Map — Who Owns What

Before touching any file, check which agent owns that domain.
Read the agent file first. Follow its workflow exactly.

| Domain | Agent File | Files It Owns |
|---|---|---|
| S3 file detection | `file-ingestion-agent.md` | `ingestion/lambda_s3.py` |
| Parsing docs | `parser-agent.md` | `ingestion/parser.py` |
| Chunking text | `chunking-agent.md` | `ingestion/chunker.py`, `ingestion/models.py` |
| Webhook ingestion | `webhook-ingestion-agent.md` | `ingestion/lambda_webhook.py` (pending) |
| Embedding vectors | `embedding-agent.md` | `embeddings/lambda_embed.py` (next) |
| Vector retrieval | `retriever-agent.md` | `api/retriever.py` (pending) |
| RAG reasoning | `rag-agent.md` | `api/agent.py` (pending) |
| API endpoint | `query-handler-agent.md` | `api/main.py`, `api/schemas.py`, `api/dependencies.py` |
| AWS deployment | `infra-deploy-agent.md` | `infra/`, `Dockerfile` (pending) |
| Testing | `test-agent.md` | `tests/` |
| Demo | `demo-agent.md` | `scripts/demo.py` (pending) |

---

## Coding Rules — Non-Negotiable

### Imports
```python
# ALWAYS import settings from api.config — NEVER use os.environ directly
from api.config import settings

# ALWAYS import logger from api.logger — NEVER use print() in production code
from api.logger import logger

# ALWAYS use shared data models from ingestion.models
from ingestion.models import TextBlock, Chunk
```

### AWS clients
```python
# Define at MODULE LEVEL, not inside handlers — reused across Lambda warm starts
_dynamo = boto3.resource("dynamodb", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
_sqs = boto3.client("sqs", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
```

### Chunking — CRITICAL
```python
# ALWAYS chunk per block (NOT per document)
# Chunking the full document text loses page/section metadata
# Each chunk MUST know its page number for source citations to work
for block in blocks:
    sub_chunks = splitter.split_text(block.text)
    # carry block.page, block.section onto every sub_chunk
```

### Error handling pattern
```python
# Every Lambda handler: catch per-record, never fail the whole batch
for record in event["Records"]:
    try:
        process(record)
    except Exception as exc:
        logger.error("handler.failed", error=str(exc), exc_info=True)
        batch_item_failures.append({"itemIdentifier": record["messageId"]})
return {"batchItemFailures": batch_item_failures}
```

### Pydantic v2
```python
# v2 syntax — use these, not v1 equivalents
model.model_dump()          # not model.dict()
model.model_validate(data)  # not model.parse_obj(data)
Field(..., min_length=1)    # not min_items
```

### Tests
```python
# ALL AWS calls must be mocked with moto — never hit real AWS in tests
@mock_aws
def test_something():
    # set up mocked resources here
    ...

# Set env vars BEFORE importing any app module
os.environ["OPENAI_API_KEY"] = "sk-test"
# then import
from api.main import app
```

---

## Environment Variables — Complete Map

All variables loaded via `api/config.py` (pydantic-settings).  
Never add a new env var without adding it to `config.py` AND `.env.example` AND `memory/env-config.md`.

| Variable | Used By | Required | Default |
|---|---|---|---|
| `OPENAI_API_KEY` | EmbeddingAgent, RetrieverAgent, RAGAgent | YES | — |
| `PINECONE_API_KEY` | EmbeddingAgent, RetrieverAgent | YES | — |
| `PINECONE_INDEX` | EmbeddingAgent, RetrieverAgent | YES | `vecturaflow` |
| `PINECONE_REGION` | setup_pinecone.py, EmbeddingAgent | YES | `us-east-1` |
| `AWS_DEFAULT_REGION` | all Lambda + boto3 clients | YES | `us-east-1` |
| `AWS_ACCESS_KEY_ID` | boto3 auth | YES (local) | — |
| `AWS_SECRET_ACCESS_KEY` | boto3 auth | YES (local) | — |
| `INGESTION_BUCKET` | FileIngestionAgent | YES | — |
| `INGESTION_QUEUE_URL` | FileIngestionAgent → SQS | YES | — |
| `EMBEDDING_QUEUE_URL` | ChunkingAgent → SQS | YES | — |
| `REGISTRY_TABLE` | All pipeline agents | YES | `vecturaflow-registry` |
| `KEYS_TABLE` | QueryHandlerAgent auth | YES | `vecturaflow-keys` |
| `REDIS_HOST` | RetrieverAgent cache | NO | `localhost` |
| `REDIS_PORT` | RetrieverAgent cache | NO | `6379` |
| `API_ENV` | FastAPI, logging | NO | `development` |
| `API_DEBUG` | FastAPI, logging format | NO | `false` |
| `CHUNK_SIZE` | ChunkingAgent | NO | `512` |
| `CHUNK_OVERLAP` | ChunkingAgent | NO | `50` |
| `RETRIEVAL_TOP_K` | RetrieverAgent | NO | `5` |
| `RETRIEVAL_SCORE_THRESHOLD` | RetrieverAgent | NO | `0.70` |
| `EMBEDDING_MODEL` | EmbeddingAgent | NO | `text-embedding-3-small` |
| `GENERATION_MODEL` | RAGAgent | NO | `gpt-4o-mini` |

---

## Build Status

| Component            | Status | Files                                                   |
|----------------------|--------|---------------------------------------------------------|
| API scaffold         | Done   | `api/`                                                  |
| S3 ingestion Lambda  | Done   | `ingestion/lambda_s3.py`                                |
| Parser + Chunker     | Done   | `ingestion/parser.py`, `ingestion/chunker.py`           |
| Embedding pipeline   | Done   | `embeddings/lambda_embed.py`                            |
| Retrieval + MMR      | Done   | `api/retriever.py`                                      |
| LangGraph RAGAgent   | Done   | `api/agent.py`                                          |
| Webhook ingestion    | Done   | `ingestion/lambda_webhook.py`                           |
| Rate limiting        | Done   | `api/rate_limit.py`                                     |
| Observability        | Done   | `api/observability.py` (Prometheus + /healthz + /readyz)|
| Docker + compose     | Done   | `Dockerfile`, `docker-compose.yml` (with LocalStack)    |
| Terraform IaC        | Done   | `infra/terraform/`                                      |
| CI/CD                | Done   | `.github/workflows/ci.yml`, `deploy.yml`                |
| Tests                | Done   | 107 tests / 79%+ coverage in `tests/`                   |
| README + docs        | Done   | `README.md`, `docs/ARCHITECTURE.md`, `docs/API.md`, `docs/OPERATIONS.md` |
| Demo script          | Pending | `scripts/demo.py`                                      |

**Next:** Polish `scripts/demo.py` for a Loom-ready end-to-end walkthrough.

---

## Known Bugs Fixed

| Bug | Where | Fix Applied |
|---|---|---|
| ChunkingAgent loses page metadata | `ingestion/chunker.py` | Chunk per block, not per document. Fixed. |
| RAGAgent decompose_query is a stub | `api/agent.py` | Not built yet — implement real LLM decompose when building api/agent.py |
| DemoAgent uses DynamoDB scan() | `scripts/demo.py` | Use GSI query() — DynamoDB table has status GSI |
| WebhookAgent doc_id collision risk | `ingestion/lambda_webhook.py` | Use uuid4() not SHA256(source+timestamp) |

---

## How to Run

```bash
# First time only
cp .env.example .env           # fill in your keys
make install                   # pip install -r requirements.txt
make setup-aws                 # create S3, SQS, DynamoDB
make setup-pinecone            # create Pinecone index
python scripts/validate_env.py # verify all connections green

# Every session
source .venv/bin/activate
make dev                       # start FastAPI at localhost:8000

# Test
make test                      # run current day tests
make test-all                  # run all tests

# POC validation
make poc-local                 # skip AWS, test embed + LangGraph + FastAPI
make poc                       # full POC including Pinecone
```

---

## Data Flow Reference

```
FILE INGESTION:
S3 upload → lambda_s3.py (FileIngestionAgent)
  → SQS ingestion queue
  → lambda_parser.py (ParserAgent + ChunkingAgent)
  → SQS embedding queue
  → lambda_embed.py (EmbeddingAgent) (not built yet)
  → Pinecone + DynamoDB

QUERY:
POST /v1/chat/completions → api/main.py (QueryHandlerAgent)
  → api/agent.py (RAGAgent) (pending)
  → api/retriever.py (RetrieverAgent) (pending)
  → Pinecone search + Redis cache
  → GPT-4o mini generation
  → OpenAI-compatible JSON response
```

---

## Pinecone Index Schema

```
Index name:  vecturaflow
Dimension:   1536  (text-embedding-3-small)
Metric:      cosine
Cloud:       aws / us-east-1

Vector metadata fields:
  doc_id        string   SHA256(bucket/key)
  source        string   S3 key or webhook source
  text          string   chunk content (truncated to 500 chars)
  chunk_index   number   global position within document
  file_type     string   pdf | docx | csv | txt | json
  page          number   PDF page number (when available)
  section       string   DOCX heading / section (when available)
```

---

## DynamoDB Tables

```
vecturaflow-registry
  PK: doc_id (String)
  GSI: status-ingested_at-index  ← ALWAYS use this for status queries, never scan()
  Status values: ingestion_started | chunked | embedded | empty_file | parse_failed

vecturaflow-keys
  PK: api_key (String)
  Dev key: "dev" (pre-seeded by setup_aws.py)
```

---

## Contacts & Links

- Author: Jagadeesh Pamidi — jagadeesh6187@gmail.com
- GitHub: push to public repo when complete
- LinkedIn: post with Loom demo when complete
- AWS Region: us-east-1 (all resources)
- Pinecone Region: us-east-1
