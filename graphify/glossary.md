# glossary.md — Project Vocabulary

Terms in this codebase have specific meanings. When you see these words in
code, tests, or logs, assume the definitions below.

---

## Data objects

**TextBlock** — a piece of text produced by `ingestion/parser.py`, carrying
structural metadata: `doc_id`, `source`, `text`, `file_type`, and (where
applicable) `page`, `section`, `row`, `element_type`. One PDF page = one
TextBlock. One CSV row = one TextBlock. Defined in `ingestion/models.py`.

**Chunk** — a TextBlock that has been split to fit inside the embedding
model's context window. Produced by `ingestion/chunker.py`. Crucially:
chunks inherit the TextBlock's metadata (page, section, row) so citations
survive into the vector store. `chunk_index` is **global** across the whole
document, not block-local.

**doc_id** — SHA-256 of `bucket/key` for S3 uploads, UUID4 for webhooks.
Primary key in `vecturaflow-registry`, metadata field on every vector.

**RetrievedChunk** — what Pinecone returns, after we've converted it into a
Pydantic v2 model. Has `chunk_id`, `doc_id`, `text`, `source`, `score`,
`chunk_index`, `low_confidence`. Defined in `api/schemas.py`.

**SourceCitation** — the trimmed citation form we send back to the client.
Defined in `api/schemas.py`. No raw text — just `doc_id`, `source`, `score`,
`chunk_index`.

**AgentState** (TypedDict) — what flows between LangGraph nodes in
`api/agent.py`. Fields: `query`, `sub_queries`, `chunks`, `answer`,
`sources`, `confidence`, `filters`.

---

## Concepts

**MMR (Maximal Marginal Relevance)** — reranking algorithm in
`api/retriever.py`. Iteratively picks candidates that maximise
`λ · sim(query, cand) − (1−λ) · max sim(cand, already_selected)`.
We use `λ = 0.5` over 20 candidates.  Trades a little relevance for
diversity so the top-K aren't N near-duplicates of the same paragraph.

**Confidence** — one of `high | low | no_context`. See `api/schemas.py`
Confidence enum. Decided by `validate_answer` in `api/agent.py`.

**Partial batch response** — AWS Lambda / SQS pattern: the handler returns
`{"batchItemFailures": [{"itemIdentifier": <msgId>}, ...]}` so only failed
records get redelivered. Every Lambda in this repo uses this pattern.

**DLQ** — dead-letter queue. Two of them: `ingest-dlq` and `embed-dlq`.
Provisioned in `infra/terraform/`. Max receives = 5.

**GSI (status-ingested_at-index)** — DynamoDB Global Secondary Index on
`vecturaflow-registry`. Use this for status filtering queries; **never
call DynamoDB `scan()`** (expensive, unbounded).

**Token-bucket limiter** — `api/rate_limit.py`. Per-key bucket, refills at
`RATE_LIMIT_PER_MINUTE`. Per-process, not distributed. Acceptable until
we shard across ECS tasks.

**Low-confidence fallback** — when no Pinecone hits clear the score
threshold, `retriever.py` returns the top-3 flagged `low_confidence=True`
so the RAG agent can answer with a warning rather than a flat "no data".

**Grounded / Ungrounded** — the output of `validate_answer`. GPT-4o mini is
asked to decide if the generated answer is supported by the retrieved
context. UNGROUNDED → downgrade confidence to `low`.

---

## Entities (AWS / Pinecone / Redis)

| Entity                          | Kind              | Purpose                                  |
|---------------------------------|-------------------|------------------------------------------|
| `vecturaflow-raw-*` (bucket)    | S3                | Raw uploads land here.                   |
| `vecturaflow-ingest`            | SQS               | Between S3 Lambda and parser Lambda.     |
| `vecturaflow-embed`             | SQS               | Between parser Lambda and embed Lambda.  |
| `vecturaflow-ingest-dlq`        | SQS DLQ           | Poison messages from ingest.             |
| `vecturaflow-embed-dlq`         | SQS DLQ           | Poison messages from embed.              |
| `vecturaflow-registry`          | DynamoDB          | PK `doc_id`, GSI `status-ingested_at`.   |
| `vecturaflow-keys`              | DynamoDB          | PK `api_key_hash`. Dev bypass is synthetic. |
| Pinecone index `vecturaflow`    | Pinecone          | 1536-dim cosine, us-east-1.              |
| ElastiCache Redis               | Redis 7           | 5-min retrieval cache.                   |
| `vecturaflow-api` cluster       | ECS Fargate       | Runs `api.main:app`.                     |

---

## Things the project calls differently than the outside world

- **"Agent"** here means a focused module with its own brief in
  `.claude/agents/`. It does **not** mean a LangGraph agent unless we say
  "RAGAgent" specifically.
- **"Confidence"** is not a calibrated probability — it's a three-valued
  enum (see above).
- **"Registry"** = DynamoDB `vecturaflow-registry`, not a pip/npm registry.
- **"Cache hit"** specifically refers to the Redis retrieval cache. Lambda
  warm-start reuse is called "warm start", not "cache hit".
