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
