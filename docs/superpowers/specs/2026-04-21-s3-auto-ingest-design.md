# S3 → Pinecone Auto-Ingest — Design (2026-04-21)

## Problem

User uploaded 2 PDFs to `s3://vecturaflow-prod-ingestion-383175541991/`:
- `100_Days_AI_DSA_ProgramGuide.pdf`
- `deep_learning_notes.pdf`

Neither is in the Pinecone `vecturaflow` index. The chat endpoint
`/v1/chat/completions` (live at
`https://vecturaflow-prod-alb-635509785.us-east-1.elb.amazonaws.com`)
returns `"confidence":"no_context"` because retrieval hits zero vectors.

Root cause: the 3 ingestion Lambdas (`ingestion/lambda_s3.py`,
`ingestion/lambda_parser.py`, `embeddings/lambda_embed.py`) were written
but never deployed — Terraform has the SQS queues + S3→SQS notification
wired, but no `aws_lambda_function` resources.

## Goals

1. **Backfill** — the 2 existing PDFs (and anything already in S3 at run time)
   must land in Pinecone and be queryable via `/v1/chat/completions`.
2. **Auto-ingest going forward** — any new object dropped into the bucket
   must be parsed → chunked → embedded → upserted within ~2 minutes.
3. **Idempotent** — re-running the backfill over the same file is a no-op.
4. **No surgery on already-running services** — the ECS API and its
   retriever must remain untouched.

## Non-goals

- Replacing the self-signed ALB cert with ACM (separate backlog item).
- Hashing API keys in DynamoDB (separate backlog item).
- Moving Terraform state to S3 + DynamoDB (separate backlog item).
- Multi-tenant namespacing of Pinecone vectors (all vectors in the default
  namespace for now).
- LangChain 0.3 CVE remediation (separate backlog item).

## Architecture

### Auto-ingest path (future uploads)

```
S3 PUT (ObjectCreated:*)
  ↓  (existing bucket notification, main.tf:249)
SQS vecturaflow-prod-ingestion  ──────→  DLQ (visibility-timeout-based)
  ↓  (new: event source mapping)
Lambda ingest-s3
  ↓  writes registry row, enqueues
SQS vecturaflow-prod-ingestion  (re-queue with doc metadata JSON)
  ↓
Lambda ingest-parser
  ↓  parses, chunks
SQS vecturaflow-prod-embedding
  ↓
Lambda ingest-embed
  ↓  OpenAI embed → Pinecone upsert → DynamoDB status=embedded
```

Note: the design uses the **same** ingestion SQS queue twice — once for
the S3 notification (body = S3 event JSON) and once for `ingest-s3`'s
output (body = doc-metadata JSON). `lambda_parser` tolerates this because
the body shape is distinguishable. If it becomes noisy we can split later.

### Backfill path (existing files, run once)

Standalone CLI, no Lambdas involved:

```
for key in list_objects(bucket):
    if registry[doc_id].status == "embedded" and not --force: skip
    body  = s3.get_object(key)
    blocks = parse_file(body, file_type, doc_id, source=key)
    chunks = chunk_blocks(blocks, size=512, overlap=50)
    embeds = OpenAI.embeddings.create(texts)
    pinecone.upsert([(chunk_id, emb, metadata) for ...])
    dynamodb.put({doc_id, status:"embedded", chunk_count, ts})
```

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Lambda packaging | Container image | PyMuPDF + unstructured too large for zip (>250 MB); matches existing ECS Dockerfile pattern |
| One image or three | **One image, three handlers** | `image_config.command` per function — less duplication, single build/push |
| Event-source wiring | S3 → SQS → Lambda (not S3 → Lambda direct) | Preserves DLQ retry semantics |
| `lambda_s3` event shape | Patch to detect SQS-wrapped S3 events | 6-line adapter at top of handler; keeps unit tests simple |
| Backfill idempotency | Deterministic chunk IDs (`sha256(key)` + `_chunk_{i}`) | Safe re-runs; matches existing convention in `chunker.py:96` |
| Backfill re-run policy | Skip docs with `status=embedded` unless `--force` | Mirrors `lambda_s3._is_already_processed()` |
| Secrets source | `.env` locally, Secrets Manager in Lambda | Lambda task role already has Secrets Manager read (ECS task def confirms) |

## Files to write

| # | Path | Purpose |
|---|---|---|
| 1 | `scripts/backfill_s3_to_pinecone.py` | One-shot CLI backfill |
| 2 | `Dockerfile.lambda` | Container image for the 3 Lambdas |
| 3 | `requirements.lambda.txt` | Runtime deps for the Lambda image (subset of `requirements.runtime.txt` minus FastAPI/uvicorn/redis/structlog) |
| 4 | `infra/terraform/lambdas.tf` | ECR repo + 3 `aws_lambda_function` + IAM roles/policies + event source mappings + log groups |

## Files to modify

| # | Path | Change |
|---|---|---|
| 1 | `ingestion/lambda_s3.py` | Patch handler to unwrap SQS-wrapped S3 events (6-line guard at top of `handler()`) |
| 2 | `.env` | Update stale resource names to `-prod-*` (bucket, queues, tables) |
| 3 | `tasks/todo.md` | Check off "Deploy missing ingestion Lambdas"; note auto-ingest is live |

## Failure modes handled

| Failure | Behavior |
|---|---|
| OpenAI 429 | 5-retry exponential backoff (reused `_embed_with_backoff` pattern) |
| Pinecone 5xx | 3-retry, then S3 `failed-chunks/` dump (matches Lambda behavior) |
| Parser raises on one file | Log, skip, continue (backfill loop level) |
| Lambda fails 3× | Message → DLQ (`vecturaflow-prod-ingestion-dlq`) |
| Backfill killed mid-run | Re-run resumes via registry check |

## Verification

1. **Post-backfill probe:**
   ```bash
   aws dynamodb scan --table-name vecturaflow-prod-registry --select COUNT
   # expect: Count: 2
   ```
2. **Chat probe:**
   ```bash
   curl -sk https://.../v1/chat/completions \
     -H "Authorization: Bearer vf_prod_..." \
     -d '{"messages":[{"role":"user","content":"Summarize the deep learning notes"}]}'
   # expect: sources includes deep_learning_notes.pdf, confidence:high
   ```
3. **Auto-ingest probe:** upload a 3rd small test file, wait 60 s, query it.
   Check registry count increments to 3.

## Deferred (tracked in `tasks/todo.md`)

- Hash API keys in DynamoDB
- ACM cert for ALB
- Terraform state backend migration (S3 + DDB)
- CVE remediation (langchain 0.2 → 0.3)
- CloudWatch alarms on DLQ depth + embed failure rate
