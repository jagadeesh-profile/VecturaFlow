# graphify/ — Project Map for VecturaFlow

This directory summarizes the repository structure, data flow, design decisions,
and per-module responsibilities. It is a compact companion to the source code
for reviewers who want to understand the system without opening every file.

---

## Read order

1. **`INDEX.md`** — this file. Orient yourself in 30 seconds.
2. **`architecture.md`** — tech stack, layers, locked decisions.
3. **`dataflow.md`** — the ingest and query pipelines end to end.
4. **`glossary.md`** — project vocabulary (`doc_id`, `RAGState`, MMR, confidence).
5. **`decisions.md`** — non-obvious tradeoffs and why.
6. **`modules/*.md`** — per-file cards. Jump here when you need implementation detail.
7. **`graph.json`** — machine-readable node and edge map of the repo.

---

## What VecturaFlow is

A production-grade agentic RAG platform on AWS. S3 uploads and HTTP webhooks
stream into an SQS fan-out pipeline; Lambdas parse, chunk, embed, and write
vectors to Pinecone and registry rows to DynamoDB. A FastAPI service on ECS
Fargate exposes an OpenAI-compatible `/v1/chat/completions` endpoint backed by
a 4-node LangGraph RAG agent: **decompose -> retrieve -> generate -> validate**.
Retrieval uses OpenAI `text-embedding-3-small`, Pinecone serverless cosine
search with MMR reranking, and a 5-minute Redis cache.

---

## Project rules

1. **Never use `os.environ` directly.** Import `settings` from `api.config`.
2. **Never use `print()`.** Import `logger` from `api.logger` or
   `ingestion.logging_util.get_logger(__name__)` in Lambdas.
3. **Chunk per TextBlock, not per document.** Losing per-block metadata
   (page, section, row) breaks citation. See `modules/ingestion-chunker.md`.
4. **Module-level boto3 clients.** Avoid creating clients inside handlers;
   it hurts Lambda warm-start latency.
5. **Partial-batch SQS errors.** Every Lambda returns `batchItemFailures`;
   never fail the whole batch on one bad record.
6. **Pydantic v2 syntax.** `model_dump()` not `dict()`. `model_validate()` not
   `parse_obj()`. `min_length=` not `min_items=`.
7. **Tests mock AWS with `moto`.** Never hit real AWS from the test suite.
   Set `OPENAI_API_KEY=sk-test` before importing app modules.
8. **Dev bypass API key is literally `"dev"`** when `API_ENV=development`.

---

## Layout

```
VecturaFlow/
├── graphify/                    # project map and per-module cards
│   ├── INDEX.md
│   ├── architecture.md
│   ├── dataflow.md
│   ├── glossary.md
│   ├── decisions.md
│   ├── graph.json
│   └── modules/
├── api/                         # FastAPI + RAG agent + retriever
├── ingestion/                   # S3/webhook Lambdas + parser + chunker
├── embeddings/                  # embedding Lambda
├── infra/terraform/             # VPC, ALB, ECS Fargate, IAM, secrets
├── scripts/                     # setup, validation, demo, graph generation
├── tests/                       # pytest + moto
└── docs/                        # README companion docs
```

---

## Regenerating this map

After a structural change, run:

```bash
python scripts/graphify.py
```

This rebuilds `modules/*.md` and `graph.json` from the live source tree. The
hand-written files (`INDEX.md`, `architecture.md`, `dataflow.md`, `glossary.md`,
`decisions.md`) are edited only when a design decision changes.

