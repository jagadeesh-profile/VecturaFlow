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
