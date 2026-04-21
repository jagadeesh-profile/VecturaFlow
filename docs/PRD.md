# VecturaFlow — Product Requirements Document

**Version:** 1.0.0  
**Author:** Jagadeesh Pamidi  
**Role:** AI Engineer / Platform Architect  
**Date:** March 2026  
**Status:** Approved — Active Development  

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Problem Statement](#2-problem-statement)
3. [Goals & Non-Goals](#3-goals--non-goals)
4. [Target Users](#4-target-users)
5. [User Stories](#5-user-stories)
6. [System Architecture](#6-system-architecture)
7. [Technical Stack — Decisions & Rationale](#7-technical-stack--decisions--rationale)
8. [Feature Requirements](#8-feature-requirements)
9. [API Specification](#9-api-specification)
10. [Data Models](#10-data-models)
11. [Agent System Design](#11-agent-system-design)
12. [Non-Functional Requirements](#12-non-functional-requirements)
13. [Success Metrics](#13-success-metrics)
14. [Risk Register](#14-risk-register)
15. [Security & Compliance](#15-security--compliance)
16. [Project Timeline — 10-Day Sprint](#16-project-timeline--10-day-sprint)
17. [POC Validation Plan](#17-poc-validation-plan)
18. [Cost Model](#18-cost-model)
19. [Future Roadmap](#19-future-roadmap)
20. [Glossary](#20-glossary)

---

## 1. Executive Summary

**VecturaFlow** is an autonomous, agentic Retrieval-Augmented Generation (RAG) data platform built on AWS. It ingests data from any source — files, APIs, databases, real-time streams — chunks and embeds it automatically, stores vectors in Pinecone, and exposes an OpenAI-compatible retrieval API backed by a LangGraph reasoning agent.

The platform solves a real production problem: enterprises have knowledge locked in PDFs, databases, and data streams that their teams can't query with natural language. VecturaFlow makes that knowledge queryable through a single API endpoint that any client — LangChain, OpenAI SDK, curl — can call without modification.

**Core value proposition:** Drop in any data → get back a queryable AI API in minutes, not months.

---

## 2. Problem Statement

### 2.1 Current State

Organizations accumulate knowledge across hundreds of file formats, databases, and real-time streams. This knowledge is siloed, unsearchable, and inaccessible to AI systems. Building a RAG pipeline from scratch requires:

- Separate ingestion logic for every data source type
- Custom chunking strategies per format
- Embedding pipeline management and cost control
- Vector database setup and maintenance
- LLM orchestration and prompt engineering
- A queryable API layer with auth and monitoring

Most teams spend 6–12 weeks building this infrastructure before writing a single line of business logic.

### 2.2 Root Causes

| Problem | Impact |
|---|---|
| No unified ingestion layer | Each data source requires custom engineering |
| Manual chunking is fragile | Poor chunk quality degrades RAG accuracy by 30–50% |
| No async embedding pipeline | Synchronous embedding blocks ingestion at scale |
| LLM orchestration complexity | Multi-step reasoning requires custom agent code |
| No standard retrieval API | Every client integration requires custom code |

### 2.3 Opportunity

Build a platform that handles all of the above automatically, exposing a single OpenAI-compatible API. Any team with data can have a production RAG system in minutes.

---

## 3. Goals & Non-Goals

### Goals

- **G1** — Ingest any file type (PDF, DOCX, CSV, TXT, JSON) via S3 upload automatically
- **G2** — Accept real-time data via HTTP webhooks and Kinesis streams
- **G3** — Chunk, embed, and index data asynchronously without manual intervention
- **G4** — Expose an OpenAI-compatible `/v1/chat/completions` API
- **G5** — Return answers with source citations and confidence scores
- **G6** — Deploy on AWS with <$120/month cost for MVP scale
- **G7** — Complete end-to-end in 10 days as a portfolio project

### Non-Goals

- **NG1** — Not a general-purpose LLM (answers only from ingested data)
- **NG2** — Not a multi-tenant SaaS (single-tenant for MVP)
- **NG3** — Not a UI product (API-first, no frontend)
- **NG4** — Not a fine-tuning platform (inference only)
- **NG5** — No PII anonymisation in MVP (future roadmap)

---

## 4. Target Users

### Primary: AI Engineers (like the author)

- Building RAG pipelines for enterprise applications
- Need a reusable platform rather than rebuilding from scratch
- Comfort with AWS, Python, FastAPI
- **Jobs-to-be-done:** Ship a working RAG API fast, use it as a portfolio piece

### Secondary: Platform Teams

- Need to expose internal knowledge bases as queryable APIs
- Have existing data in S3, databases, or streams
- Want OpenAI SDK compatibility so existing tooling works unchanged

### Tertiary: Hiring Managers / Technical Interviewers

- Evaluating AI engineering candidates
- Looking for production-grade, end-to-end system design experience
- **Signal:** Complete, deployed, demonstrable system with clean code

---

## 5. User Stories

### Epic 1 — Data Ingestion

```
US-001  As a data engineer, I want to upload a PDF to S3 and have it
        automatically chunked and indexed, so I don't write ingestion code.

US-002  As a developer, I want to POST JSON to a webhook endpoint and have
        it indexed within 30 seconds, so I can ingest real-time events.

US-003  As a platform engineer, I want to connect a Kinesis stream to
        VecturaFlow, so live data is continuously indexed without manual jobs.

US-004  As an operator, I want duplicate files to be silently skipped,
        so re-uploads don't create duplicate vectors.

US-005  As an operator, I want failed ingestions logged with status in
        DynamoDB, so I can identify and retry problem files.
```

### Epic 2 — Querying

```
US-006  As a developer, I want to query VecturaFlow using the OpenAI Python
        SDK without code changes, so I can drop it in as a model replacement.

US-007  As a user, I want answers to include source citations (doc name,
        page), so I can verify the information.

US-008  As a user, I want the system to say "I don't know" when context
        is insufficient, rather than hallucinate an answer.

US-009  As a developer, I want to filter queries by metadata (e.g. source
        file), so I can scope answers to specific documents.
```

### Epic 3 — Operations

```
US-010  As an operator, I want a /health endpoint that returns 200 when
        all dependencies are healthy, so ECS can route traffic correctly.

US-011  As an operator, I want every request logged with latency and
        confidence to CloudWatch, so I can build dashboards.

US-012  As an operator, I want API key authentication so only authorised
        clients can query the system.
```

---

## 6. System Architecture

### 6.1 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        DATA SOURCES                             │
│  S3 Files │ HTTP Webhooks │ Kinesis Streams │ Databases         │
└─────┬─────┴──────┬────────┴───────┬─────────┴──────────────────┘
      │            │                │
      ▼            ▼                ▼
┌─────────────────────────────────────────────────────────────────┐
│                    INGESTION LAYER (AWS Lambda)                  │
│                                                                 │
│  FileIngestionAgent   WebhookAgent   StreamIngestionAgent       │
│        │                   │               │                    │
│        └───────────────────┴───────────────┘                   │
│                            │                                    │
│                     SQS Ingestion Queue                         │
│                            │                                    │
│                      ParserAgent                                │
│              (PDF│DOCX│CSV│TXT│JSON)                            │
│                            │                                    │
│                      ChunkingAgent                              │
│                   (512 chars, 50 overlap)                       │
│                            │                                    │
│                    SQS Embedding Queue                          │
└────────────────────────────┼────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                  EMBEDDING LAYER (AWS Lambda)                    │
│                                                                 │
│               EmbeddingAgent                                    │
│     OpenAI text-embedding-3-small (1536-dim)                    │
│                    │                                            │
│             Pinecone Upsert                                     │
│         + DynamoDB Status Update                                │
└────────────────────────────┼────────────────────────────────────┘
                             │
                    ┌────────┴────────┐
                    │   Pinecone      │   DynamoDB
                    │  Vector Store   │   Registry
                    └────────┬────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│              RETRIEVAL API LAYER (ECS Fargate)                   │
│                                                                 │
│  API Gateway → QueryHandlerAgent (FastAPI)                      │
│                      │                                          │
│               RAGAgent (LangGraph)                              │
│          decompose → retrieve → generate                        │
│                      │                                          │
│               RetrieverAgent                                    │
│         Pinecone search + Redis cache                           │
│                      │                                          │
│              GPT-4o mini generation                             │
│                      │                                          │
│         OpenAI-compatible JSON response                         │
│         + source citations + confidence                         │
└─────────────────────────────────────────────────────────────────┘
```

### 6.2 Data Flow — Batch (Files)

```
S3 Upload
  → Lambda (FileIngestionAgent): validate, dedup, queue
  → Lambda (ParserAgent): download, parse, chunk
  → Lambda (EmbeddingAgent): embed, upsert to Pinecone
  → DynamoDB: status = "embedded"
  → [Queryable in < 60 seconds]
```

### 6.3 Data Flow — Stream (Real-time)

```
Kinesis/Webhook event
  → Lambda (StreamIngestionAgent / WebhookAgent)
  → SQS Embedding Queue (bypass parser — data pre-structured)
  → Lambda (EmbeddingAgent): embed, upsert
  → [Queryable in < 5 seconds]
```

### 6.4 Data Flow — Query

```
POST /v1/chat/completions
  → API Gateway: rate limit, route
  → QueryHandlerAgent: auth, validate, extract query
  → RAGAgent (LangGraph):
      node 1: decompose_query (split complex queries)
      node 2: retrieve_context (Pinecone search via RetrieverAgent)
      node 3: merge_context (dedup, rank top-5)
      node 4: generate_answer (GPT-4o mini)
      node 5: validate_answer (hallucination check)
  → Response: answer + citations + confidence
```

---

## 7. Technical Stack — Decisions & Rationale

### 7.1 Core Stack

| Component | Choice | Rationale | Alternatives Considered |
|---|---|---|---|
| API Framework | FastAPI | Async, Pydantic v2 native, OpenAPI auto-docs, fastest Python framework | Flask (no async), Django (too heavy) |
| Agent Orchestration | LangGraph | Typed state graph, supports cycles, best for multi-step RAG | LangChain LCEL (less transparent), CrewAI (too opaque for debugging) |
| Embedding Model | text-embedding-3-small | Best quality/cost ratio, 1536-dim, $0.02/1M tokens | ada-002 (older), bge-large (requires SageMaker hosting) |
| Vector Store | Pinecone | Managed, serverless, free tier for MVP, fastest query at scale | pgvector (ops overhead), Weaviate (complex setup), ChromaDB (not production-grade) |
| Generation Model | GPT-4o mini | 95% cheaper than GPT-4o, excellent at grounded QA | GPT-4o (10x cost), LLaMA 3 (requires GPU hosting) |
| Message Queue | AWS SQS | Decouples ingestion from embedding, handles bursts, DLQ built-in | Kinesis (overkill for queue), RabbitMQ (self-managed) |
| Registry | AWS DynamoDB | Serverless, on-demand pricing, GSI for status queries, no ops | RDS (requires VPC, always-on cost), Redis (volatile) |
| Cache | Redis (ElastiCache) | Sub-millisecond query cache, cuts LLM calls 30-50% on repeated queries | DynamoDB (too slow for cache), in-memory (doesn't survive restarts) |
| Compute — API | ECS Fargate | Containerised, no server management, scales to zero-ish | EC2 (ops overhead), Lambda (cold start for API), App Runner (less control) |
| Compute — Pipelines | AWS Lambda | Serverless, event-driven, pay-per-invocation, perfect for pipeline stages | ECS (always-on cost for bursty workloads) |
| Observability | CloudWatch | Native AWS, no extra setup, structured logs + metrics + alarms | Datadog (cost), Grafana (self-managed) |

### 7.2 Key Architectural Decisions

**ADR-001: Async SQS-based pipeline over synchronous chain**
- Decision: Use SQS between every pipeline stage
- Rationale: Decouples stages, enables independent scaling, prevents cascade failures. If embedding is slow, ingestion doesn't back up.
- Tradeoff: Higher latency (seconds vs milliseconds) but far more resilient

**ADR-002: Chunk per block, not per document**
- Decision: ChunkingAgent chunks each parsed TextBlock independently
- Rationale: Preserves page/section metadata on every chunk. Without this, source citations lose their page number.
- Tradeoff: Slightly more SQS messages, but metadata fidelity is non-negotiable for production RAG

**ADR-003: OpenAI-compatible API surface**
- Decision: Mirror `/v1/chat/completions` request/response exactly
- Rationale: Any client already using OpenAI SDK works with zero code changes. Drop-in replacement.
- Tradeoff: Must maintain OpenAI API shape compatibility as they evolve it

**ADR-004: LangGraph over vanilla LangChain for RAG**
- Decision: Use LangGraph StateGraph for RAG pipeline
- Rationale: Typed state, explicit node transitions, debuggable, supports conditional edges for future routing
- Tradeoff: More boilerplate than LCEL, but production systems need the transparency

---

## 8. Feature Requirements

### 8.1 P0 — Must Have (MVP)

| ID | Feature | Acceptance Criteria |
|---|---|---|
| F-001 | S3 file ingestion | PDF/DOCX/CSV/TXT upload → indexed in Pinecone within 60s |
| F-002 | Webhook ingestion | POST /ingest/webhook → indexed within 10s |
| F-003 | Text chunking | Chunks ≤ 512 chars, overlap 50, page metadata preserved |
| F-004 | OpenAI embedding | text-embedding-3-small, batch upsert to Pinecone |
| F-005 | RAG query endpoint | POST /v1/chat/completions returns grounded answer with sources |
| F-006 | API key auth | Invalid key → 401; valid key → response |
| F-007 | Health endpoint | GET /health → 200 with version and env |
| F-008 | Deduplication | Same file uploaded twice → second upload silently skipped |
| F-009 | No-context response | Insufficient context → "I don't have enough information" |
| F-010 | Source citations | Every answer includes doc_id, source filename, similarity score |

### 8.2 P1 — Should Have (Sprint)

| ID | Feature | Acceptance Criteria |
|---|---|---|
| F-011 | Redis query cache | Repeated identical queries served from cache in <10ms |
| F-012 | DynamoDB doc registry | Every doc has status tracking: ingestion_started → embedded |
| F-013 | Confidence scoring | Response includes confidence: high / low / no_context |
| F-014 | Metadata filters | Query with `filters: {source: "file.pdf"}` scopes to that doc |
| F-015 | CloudWatch logging | Every request logged with latency, confidence, key_id |
| F-016 | Docker + ECS deploy | `make deploy` → live HTTPS endpoint on AWS |
| F-017 | E2E demo script | `python scripts/demo.py` → full pipeline demo in <2 min |

### 8.3 P2 — Nice to Have (Post-Sprint)

| ID | Feature | Notes |
|---|---|---|
| F-018 | Kinesis stream ingestion | Real-time stream consumer |
| F-019 | Hybrid search (BM25 + vector) | OpenSearch integration for keyword-heavy queries |
| F-020 | Reranking | Cohere reranker for improved result quality |
| F-021 | Multi-tenant namespacing | Pinecone namespace per tenant_id |
| F-022 | Embedding drift monitoring | W&B + RAGAS evaluation pipeline |
| F-023 | Admin dashboard | Ingestion status, query analytics, vector count |

---

## 9. API Specification

### 9.1 `POST /v1/chat/completions`

**Request**
```json
{
  "model": "vecturaflow",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "What are the key findings in the Q3 report?"}
  ],
  "filters": {"source": "q3-report.pdf"}
}
```

**Response 200**
```json
{
  "id": "chatcmpl-a1b2c3d4",
  "object": "chat.completion",
  "created": 1711234567,
  "model": "vecturaflow",
  "choices": [{
    "index": 0,
    "message": {
      "role": "assistant",
      "content": "The Q3 report highlights a 23% revenue increase driven by..."
    },
    "finish_reason": "stop"
  }],
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "sources": [
      {"doc_id": "abc123", "source": "q3-report.pdf", "score": 0.92, "chunk_index": 14},
      {"doc_id": "abc123", "source": "q3-report.pdf", "score": 0.88, "chunk_index": 15}
    ],
    "confidence": "high",
    "latency_ms": 847
  }
}
```

**Error responses**
```json
// 401 Unauthorized
{"error": {"message": "Invalid API key", "type": "authentication_error", "code": "invalid_key"}}

// 422 Unprocessable Entity
{"error": {"message": "No user message found", "type": "invalid_request_error", "code": "missing_user_message"}}

// 503 Service Unavailable
{"error": {"message": "RAG pipeline unavailable", "type": "service_error", "code": "503"}}
```

### 9.2 `POST /ingest/webhook`

**Request**
```json
{"event": "user_signup", "name": "Alice", "role": "Engineer", "team": "AI"}
```

**Response 200**
```json
{"status": "queued", "doc_ids": ["sha256hex..."]}
```

### 9.3 `GET /health`

```json
{"status": "ok", "version": "1.0.0", "env": "production"}
```

### 9.4 `GET /v1/models`

```json
{
  "object": "list",
  "data": [{"id": "vecturaflow", "object": "model", "owned_by": "vecturaflow"}]
}
```

---

## 10. Data Models

### 10.1 DynamoDB — `vecturaflow-registry`

```
Partition Key: doc_id (String)  — SHA256(bucket/key)
GSI: status-ingested_at-index   — enables status queries without full scan

Attributes:
  doc_id        String    SHA256 hash
  source        String    S3 key / webhook source name
  file_type     String    pdf | docx | csv | txt | json | webhook
  status        String    ingestion_started | chunked | embedded | empty_file | parse_failed
  chunk_count   Number    Total chunks created
  ingested_at   String    ISO 8601 timestamp
  updated_at    String    ISO 8601 timestamp
  parse_error   String    Error message if status = parse_failed
```

### 10.2 DynamoDB — `vecturaflow-keys`

```
Partition Key: api_key (String)

Attributes:
  api_key       String    Bearer token value
  key_id        String    Human-readable identifier
  owner         String    Owner name / team
  revoked       Boolean   If true, all requests rejected
  created_at    String    ISO 8601 timestamp
```

### 10.3 Pinecone Vector Record

```json
{
  "id": "sha256hex_chunk_14",
  "values": [0.021, -0.043, ...],
  "metadata": {
    "doc_id": "sha256hex",
    "source": "q3-report.pdf",
    "text": "Revenue increased 23% in Q3 driven by...",
    "chunk_index": 14,
    "file_type": "pdf",
    "page": 3,
    "section": "Financial Results"
  }
}
```

### 10.4 SQS Message Schema

**Ingestion Queue**
```json
{"doc_id": "sha256", "bucket": "vecturaflow-ingestion", "key": "docs/report.pdf", "file_type": "pdf"}
```

**Embedding Queue**
```json
{
  "chunk_id": "sha256_chunk_14",
  "doc_id": "sha256",
  "text": "chunk content here",
  "source": "docs/report.pdf",
  "chunk_index": 14,
  "total_chunks": 42,
  "file_type": "pdf",
  "page": 3,
  "section": "Financial Results"
}
```

---

## 11. Agent System Design

VecturaFlow uses a Claude Code agent architecture — each agent is a `.md` file in `.claude/agents/` with a focused responsibility.

| Agent | File | Trigger | Outputs |
|---|---|---|---|
| FileIngestionAgent | `file-ingestion-agent.md` | S3 PUT event | doc_id, SQS message |
| ParserAgent | `parser-agent.md` | SQS ingestion queue | TextBlock list |
| ChunkingAgent | `chunking-agent.md` | ParserAgent output | Chunk list, SQS messages |
| WebhookIngestionAgent | `webhook-ingestion-agent.md` | HTTP POST | SQS embedding message |
| EmbeddingAgent | `embedding-agent.md` | SQS embedding queue | Pinecone vectors |
| RetrieverAgent | `retriever-agent.md` | RAGAgent call | Ranked chunk list |
| RAGAgent | `rag-agent.md` | QueryHandlerAgent | answer, sources, confidence |
| QueryHandlerAgent | `query-handler-agent.md` | HTTP POST | OpenAI-compat response |
| InfraDeployAgent | `infra-deploy-agent.md` | Manual / CI | Live ECS service |
| TestAgent | `test-agent.md` | Manual / CI | pytest results |
| DemoAgent | `demo-agent.md` | Manual | Console output |

### Agent Execution Order

```
[Ingest]   FileIngestionAgent → ParserAgent → ChunkingAgent → EmbeddingAgent
[Query]    QueryHandlerAgent → RAGAgent → RetrieverAgent → RAGAgent → QueryHandlerAgent
[Deploy]   InfraDeployAgent
[Verify]   TestAgent → DemoAgent
```

---

## 12. Non-Functional Requirements

### Performance

| Metric | Target | Measurement |
|---|---|---|
| Query API P95 latency | < 3 seconds | CloudWatch |
| Query API P99 latency | < 5 seconds | CloudWatch |
| Ingestion latency (file) | < 60 seconds S3 → queryable | DynamoDB timestamp diff |
| Ingestion latency (webhook) | < 10 seconds | DynamoDB timestamp diff |
| Embedding throughput | > 100 chunks/minute | CloudWatch metric |
| Cache hit rate | > 40% on repeated queries | Redis INFO stats |

### Reliability

| Metric | Target |
|---|---|
| API uptime | > 99.5% |
| Ingestion success rate | > 99% (with DLQ fallback) |
| Max retry attempts | 3x with exponential backoff |
| DLQ monitoring | CloudWatch alarm on DLQ depth > 0 |

### Scalability

| Dimension | Approach |
|---|---|
| Ingestion burst | SQS absorbs spikes, Lambda scales to 1000 concurrent |
| Query load | ECS auto-scaling on CPU/memory |
| Vector scale | Pinecone serverless scales automatically |
| Embedding cost | Batch embedding (up to 100 per API call) |

### Security

| Requirement | Implementation |
|---|---|
| API authentication | Bearer token validated against DynamoDB |
| Key revocation | `revoked: true` in DynamoDB — instant effect |
| Secrets management | AWS SSM Parameter Store (never in env vars in prod) |
| S3 bucket access | Private, versioning enabled, public access blocked |
| Network | ECS in private subnet, only API Gateway is public |

---

## 13. Success Metrics

### Technical KPIs

| Metric | Target | How Measured |
|---|---|---|
| Answer relevance (RAGAS) | > 0.80 faithfulness score | RAGAS eval suite |
| Context recall | > 0.75 | RAGAS eval suite |
| P95 query latency | < 3 seconds | CloudWatch |
| Ingestion success rate | > 99% | DynamoDB status ratio |
| Test coverage | > 70% | pytest-cov |

### Portfolio KPIs

| Metric | Target |
|---|---|
| GitHub stars / forks | Leading indicator of quality |
| LinkedIn post engagement | Demonstrates visibility to hiring managers |
| Interview mentions | Candidate can demo live in < 5 minutes |
| AWS deployment cost | < $120/month at MVP scale |

### Sprint KPIs

| Metric | Target |
|---|---|
| Days to working API | Day 4 |
| Days to AWS deployment | Day 7 |
| Days to complete | Day 10 |
| Backlog items at Day 10 | 0 |

---

## 14. Risk Register

| ID | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R-001 | OpenAI API rate limits hit during bulk ingestion | Medium | High | Exponential backoff (5 retries), batch embedding (100 chunks/call), SQS throttling |
| R-002 | Pinecone free tier exceeded (>1M vectors) | Low | Medium | Monitor vector count, use metadata filtering to delete stale docs before limit |
| R-003 | Lambda cold start adds latency to first ingestion | Low | Low | Pre-warm Lambda with EventBridge scheduled ping |
| R-004 | LangGraph agent loop infinite on edge case queries | Low | High | Set max_iterations=10 in graph, timeout at 30s, return 503 |
| R-005 | ChunkingAgent metadata loss (original bug) | Fixed | Critical | Chunk per block, not per document — implemented in Day 2 |
| R-006 | RAGAgent decompose_query stub never fires | Medium | Medium | Implement real LLM-based decomposition on Day 6 |
| R-007 | DynamoDB full table scan in demo script | Fixed | Medium | GSI on (status, ingested_at), use query() not scan() |
| R-008 | Sprint scope creep | Medium | High | Hard 10-day rule, backlog everything that isn't P0/P1 |
| R-009 | AWS costs exceed $120/month | Low | Medium | Serverless-first stack, Lambda on-demand, Pinecone free tier |

---

## 15. Security & Compliance

### Authentication Flow

```
Client → API Gateway → QueryHandlerAgent
  → Extract "Bearer <key>" from Authorization header
  → DynamoDB.get_item(api_key=key)
  → If not found OR revoked=true → 401
  → If found → proceed with request
```

### Secrets Management

| Secret | Storage | Access Pattern |
|---|---|---|
| `OPENAI_API_KEY` | SSM Parameter Store (SecureString) | Lambda env via SSM at cold start |
| `PINECONE_API_KEY` | SSM Parameter Store (SecureString) | Lambda env via SSM at cold start |
| `AWS_*` credentials | IAM Role (no static keys in prod) | Implicit via EC2/Lambda role |

### IAM Principle of Least Privilege

Each Lambda function has its own IAM role with only the permissions it needs:

- `FileIngestionAgent` role: `s3:GetObject`, `dynamodb:PutItem`, `dynamodb:GetItem`, `sqs:SendMessage`
- `EmbeddingAgent` role: `sqs:ReceiveMessage`, `dynamodb:UpdateItem`, `s3:PutObject` (for failed chunks)
- `QueryHandlerAgent` role: `dynamodb:GetItem` (keys table only)

---

## 16. Project Timeline — 10-Day Sprint

```
Day 01 [DONE] ── Scaffold: FastAPI skeleton, S3 Lambda, Pinecone setup
Day 02 [DONE] ── ParserAgent: PDF/DOCX/CSV/TXT/JSON + ChunkingAgent (metadata fix)
Day 03 ────────── EmbeddingAgent: SQS consumer → OpenAI embed → Pinecone upsert
Day 04 ────────── Basic RAG: RetrieverAgent + QueryHandlerAgent wired end-to-end
Day 05 ────────── Webhook + Stream ingestion, full ingestion pipeline tested E2E
Day 06 ────────── LangGraph RAGAgent: decompose → retrieve → generate (real logic)
Day 07 ────────── Docker + ECS Fargate deploy, API Gateway, live HTTPS endpoint
Day 08 ────────── Auth hardening, CloudWatch dashboards, Redis cache
Day 09 ────────── README, test coverage >70%, demo script polished
Day 10 ────────── E2E smoke test, Loom recording, GitHub push, LinkedIn post
```

### Daily Deliverables

| Day | Deliverable | Definition of Done |
|---|---|---|
| 1 | Repo live on GitHub | `make dev` starts API, `/health` returns 200 |
| 2 | Parser + Chunker | `make test` passes all Day 2 tests |
| 3 | Embedding pipeline | PDF → Pinecone vectors confirmed via Pinecone console |
| 4 | Working RAG query | `curl /v1/chat/completions` returns grounded answer |
| 5 | All ingestion paths | File, webhook, stream all route to Pinecone |
| 6 | LangGraph agent live | Multi-step reasoning with cited sources |
| 7 | AWS deployed | Live HTTPS endpoint, health check passing |
| 8 | Secured + monitored | Auth + CloudWatch dashboard |
| 9 | Interview-ready repo | README, tests, demo script |
| 10 | Public | GitHub public, LinkedIn post published |

---

## 17. POC Validation Plan

See `poc/poc_runner.py` for the full runnable POC.

### Hypothesis

> *"The VecturaFlow stack (OpenAI embed + Pinecone + GPT-4o mini + LangGraph) can ingest a real document, retrieve relevant context accurately, and answer questions about it with latency under 3 seconds — all validated locally before AWS deployment."*

### POC Test Matrix

| Test | What It Validates | Pass Criteria |
|---|---|---|
| POC-001 | S3 → Pinecone end-to-end | Vectors confirmed in Pinecone after upload |
| POC-002 | OpenAI embed quality | Cosine similarity > 0.75 for relevant query |
| POC-003 | FastAPI latency | P95 < 3000ms over 50 concurrent requests |
| POC-004 | LangGraph reasoning | Correct answer + correct source on 3 test questions |
| POC-005 | Complete E2E | Upload → index → query → cited answer in < 90 seconds |

### POC Success = Day 1 Start

If all 5 POC tests pass, the technical hypothesis is validated and Day 1 sprint begins with confidence.

---

## 18. Cost Model

### MVP Monthly Cost (~500 docs, ~1000 queries/day)

| Service | Usage | Monthly Cost |
|---|---|---|
| AWS Lambda | 2M invocations | $0 (free tier) |
| AWS SQS | 5M messages | $0 (free tier) |
| AWS S3 | 50GB storage | $1.15 |
| AWS DynamoDB | On-demand | ~$1 |
| AWS Kinesis | 2 shards | $21.60 |
| AWS ECS Fargate | 0.5 vCPU / 1GB | $9 |
| AWS API Gateway | 1M calls | $0 (free tier) |
| AWS ElastiCache | cache.t3.micro | $13 |
| OpenAI Embeddings | 5M tokens | $0.10 |
| OpenAI GPT-4o mini | 500K tokens | $0.15 |
| Pinecone | Starter (≤1M vectors) | $0 |
| **Total** | | **~$46–50/month** |

### Cost Scaling

| Scale | Monthly Cost |
|---|---|
| MVP (500 docs, 1K q/day) | ~$50 |
| Growth (5K docs, 10K q/day) | ~$150 |
| Scale (50K docs, 100K q/day) | ~$600 |

---

## 19. Future Roadmap

### v1.1 — Performance & Quality (Post-Sprint)

- Cohere reranker integration (15–20% retrieval quality improvement)
- Hybrid BM25 + vector search via OpenSearch
- RAGAS evaluation pipeline with automated quality regression testing
- Embedding drift monitoring with W&B

### v1.2 — Scale & Multi-Tenancy

- Pinecone namespace isolation per tenant
- Per-tenant usage metering and billing
- Self-hosted LLaMA 3 on SageMaker for cost predictability at scale

### v2.0 — Platform

- Admin UI: ingestion status, query analytics, vector browser
- Terraform IaC for reproducible multi-environment deploys
- GitHub Actions CI/CD pipeline
- OpenTelemetry distributed tracing

---

## 20. Glossary

| Term | Definition |
|---|---|
| RAG | Retrieval-Augmented Generation — LLM answers grounded in retrieved context |
| Chunk | A 512-character text segment extracted from a document |
| Embedding | A 1536-dimensional float vector representing text semantics |
| Pinecone | Managed vector database for similarity search |
| LangGraph | Graph-based agent orchestration framework from LangChain |
| SQS | AWS Simple Queue Service — async message queue |
| DLQ | Dead Letter Queue — SQS queue for failed messages |
| ECS Fargate | AWS serverless container runtime |
| OpenAI-compatible | API that matches OpenAI's request/response schema |
| Cosine similarity | Distance metric between two vectors (1.0 = identical) |
| RAGAS | RAG Assessment — framework for evaluating RAG pipeline quality |
| doc_id | SHA256 hash of bucket/key — unique document identifier |
| chunk_id | `{doc_id}_chunk_{index}` — unique chunk identifier |
