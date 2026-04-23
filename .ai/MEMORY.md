# Project Memory — Single Source of Truth

> READ by every AI tool at session start. WRITE-PROTECTED — only the human owner edits
> invariants. Tools APPEND to CHANGELOG-AI.md and DECISIONS.md only, never edit this file.

## Owner
Jagadeesh Pamidi · jagadeesh6187@gmail.com · AI Engineer

## Project Identity
- Name: VecturaFlow
- Purpose: Autonomous agentic RAG data platform on AWS (portfolio + interview showcase)
- Stage: Feature-complete. Production-hardening pass in progress.
- Repo: github.com/jagadeesh6187/VecturaFlow

## Stack (pinned — do not upgrade without updating deps.md)
- API: FastAPI 0.111.0 + Pydantic v2 (2.12.5) + uvicorn 0.29.0
- Agent: LangGraph 0.1.1 StateGraph + LangChain 0.2.1
- LLM: GPT-4o mini (generation, temperature=0) | text-embedding-3-small (1536-dim)
- Vector DB: Pinecone serverless 3.2.2 | us-east-1 | cosine metric
- Queue: AWS SQS FIFO between every pipeline stage
- Registry: DynamoDB on-demand + GSI(status, ingested_at)
- Cache: Redis 5.0.4 (5-min TTL, bypass on failure)
- Logging: structlog 24.1.0 (JSON in prod, console in dev)
- Testing: pytest 8.2.0 + moto 5.0.6 (mock all AWS)
- Deploy: ECS Fargate (pending) | Docker python:3.11-slim

See `.ai/deps.md` for exact versions and official doc links.

## Invariants (NEVER violate — these are non-negotiable)

1. **No os.environ direct access in api/.** Always `from api.config import settings`.
2. **No print() in production code.** Use `from api.logger import logger` (API) or
   `from ingestion.logging_util import get_logger` (Lambdas).
3. **Chunk per TextBlock, not per document.** Chunking full doc destroys page/section metadata.
   Each Chunk MUST carry block.page and block.section for source citations to work.
4. **Module-level boto3 clients.** Never instantiate _dynamo/_sqs/_s3 inside a handler — kills
   Lambda warm-start. Define at module level, outside handler function.
5. **Partial-batch SQS errors.** Every Lambda returns `{"batchItemFailures": [...]}`.
   Never raise an exception that fails the whole batch — catch per-record.
6. **Pydantic v2 syntax only.** `model_dump()` not `dict()`. `model_validate()` not
   `parse_obj()`. `min_length=` not `min_items=`.
7. **Tests mock AWS with moto.** `@mock_aws` decorator always. Never hit real AWS in tests.
   Set `OPENAI_API_KEY=sk-test` etc. BEFORE importing any app module.
8. **DynamoDB: never scan().** Always use GSI `status-ingested_at-index` with `query()`.
   Full table scan is O(n) and breaks at scale.
9. **Dev bypass API key is the literal string "dev"** when `API_ENV=development`.
   Never remove — required for `make dev` to work.
10. **Secrets via env vars only.** Never commit .env or real credentials. SSM Parameter Store
    for production secrets in ECS task definitions.
11. **Tests required for any logic change.** 107 tests, 82% coverage — do not reduce coverage.
12. **Zero TODO in merged code.** File a bug or do it now.

## Key Architectural Decisions (summary — full ADRs in graphify/decisions.md)
- SQS between every pipeline stage (async, resilient, independently scalable)
- OpenAI-compatible /v1/chat/completions API surface (drop-in replacement)
- LangGraph 4-node RAG: decompose → retrieve → generate → validate
- Pinecone serverless over pgvector (zero ops, free tier covers MVP)
- doc_id = SHA256(bucket/key) for S3 | uuid4() for webhooks

## Session Protocol (MANDATORY for every tool)

### Session start:
1. READ `graphify/INDEX.md` — architecture orientation (~1.4K tokens)
2. READ `.ai/CONTEXT.md` — pick up where last tool left off (~300 tokens)
3. READ this file — invariants (~2K tokens)
4. Do NOT scan source files. Query `graphify/modules/<file>.md` first.
5. To touch a specific file: read its module card first, then only the relevant line range.

### Before any code change:
- Check `graphify/modules/<module>-<file>.md` for the file's purpose, imports, key functions.
- Verify the change does not violate any invariant above.
- If touching an existing architectural decision, read `graphify/decisions.md` first.

### Session end (ALWAYS — non-negotiable):
1. Append to `.ai/CHANGELOG-AI.md`:
   ```
   ## [ISO-TIMESTAMP] TOOL=<claude-code|codex|copilot>
   Task: <one-line>
   Files: <path> (L<start>-<end>)
   Decision: <what you chose and why>
   Next: <the single next action>
   Blockers: <none | description>
   ```
2. Overwrite `.ai/CONTEXT.md` with current task state.
3. If code structure changed (new/renamed/deleted files): run `python scripts/graphify.py`
4. Commit: `git add .ai/ && git commit -m "<tool>: <task>"`

## Token Discipline (HARD RULES)
- Never cold-read source. `graphify/modules/` first, targeted reads second.
- Never read a file >200 lines in full. Get line range from module card, read only that span.
- Never re-run find/ls/git log. Use `.ai/tree.txt` / `hotspots.txt` / `recent-commits.txt`.
- One session = one task. New task = new session, update CONTEXT.md first.