# dataflow.md — How Data Moves

Two pipelines: **write path** (ingestion) and **read path** (query).
They share DynamoDB and Pinecone but never call each other directly.

---

## Write path — from upload to queryable vector

```
 ┌─────────────┐
 │ User / ETL  │
 └──────┬──────┘
        │ PUT s3://${INGESTION_BUCKET}/raw/<file>
        ▼
 ┌────────────────────────┐
 │ S3 ObjectCreated event │
 └──────┬─────────────────┘
        ▼
 ┌─────────────────────────────────────────────────────┐
 │ λ FileIngestionAgent  (ingestion/lambda_s3.py)      │
 │  • doc_id  = SHA256(bucket/key)                     │
 │  • Registry.put_item(doc_id, status="ingestion_started"), idempotent                │
 │  • SQS.send(INGESTION_QUEUE_URL, {doc_id,bucket,key})│
 └──────┬──────────────────────────────────────────────┘
        ▼
 ┌─────────────────────────────────────────────────────┐
 │ SQS ingest queue                                    │
 │  • visibility_timeout_seconds = 360  (6 min)        │
 │  • DLQ after 5 receives                             │
 └──────┬──────────────────────────────────────────────┘
        ▼
 ┌─────────────────────────────────────────────────────┐
 │ λ lambda_parser  (ingestion/lambda_parser.py)       │
 │  • parser.parse_file() → list[TextBlock]            │
 │  • chunker.chunk_blocks() → list[Chunk]             │
 │  • chunker.publish_chunks(EMBEDDING_QUEUE_URL)      │
 │  • Registry.update_item(status="chunked")           │
 │  • Returns batchItemFailures (per-record)           │
 └──────┬──────────────────────────────────────────────┘
        ▼
 ┌─────────────────────────────────────────────────────┐
 │ SQS embedding queue                                 │
 └──────┬──────────────────────────────────────────────┘
        ▼
 ┌─────────────────────────────────────────────────────┐
 │ λ EmbeddingAgent  (embeddings/lambda_embed.py)      │
 │  • OpenAI.embeddings.create(text-embedding-3-small) │
 │  • Pinecone.upsert(vectors with full metadata)      │
 │  • Registry.update_item(status="embedded")          │
 └──────┬──────────────────────────────────────────────┘
        ▼
 ┌─────────────────────────────────────────────────────┐
 │ Pinecone index `vecturaflow`                        │
 │  dim=1536, metric=cosine, aws/us-east-1             │
 │  metadata: doc_id, source, text, chunk_index,       │
 │            file_type, page, section                 │
 └─────────────────────────────────────────────────────┘
```

**Alternative write entrypoint — webhook:**

```
 HTTP POST /webhook/ingest  ─▶ λ WebhookIngestionAgent
                                 • doc_id = uuid4()  (NOT hash — avoid collisions)
                                 • Writes text to S3 raw/ (triggers same pipeline)
                                 • or publishes directly to ingest SQS
```

### Invariants

- **Idempotency:** `doc_id = SHA256(bucket/key)` for S3 uploads → re-uploading
  the same file writes the same row and the same Pinecone vectors. Conditional
  writes on DynamoDB prevent duplicate `ingestion_started` updates.
- **Partial batches:** Every Lambda returns
  `{"batchItemFailures":[{"itemIdentifier":messageId}, ...]}` so SQS retries
  only the failed records, not the whole batch.
- **Metadata preservation:** Chunking is done **per TextBlock**, never on the
  concatenated document text. Each `Chunk` carries the block's `page`,
  `section`, `row`, `element_type`.
- **DLQ:** 5 receives → DLQ. Ops replay via `aws sqs receive-message` →
  re-send to main queue (see `docs/OPERATIONS.md`).

### Registry states

`ingestion_started` → `chunked` → `embedded`
Terminal failure states: `empty_file`, `parse_failed`.

---

## Read path — from question to grounded answer

```
 ┌─────────────┐
 │   client    │  POST /v1/chat/completions  (Bearer <api_key>)
 └──────┬──────┘
        ▼
 ┌────────────────────────────────────────────────┐
 │  ALB  →  ECS Fargate  →  FastAPI (api/main.py) │
 │   • CORS middleware                            │
 │   • metrics middleware (Prometheus)            │
 │   • request_logging_middleware (X-Request-ID)  │
 └──────┬─────────────────────────────────────────┘
        ▼
 ┌────────────────────────────────────────────────┐
 │ api.dependencies.verify_api_key                │
 │  • Bearer scheme check                         │
 │  • DynamoDB keys_table lookup                  │
 │  • Dev bypass: key == "dev" in development     │
 └──────┬─────────────────────────────────────────┘
        ▼
 ┌────────────────────────────────────────────────┐
 │ api.rate_limit.require_rate_limit              │
 │  • TokenBucketLimiter per-key                  │
 │  • 429 + Retry-After: 30 when exhausted        │
 └──────┬─────────────────────────────────────────┘
        ▼
 ┌────────────────────────────────────────────────┐
 │ api.agent.run_rag  (LangGraph StateGraph)      │
 │                                                │
 │   ┌──────────┐   ┌──────────┐   ┌──────────┐   │
 │   │decompose │──▶│ retrieve │──▶│ generate │   │
 │   └──────────┘   └─────┬────┘   └─────┬────┘   │
 │                        │              │        │
 │                        ▼              ▼        │
 │                   api.retriever   validate ───▶END
 └──────┬─────────────────────────────────────────┘
        ▼
 ┌────────────────────────────────────────────────┐
 │ api.retriever.retrieve                         │
 │  1. cache key = md5(query|top_k|filters)       │
 │  2. Redis GET (5-min TTL)  ── HIT? return.     │
 │  3. OpenAI embed query                         │
 │  4. Pinecone.query(top_k=20, include_values=T) │
 │  5. score ≥ threshold? apply; else top-3 low_confidence
 │  6. MMR rerank (λ=0.5) → top_k                 │
 │  7. Redis SETEX 300 result                     │
 └──────┬─────────────────────────────────────────┘
        ▼
 ┌────────────────────────────────────────────────┐
 │ api.agent.generate_answer                      │
 │  • Build context block with [source (page N)]  │
 │  • GPT-4o mini, temperature=0                  │
 └──────┬─────────────────────────────────────────┘
        ▼
 ┌────────────────────────────────────────────────┐
 │ api.agent.validate_answer                      │
 │  • GPT-4o mini grounding check                 │
 │  • UNGROUNDED → downgrade confidence=low       │
 └──────┬─────────────────────────────────────────┘
        ▼
   200 OK   { choices:[{message:{content}}],
              usage:{sources,confidence,latency_ms} }
```

### Confidence tiers

- `high` — all chunks above score threshold, answer grounded.
- `low` — chunks came from low-confidence fallback **or** validator said
  UNGROUNDED.
- `no_context` — Pinecone returned zero hits. Answer is a fixed refusal line.

### Failure modes

| Stage       | Failure                        | Client sees           |
|-------------|--------------------------------|-----------------------|
| Auth        | Missing/invalid key            | 401 `invalid_key`     |
| Auth        | DynamoDB unavailable           | 503 `auth_unavailable`|
| Rate limit  | Bucket empty                   | 429 `Retry-After: 30` |
| Embed       | OpenAI 3x retry failure        | 503 from `/v1/chat/completions` |
| Pinecone    | 2x retry failure               | 200 with empty sources + `no_context` |
| Redis       | Any error                      | Transparent — retrieval still works |
| Generation  | OpenAI failure                 | 200 with error message in `answer`  |
| Validation  | OpenAI failure                 | 200, keeps current confidence       |
