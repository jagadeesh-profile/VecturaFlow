# AI Change Log

> Append-only. Every tool logs here at session end. Never edit past entries.
> Format: ISO timestamp, TOOL= tag, Task, Files, Decision, Next, Blockers.

## [2026-04-22T00:00:00Z] TOOL=claude-code
Task: Initial .ai/ session layer setup (Approach B — preserve graphify/, add .ai/)
Files: .ai/* (all), .aiignore, CLAUDE.md, AGENTS.md,
       .github/copilot-instructions.md, scripts/pre-commit-hook.sh
Decision: Kept existing graphify/ custom knowledge layer unchanged. Added .ai/ as
          session/handoff layer. Two-layer architecture: knowledge + session.
Next: First real development task — user directs.
Blockers: none

## [2026-04-22T22:22:49.0811368-05:00] TOOL=codex
Task: Complete project review of VecturaFlow
Files: graphify/INDEX.md; .ai/CONTEXT.md; .ai/MEMORY.md; api/*; ingestion/*; embeddings/*; infra/terraform/*; Dockerfile; .github/workflows/*; pyproject.toml; pytest.ini
Decision: Performed review-only pass against project invariants, runtime paths, deployment IaC, CI/CD, packaging, and verification commands. No source fixes applied.
Next: Fix highest-severity deployment blockers: Lambda secrets env injection, SQS visibility timeouts, stale graphify, package backend, and deploy wheelhouse.
Blockers: Local test execution blocked by Python 3.13/incomplete deps; uv dependency attempt failed building numpy 1.26.4 on Python 3.13.

## [2026-04-22T22:47:52.3184394-05:00] TOOL=codex
Task: Fix project review findings
Files: api/schemas.py; api/retriever.py; api/agent.py; ingestion/lambda_parser.py; embeddings/lambda_embed.py; infra/terraform/main.tf; .github/workflows/deploy.yml; pyproject.toml; pytest.ini; scripts/verify_pinecone.py; tests/*; graphify/*
Decision: Fixed deployment blockers, citation metadata loss, parser partial-batch retries, embedder completion tracking, package/test config, GSI query usage, Terraform formatting, and graphify drift with regression coverage.
Next: Commit the completed fix set once git index permissions allow `git add`.
Blockers: `git add` previously failed with .git/index.lock permission denied; user rejected escalation during review session.
