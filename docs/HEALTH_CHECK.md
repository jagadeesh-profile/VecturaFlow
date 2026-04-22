# Health Check

Run the full pipeline verification with one command:

```bash
make check-all
```

This runs, in order: `preflight` → `pinecone-stats` → `verify`.

## First-run checklist

| Step | Expected | If it fails |
|------|----------|-------------|
| `make preflight` | Masked key prints, OpenAI returns `ok` | `OPENAI_API_KEY` missing, placeholder, or invalid — check `.env` and shell cache |
| `make pinecone-stats` | `total_vector_count > 0`, correct index name | Ingestion ran but vectors didn't land — check Pinecone index name and environment vars |
| `make verify` (in-corpus Q) | Matches with score >= `RETRIEVAL_THRESHOLD` and answer contains PDF specifics | Embedding model mismatch between ingestion and query, or index incomplete |
| `make verify` (out-of-corpus Q) | Answer: "Not found in the provided documents" | Prompt construction issue — model is answering from weights, not context |

## Failure patterns by step

### 1. `make preflight` fails

| Symptom | Likely cause | Fix |
|---|---|---|
| `OPENAI_API_KEY` not set | `.env` not loaded or var missing | Check `.env` exists, source `.env`, or confirm `python-dotenv` loads it |
| Key prints as `sk-xxx...xxxx` (placeholder pattern) | `.env` still has template value | Replace placeholder with real rotated key |
| `401 Unauthorized` from OpenAI | Key invalid, revoked, or from wrong org | Regenerate at platform.openai.com, update `.env` |
| `429` quota exceeded | Billing issue or rate limit | Check usage dashboard, add payment method |
| Masked key matches old key, not new one | Shell cached old env var | `unset OPENAI_API_KEY` then re-source `.env`, or open fresh terminal |
| Works locally, fails in Lambda/CI | Secret not propagated to deploy env | Update secret in AWS Secrets Manager / GitHub Actions / wherever |

### 2. `make pinecone-stats` fails

| Symptom | Likely cause | Fix |
|---|---|---|
| `total_vector_count: 0` | Ingestion never upserted, or upserted to different index | Check registry vs. Pinecone console; verify index name in config |
| `IndexNotFoundError` | Wrong `PINECONE_INDEX` value | Confirm exact index name in Pinecone console (case-sensitive) |
| `AuthenticationError` | Wrong or expired `PINECONE_API_KEY` | Regenerate in Pinecone console |
| Wrong `PINECONE_ENVIRONMENT` | Index is in a different region/env than configured | Check console; value usually looks like `us-east-1-aws` or `gcp-starter` |
| Count is nonzero but far below expected | Partial ingestion, or ingestion hit rate limits mid-run | Check ingestion logs for errors; re-run ingestion for missing docs |
| Count looks right but namespaces empty/wrong | Upserts went to default namespace, queries target named namespace (or vice versa) | Align namespace between ingest and query code |

### 3. `make verify` fails

| Symptom | Likely cause | Fix |
|---|---|---|
| All scores between 0.4–0.6 but clearly relevant | Threshold calibrated for wrong embedding model | Run `python scripts/verify_end_to_end.py --calibrate` and update `RETRIEVAL_THRESHOLD` in `.env`. |
| `DimensionMismatchError` from Pinecone | Index was created for one model's dimensions, query uses another | Recreate index with correct dimension, or switch embedding model back |
| Zero matches returned | Wrong namespace, or top_k query filtering too aggressively | Remove metadata filters temporarily; check namespace |
| Good scores (>0.8) but answer is generic | Retrieved chunks not reaching the OpenAI call | Print the exact messages payload sent to OpenAI; verify context is in the user/system message |
| Good scores, answer is hallucinated specifics | System prompt not strict enough about grounding | Tighten prompt: "Answer ONLY from the provided context. If context is insufficient, respond exactly: 'Not found in the provided documents.'" |
| Out-of-corpus question still returns a confident answer | Model ignoring the refusal instruction | Add a few-shot example of refusal in the system prompt; lower temperature to 0 |
| In-corpus PASS but out-of-corpus also PASSes with PDF content | Scoring threshold too permissive, irrelevant chunks retrieved | Raise score threshold or add a minimum relevance gate before calling LLM |
| Works for some PDFs, not others | Chunking split a concept across chunks without overlap | Increase chunk overlap at ingestion (e.g. 200 tokens); re-ingest affected docs |

## Sneaky failures

1. Silent embedding-model drift. Someone upgrades `text-embedding-3-small` to `text-embedding-3-large` in query code but not ingestion. Pinecone still returns matches, but scores are garbage and retrieval is effectively random. Always log and assert embedding dimensions match.
2. Lambda image cache. You push new code, but the Lambda container reuses a warm instance with the old image or old env vars. Symptoms: local `make check-all` passes, deployed version fails identically every time. Fix: force a cold start (update env var, republish, or bump memory) and re-test.

## Diagnostic escalation order

If `make check-all` fails and the tables above don't pinpoint it:

1. Run each script directly with Python and read the full traceback. Make swallows some output.
2. Add a `--verbose` flag to `verify_end_to_end.py` that prints the exact OpenAI request payload.
3. Compare a known-good vector from the registry against `fetch()` in Pinecone. This proves round-trip at the storage layer.
4. Re-run ingestion on a single known doc and watch it flow queue → embed → upsert → registry → retrieval in one session.

## Individual commands

- `make preflight` — validate OpenAI key only
- `make pinecone-stats` — inspect vector index
- `make verify` — default 3-question RAG test
- `make verify-q Q="your question"` — ask a custom question
- `make triage` — dry-run queue triage
- `make triage-apply` — apply drain/reprocess/DLQ decisions
