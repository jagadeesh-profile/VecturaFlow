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

## [2026-04-23T06:52:01.4623957-05:00] TOOL=codex
Task: Continue after review-fix commit
Files: .ai/CONTEXT.md; .ai/CHANGELOG-AI.md
Decision: Verified commit 71c32fb on main with the full Python 3.11 test suite before presenting integration/next-step options.
Next: User chooses whether to push, keep local, or start another hardening task.
Blockers: none

## [2026-04-23T07:16:59.2150328-05:00] TOOL=codex
Task: Implement strongly recommended production hardening
Files: api/config.py (L44), api/dependencies.py (L40-L113), infra/terraform/main.tf (L294-L650), infra/terraform/variables.tf (L88-L98), infra/terraform/backend.tf (L1-L3), infra/terraform/backend.example.hcl (L1-L5), tests/test_api.py (L57-L212), tests/test_infra_static.py (L57-L104), docs/*, graphify/*
Decision: Replaced raw API-key storage with SHA-256 DynamoDB keys, gated the local dev bypass behind API_DEV_BYPASS, required ACM-managed ALB TLS, added S3+DynamoDB backend config scaffolding, and added CloudWatch alarms for API/SQS health while leaving real key rotation/state migration/deploy as AWS account actions.
Next: Provision/migrate Terraform state backend, apply Terraform with a real ACM certificate ARN, rotate exposed API keys, and deploy/push updated artifacts.
Blockers: Actual AWS key rotation, Terraform state migration, and artifact deployment require account-side action.

## [2026-04-23T14:01:46.6957632Z] TOOL=codex
Task: Execute 7-step production hardening rollout
Files: Dockerfile.lambda; Makefile; requirements.lambda.txt; infra/terraform/main.tf; infra/terraform/lambdas.tf; tests/test_infra_static.py; docs/ARCHITECTURE.md; docs/OPERATIONS.md; graphify/architecture.md; graphify/glossary.md; graphify/graph.json; scripts/graphify.py; .ai/CONTEXT.md
Decision: Pushed `b8563a0`, migrated Terraform to remote state, created/migrated hashed key table v2, deleted the raw-key table after v2-only auth smoke passed, deployed ECS revision 2 and three ingestion Lambdas, fixed Lambda image/runtime deployment blockers, and left ACM managed-cert cutover pending external DNS delegation.
Next: Commit/push this rollout fix commit, then fix GoDaddy DNS delegation or copy the ACM CNAME there so the Amazon-issued certificate can validate and replace the imported ALB cert.
Blockers: `vecturaflow.chatslm.com` ACM cert remains `PENDING_VALIDATION` because public `chatslm.com` nameservers are GoDaddy, not the Route53 hosted zone containing the validation record.
