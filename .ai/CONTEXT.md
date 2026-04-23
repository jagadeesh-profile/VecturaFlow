# Live Context (overwritten at every session end)

## Current Task
Continue after review-fix commit

## State
- Phase: complete, committed on main
- Files recently touched: api/schemas.py, api/retriever.py, api/agent.py,
  ingestion/lambda_parser.py, embeddings/lambda_embed.py, infra/terraform/main.tf,
  .github/workflows/deploy.yml, pyproject.toml, pytest.ini,
  scripts/verify_pinecone.py, tests/test_*.py, graphify/*
- Commit: 71c32fb fix: address project review findings
- Last command run: uv run --python 3.11 --with-requirements requirements.txt --with pytest-cov python -m pytest tests/ --tb=short
- Verification:
  - Fresh continue-session verification: `uv run --python 3.11 --with-requirements requirements.txt --with pytest-cov python -m pytest tests/ --tb=short` -> 115 passed, 82.66% coverage, 1 dependency warning
  - `python -m ruff check api ingestion embeddings` -> pass
  - `terraform fmt -check -recursive infra/terraform` -> pass
  - `terraform validate` from `infra/terraform` -> pass
  - `python scripts/graphify.py --dry-run` -> would change 0 files
  - `uv run --python 3.11 --with-requirements requirements.txt --with pytest-cov python -m pytest tests/ --tb=short` -> 115 passed, 82.99% coverage, 1 dependency warning
  - Invariant sweep: no `.scan(` or `dynamodb:Scan` remains outside graphify script text; Lambda secret ARN envs remain intentionally consumed by embeddings/lambda_embed.py via Secrets Manager resolution

## Mental Model
- Lambda embedder now resolves `OPENAI_API_KEY_ARN` / `PINECONE_API_KEY_ARN` through Secrets Manager when direct secret env vars are absent.
- Embedder tracks unique embedded chunk IDs in DynamoDB and marks `embedded` only when all chunks for a doc are seen.
- Retriever/RAG citations now preserve `page` and `section` through `RetrievedChunk` and `SourceCitation`.
- Parser failed records now appear in `batchItemFailures`, so SQS can retry/DLQ them.
- Deploy workflow now builds the Docker wheelhouse before `docker build`; Terraform SQS visibility timeouts are 360s.

## Next Action
User chooses whether to push main, keep local, or start the next hardening task.

## Do Not
- Do not edit .ai/MEMORY.md.
- Do not revert unrelated untracked docs under `docs/superpowers/`; they pre-existed this fix pass.
- Do not reintroduce DynamoDB scans or premature `embedded` registry updates.
