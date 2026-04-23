# Architectural Decision Records

> Append-only. Use the format below for new decisions.
> Legacy ADRs (ADR-001 through ADR-008) live in graphify/decisions.md — do not duplicate.

## ADR-0001 — Two-layer AI memory: graphify/ (knowledge) + .ai/ (session)
Date: 2026-04-22
Status: Accepted
Context: Three AI tools (Claude Code, Codex, Copilot) work this project. Cold-reading the
         codebase on every session wastes 14K-354K tokens depending on tool discipline.
         An existing custom graphify/ knowledge layer already maps the codebase.
Decision: Keep graphify/ as the knowledge layer (architecture, data flow, module cards,
          graph.json). Add .ai/ as the session layer (invariants, live context, changelog,
          handoff scripts). All tool instruction files enforce a 3-step read order:
          graphify/INDEX.md → .ai/CONTEXT.md → .ai/MEMORY.md.
Consequences:
  + ~77% token reduction per session (22K → 5K for a typical bug-fix session)
  + Lossless tool handoff via CONTEXT.md + switch-guard.sh
  + Pre-commit hook auto-updates graphify/ on structural code changes
  - Tools must log to CHANGELOG-AI.md and update CONTEXT.md at session end
  - graphify/ must be regenerated after module-level structural changes

## ADR-0002 — Safe production API-key table migration
Date: 2026-04-23
Status: Accepted
Context: The original production `vecturaflow-prod-keys` table used raw `api_key` as the
         partition key. Changing the partition key in place would force table replacement
         and risk deleting the only production API keys during Terraform apply.
Decision: Create `vecturaflow-prod-keys-v2` with `api_key_hash` as the partition key,
          migrate legacy keys by SHA-256 digest, point ECS at v2, verify v2-only auth,
          then delete the old raw-key table manually outside Terraform.
Consequences:
  + No raw API keys remain in the production key table.
  + Terraform can manage the v2 table without destroying the legacy table first.
  + Rollback to pre-hardening app revisions would require recreating compatible key data.
  - Operators must avoid reusing the old `vecturaflow-prod-keys` table name.

## ADR-0003 — Lambda image pushes must use Lambda-compatible single manifests
Date: 2026-04-23
Status: Accepted
Context: Docker BuildKit pushed the Lambda image as an OCI image index with an attestation
         manifest. AWS Lambda rejected that ECR tag with an unsupported image manifest
         error.
Decision: Build/push Lambda images with `docker buildx build --platform linux/amd64
          --provenance=false --sbom=false --push` and expose this as
          `make lambda-image-push`.
Consequences:
  + Lambda can create functions directly from the pushed ECR tag.
  + The build target documents the required production-safe flags.
  - SBOM/provenance attestations are disabled for Lambda images until the runtime supports
    that manifest shape.
