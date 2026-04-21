# graphify/ — Portable Project Brain for VecturaFlow

> **You are an AI agent opening this repo.** Read this file first. It tells you
> everything you need to act correctly in this codebase without re-reading the
> full source tree.

This directory is **editor-agnostic**. Any agent in any editor — Claude Code,
Cursor, Copilot, Aider, Codex, Continue, Windsurf — can read the Markdown here
and understand the project. The `graph.json` manifest lets tooling that prefers
structured data consume the same knowledge.

The root of the repo also contains `AGENTS.md`, which is a one-line pointer
that sends you here.

---

## Read order (fastest path to being useful)

1. **`INDEX.md`** — this file. Orient yourself in 30 seconds.
2. **`architecture.md`** — tech stack, layers, locked decisions.
3. **`dataflow.md`** — the two pipelines (ingest + query) end to end.
4. **`agents.md`** — which of the 11 specialist agents owns which module.
5. **`glossary.md`** — project vocabulary (doc_id, RAGState, MMR, confidence).
6. **`decisions.md`** — every non-obvious tradeoff and why.
7. **`modules/*.md`** — per-file cards. Jump here when you need to touch code.
8. **`graph.json`** — machine-readable node+edge map of the repo.

You almost never need all of these in one session. Pick the minimum.

---

## What VecturaFlow is (one paragraph)

A production-grade agentic RAG platform on AWS. S3 uploads and HTTP webhooks
stream into an SQS fan-out pipeline; Lambdas parse → chunk → embed → write
vectors to Pinecone and registry rows to DynamoDB. A FastAPI service on ECS
Fargate exposes an OpenAI-compatible `/v1/chat/completions` endpoint backed by
a 4-node LangGraph RAG agent: **decompose → retrieve → generate → validate**.
Retrieval uses OpenAI `text-embedding-3-small`, Pinecone serverless cosine
search with MMR reranking, and a 5-minute Redis cache.

---

## Hard rules (these override your defaults)

1. **Never use `os.environ` directly.** Import `settings` from `api.config`.
2. **Never use `print()`.** Import `logger` from `api.logger` (or
   `ingestion.logging_util.get_logger(__name__)` in Lambdas).
3. **Chunk per TextBlock, not per document.** Losing per-block metadata
   (page, section, row) breaks citation. See `modules/ingestion-chunker.md`.
4. **Module-level boto3 clients.** Never instantiate inside a handler —
   it kills Lambda warm-start latency.
5. **Partial-batch SQS errors.** Every Lambda returns `batchItemFailures`;
   never fail the whole batch on one bad record.
6. **Pydantic v2 syntax.** `model_dump()` not `dict()`. `model_validate()` not
   `parse_obj()`. `min_length=` not `min_items=`.
7. **Tests mock AWS with `moto`.** Never hit real AWS from the test suite.
   Set `OPENAI_API_KEY=sk-test` etc. **before** importing any app module.
8. **Dev bypass API key is literally the string `"dev"`** when
   `API_ENV=development`. Don't remove this — it's how `make dev` works.

---

## Layout

```
VecturaFlow/
├── AGENTS.md                    # root pointer → this folder
├── graphify/                    # <-- you are here
│   ├── INDEX.md
│   ├── architecture.md
│   ├── agents.md
│   ├── dataflow.md
│   ├── glossary.md
│   ├── decisions.md
│   ├── graph.json
│   └── modules/                 # per-file cards
├── CLAUDE.md                    # legacy project brain (Claude Code-specific)
├── api/                         # FastAPI + RAG agent + retriever
├── ingestion/                   # S3/webhook Lambdas + parser + chunker
├── embeddings/                  # embedding Lambda
├── infra/terraform/             # VPC, ALB, ECS Fargate, IAM, secrets
├── scripts/                     # setup_aws, setup_pinecone, validate_env, demo, graphify
├── tests/                       # pytest + moto (107 tests, 82% coverage)
└── docs/                        # README companion docs
```

---

## Regenerating this memory

After a structural change (new module, renamed package, new import), run:

```bash
python scripts/graphify.py
```

This rebuilds `modules/*.md` and `graph.json` from the live source tree. The
hand-written files (`INDEX.md`, `architecture.md`, `agents.md`, `dataflow.md`,
`glossary.md`, `decisions.md`) are **not** regenerated — edit them by hand
when a design decision actually changes.

---

## Cross-editor consumption

| Editor / agent  | How it finds graphify                                        |
|-----------------|--------------------------------------------------------------|
| Claude Code     | Reads `CLAUDE.md` first; `CLAUDE.md` points to `graphify/`.  |
| Cursor          | Reads `AGENTS.md` at repo root → follows to `graphify/`.     |
| Aider           | Reads `AGENTS.md` + any file you add to `--read`.            |
| Codex CLI       | Reads `AGENTS.md` by convention.                             |
| Copilot Chat    | Open `graphify/INDEX.md` in the editor before asking.        |
| Continue        | Add `graphify/` to `contextProviders` in `config.json`.      |
| Windsurf        | Reads `AGENTS.md` and `.windsurfrules` if present.           |
| Custom agents   | Parse `graph.json` for structured nodes + edges.             |

---

## Contact

**Owner:** Jagadeesh Pamidi — `jagadeesh6187@gmail.com`
**Repo:** github.com/jagadeesh6187/VecturaFlow
