# decisions.md — Non-Obvious Tradeoffs

The reasoning behind things that would look wrong without context.

---

### 1. Chunking is per-TextBlock, not per-document

**Why it matters:** the earlier version concatenated all parsed text, then
split. That produces chunks that span pages — which destroys the ability to
cite "page 12 of the report." Every chunk now inherits its origin
TextBlock's `page`, `section`, `row`.

**Cost:** small — slightly more chunks at block boundaries.
**Alternatives rejected:** global split with offset tracking (too fragile
across file types).

See `ingestion/chunker.py::chunk_blocks` and its docstring.

---

### 2. Module-level boto3 clients, not per-handler

**Why:** Lambda containers reuse the process across invocations ("warm
start"). Creating a boto3 client is ~100ms. Keeping clients module-level,
wrapped in `lru_cache`, means only the first cold invocation pays the tax.

**Pattern:**
```python
@lru_cache(maxsize=1)
def _sqs():
    return boto3.client("sqs", region_name=_region())
```

---

### 3. `lru_cache` on every client factory

Applies to: `_sqs`, `_dynamo`, `_s3`, `_openai_client`, `_pinecone_index`,
`_redis_cache`, `_llm`, `get_settings`.

**Why:** lazy initialisation so the module imports without real credentials
(tests, docs build, `poc/`). The cache guarantees singletons without
module-load-time work.

---

### 4. MMR reranking with λ=0.5 and 20 candidates

**Why:** pure relevance returns near-duplicates when a document has
repetitive phrasing (FAQ sections, boilerplate). MMR trades a little
relevance for diversity. λ=0.5 is a balanced starting point; 20 candidates
is enough variety without doubling latency.

**Cost:** one extra Pinecone fetch (cheap) + O(K · N) cosine similarities
in pure Python. For K=5, N=20 that's 100 cosines at ~1536 dims — under a
millisecond.

**Alternatives rejected:** numpy vectorised cosine (not worth the
dependency weight for a single op); learned rerankers (cost + infra).

---

### 5. Redis failure is non-fatal

**Why:** retrieval must stay up even if ElastiCache has a node hiccup.
Every Redis call is wrapped in try/except that falls through to the live
Pinecone path. Monitoring:
`vf_retriever_cache_total{outcome="error"}` counter.

**Tradeoff:** during an outage we'll see p99 latency rise from ~400ms to
~1.1s. Accepted — better than a 5xx.

---

### 6. Per-process rate limiting (not distributed)

**Why:** Redis-backed distributed rate limiting adds a round trip on every
call and a new failure domain. Per-process buckets give us 80% of the
value for 5% of the complexity.

**When it breaks:** if one API key spreads requests evenly across N ECS
tasks, they effectively get N × limit. We'll switch to Redis INCR with
TTL when this actually hurts (currently: it doesn't).

---

### 7. Dev bypass key is literally `"dev"`

`API_ENV=development` + `Bearer dev` skips DynamoDB and returns a synthetic
key record. This is why `make dev` works without AWS credentials.

**Safety:** `verify_api_key` checks `settings.api_env == "development"`
first. In production this branch is unreachable.

---

### 8. `UP017` ignored in ruff

Ruff would auto-rewrite `datetime.timezone.utc` → `datetime.UTC`. That
syntax is Python 3.11+. Our production runtime is 3.11, but the test
matrix still runs on 3.10 (moto + GitHub Actions matrix). Keeping the 3.10
form avoids an import error on collection. When we drop 3.10 from the
matrix, remove `UP017` from `ignore` in `pyproject.toml`.

---

### 9. UUID4 for webhook doc_id, SHA256 for S3 uploads

**Why different:** S3 keys are globally unique inside a bucket, so
`SHA256(bucket/key)` gives content-based idempotency (re-uploading the
same file writes the same vectors). Webhook payloads have no such unique
key — using `SHA256(source+timestamp)` caused collisions under burst load.
UUID4 sidesteps that entirely.

---

### 10. No streaming responses yet

`ChatRequest.stream` is accepted for OpenAI API compatibility, but we
ignore it. The RAG agent's `validate_answer` node needs the whole
generation to check grounding — streaming tokens before validation would
leak hallucinations. We'll revisit with a two-phase design.

---

### 11. FastAPI, not API Gateway → Lambda

**Why a long-running container instead of serverless API?**
- Warm connections to Pinecone, OpenAI, Redis, DynamoDB. Eliminates the
  300-800ms cold start on every request.
- LangGraph + LangChain imports alone cost ~1.2s on cold start.
- ECS autoscaling is more predictable than Lambda concurrency limits.

**When we'd reconsider:** if traffic drops to <1000 requests / day, Lambda
becomes cheaper.

---

### 12. Pinecone serverless, not an index per tenant

**Why:** a single index with metadata filtering (`{source: "report.pdf"}`)
is enough while we're under ~1M vectors. Multi-tenant hard isolation is a
v2 concern — see `docs/ARCHITECTURE.md § What's deliberately not here`.
