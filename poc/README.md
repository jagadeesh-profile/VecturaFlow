# VecturaFlow — POC

**Run this before starting the 10-day sprint.**  
Validates all 5 technical hypotheses locally. If all pass, you're ready to build.

---

## What It Validates

| Test | Hypothesis | Pass Criteria |
|---|---|---|
| POC-001 | S3 → Pinecone pipeline works end-to-end | Vectors confirmed in Pinecone after ingest |
| POC-002 | OpenAI embed quality on real data | Cosine similarity > 0.75 for related texts |
| POC-003 | FastAPI P95 latency < 3s under load | 20 concurrent workers × 3 requests each |
| POC-004 | LangGraph agent answers correctly | 3 test Q&A pairs with keyword validation |
| POC-005 | Complete E2E in < 90 seconds | Text → index → query → cited answer |

---

## Setup

```bash
# From project root
cp .env.example .env
# Fill in: OPENAI_API_KEY, PINECONE_API_KEY, PINECONE_REGION

pip install -r requirements.txt
```

---

## Run

```bash
# Run all tests (POC-001 requires real Pinecone)
python poc/poc_runner.py

# Skip POC-001 (no Pinecone needed)
python poc/poc_runner.py --skip-aws

# Run a single test
python poc/poc_runner.py --test poc002
python poc/poc_runner.py --test poc004
```

---

## Expected Output (All Pass)

```
════════════════════════════════════════════════════════════
  VecturaFlow — Proof of Concept Validation
════════════════════════════════════════════════════════════

──────────────────────────────────────────────────────────────
  POC001  S3 → Pinecone end-to-end pipeline
──────────────────────────────────────────────────────────────
  Parsed 8 blocks
  Chunked into 12 chunks
  Embedded 12 vectors (dim=1536)
  Confirmed 12 vectors in Pinecone
  ✓ PASSED  (18432ms)

──────────────────────────────────────────────────────────────
  POC002  OpenAI embedding quality on real data
──────────────────────────────────────────────────────────────
  [related]   similarity = 0.8821  (pair 1)
  [related]   similarity = 0.8634  (pair 2)
  [unrelated] similarity = 0.3201  (pair 3)
  ✓ PASSED  (1203ms)

──────────────────────────────────────────────────────────────
  POC003  FastAPI latency under concurrent load
──────────────────────────────────────────────────────────────
  Requests: 60 successful, 0 errors
  Latency  P50=24ms  P95=87ms  P99=142ms  Mean=31ms
  ✓ PASSED  (4821ms)

──────────────────────────────────────────────────────────────
  POC004  LangGraph RAG agent reasoning accuracy
──────────────────────────────────────────────────────────────
  Q1: What embedding model does VecturaFlow use?
  A:  VecturaFlow uses OpenAI text-embedding-3-small...
  Keywords found: ['text-embedding-3-small', '1536', 'OpenAI'] (3/2 required)
  Confidence: high  ✓
  ✓ PASSED  (6341ms)

──────────────────────────────────────────────────────────────
  POC005  Complete E2E: ingest → index → query → cited answer
──────────────────────────────────────────────────────────────
  Step 1 — Ingest + embed: 12 chunks (3421ms)
  Step 2 — Query + generate (2134ms)
  Query:  What makes VecturaFlow different from a basic RAG implementation?
  Answer: VecturaFlow distinguishes itself through its async SQS-based pipeline...
  Sources: 5 chunks cited
  Total E2E time: 5823ms  (threshold: 90,000ms)
  ✓ PASSED  (5903ms)

════════════════════════════════════════════════════════════
  VecturaFlow POC — Results Summary
════════════════════════════════════════════════════════════
  ✓ POC001  PASS  18432ms  S3 → Pinecone end-to-end pipeline
  ✓ POC002  PASS  1203ms   OpenAI embedding quality on real data
  ✓ POC003  PASS  4821ms   FastAPI latency under concurrent load
  ✓ POC004  PASS  6341ms   LangGraph RAG agent reasoning accuracy
  ✓ POC005  PASS  5903ms   Complete E2E: ingest → index → query → cited answer

  Result: 5/5 tests passed
  ✓ All hypotheses validated. Ready to start building.
════════════════════════════════════════════════════════════
```

---

## If Tests Fail

| Failure | Likely Cause | Fix |
|---|---|---|
| POC-001 fails | Pinecone key wrong or region mismatch | Check `.env` PINECONE_API_KEY + PINECONE_REGION |
| POC-002 similarity too low | Wrong embedding model | Confirm `text-embedding-3-small` in `.env` |
| POC-003 latency too high | Machine too slow or port conflict | Kill other processes, retry |
| POC-004 wrong answers | OpenAI API key invalid | Check OPENAI_API_KEY |
| POC-005 E2E timeout | Slow network to OpenAI | Run on faster connection |
